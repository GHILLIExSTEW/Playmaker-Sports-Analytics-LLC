import asyncio
from datetime import date, datetime
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import discord
import pytest

import src.bot as bot_module
import src.reporting as module
from src.reporting import Reporting, TRACKER_TIMEZONE, TrackerMessageStore, build_official_tracker_embed


def cog():
    return Reporting(
        Mock(user=SimpleNamespace(id=999), is_ready=Mock(return_value=False)),
        features=Mock(), rankings=Mock(), get_tracker_start=Mock(return_value=None),
        settlement_channels=Mock(return_value=[("OFFICIAL_CHANNEL_ID", 101)]),
        reconcile_reactions=AsyncMock(return_value=0),
        tracked_operators=AsyncMock(return_value={42}),
        resolve_channel=AsyncMock(), can_manage_plays=Mock(), staff_alert=AsyncMock(),
        tracker_message_store=Mock(get_message_id=Mock(return_value=None), set_message_id=Mock()),
    )


def play(play_id=7, **overrides):
    return {
        "id": play_id, "user_id": 1, "message_id": str(play_id), "status": "win", "units": 2, "odds": 150,
        "created_at": "2026-10-01T12:00:00+00:00", "settled_at": "2026-10-02T12:00:00+00:00",
        **overrides,
    }


def test_tracker_message_store_reads_and_saves_discord_message_id(monkeypatch):
    select = Mock(return_value=SimpleNamespace(data=[{"message_id": "123456789012345678"}]))
    update = Mock(return_value=SimpleNamespace(data=[{"message_id": "123456789012345678"}]))
    monkeypatch.setattr(module.supabase_service, "select", select)
    monkeypatch.setattr(module.supabase_service, "update", update)
    store = TrackerMessageStore()

    assert store.get_message_id() == 123456789012345678
    store.set_message_id(123456789012345678)

    select.assert_called_once_with("tracker_message_settings", "message_id", {"singleton": True})
    update.assert_called_once_with(
        "tracker_message_settings",
        {"message_id": "123456789012345678"},
        {"singleton": True},
    )


def test_tracker_message_store_requires_applied_migration(monkeypatch):
    monkeypatch.setattr(module.supabase_service, "select", Mock(return_value=SimpleNamespace(data=[])))

    with pytest.raises(RuntimeError, match="tracker message migration"):
        TrackerMessageStore().get_message_id()


def test_tracker_fetch_paginates_both_tables_without_truncation(monkeypatch):
    client = Mock()
    queries = {"display_plays": Mock(), "users": Mock()}
    for name, query in queries.items():
        query.select.return_value = query
        query.range.return_value = query
        query.execute.side_effect = [
            SimpleNamespace(data=[{"id": index} for index in range(1000)]),
            SimpleNamespace(data=[{"id": 1000}]),
        ]
    client.table.side_effect = lambda name: queries[name]
    monkeypatch.setattr(module.supabase_service, "_ensure_client", Mock(return_value=client))
    plays, users = module.fetch_official_tracker_rows()
    assert len(plays) == len(users) == 1001
    for query in queries.values():
        assert [call.args for call in query.range.call_args_list] == [(0, 999), (1000, 1999)]
    assert queries["display_plays"].select.call_args.args[0].endswith(",stars")


def test_tracker_fetch_database_error_propagates(monkeypatch):
    monkeypatch.setattr(module.supabase_service, "_ensure_client", Mock(side_effect=RuntimeError("offline")))
    with pytest.raises(RuntimeError, match="offline"):
        module.fetch_official_tracker_rows()


def test_current_cutoff_is_read_on_each_build():
    reporting = cog()
    reporting.get_tracker_start.side_effect = [date(2026, 10, 3), None]
    users = [{"id": 1, "display_name": "Ace"}]
    assert reporting.tracker_embed([play()], users)[0].fields[1].value == "+0u"
    assert reporting.tracker_embed([play()], users)[0].fields[1].value == "+3u"
    assert reporting.get_tracker_start.call_count == 2


def test_partial_void_odds_and_future_results_keep_existing_tally_rules():
    rows = [
        play(1), play(2, status="loss", units=1),
        play(3, status="partial"), play(4, status="void"),
        play(5, settled_at="2026-10-20T12:00:00+00:00"),
        play(6, status="regraded", settled_at=None),
        play(7, message_id=None), play(8, message_id="1"),
    ]
    embed, top, breakdown = build_official_tracker_embed(
        rows, [{"id": 1, "display_name": "Ace"}], datetime(2026, 10, 9, 12, tzinfo=TRACKER_TIMEZONE),
    )
    assert embed.fields[0].value == "1 bet"
    assert embed.fields[1].value == "+3.5u"
    assert embed.fields[2].value == "+3.5u"
    metrics = embed.fields[3].value
    assert "**Record** — 1-1-1 · 1 partial" in metrics
    assert "**ROI** — +70.0% (+3.50u net / 5.00u risked)" in metrics
    assert "**Performance** — +3.50u net · 50.0% win rate · 4 settled" in metrics
    assert "**Star Rating** — No rated plays yet" in metrics
    assert "1-1" in breakdown and "+2.00u" in breakdown
    assert "50% win rate" in top[0]


def test_tracker_metrics_break_out_official_star_ratings():
    rows = [
        play(1, stars=3),
        play(2, stars=3, status="loss", units=1),
        play(3, stars=4, odds=-110),
        play(4, stars=5, status="void"),
        play(5, tracking_scope="capper", stars=None),
    ]
    embed, _, _ = build_official_tracker_embed(
        rows, [{"id": 1, "display_name": "Ace"}], datetime(2026, 10, 9, 12, tzinfo=TRACKER_TIMEZONE),
    )
    metrics = embed.fields[3].value
    assert "3⭐ 1-1-0 · +2.00u" in metrics
    assert "4⭐ 1-0-0 · +1.82u" in metrics
    assert "5⭐ 0-0-1 · +0.00u" in metrics


def test_capper_tracker_reports_star_rating_as_not_applicable(monkeypatch):
    monkeypatch.setattr(module, "GUILD_ID", 123)
    reporting = cog()
    reporting.tracker_rows = Mock(return_value=(
        [play(tracking_scope="capper", stars=None)],
        [{"id": 1, "discord_user_id": "42"}],
    ))
    request = SimpleNamespace(
        guild=SimpleNamespace(id=123), user=SimpleNamespace(id=99),
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    asyncio.run(reporting.capper_tracker_command.callback(reporting, request))
    embed = request.followup.send.call_args.kwargs["embed"]
    assert "**Star Rating** — Not rated for individual capper plays" in embed.fields[3].value
    assert all("Monthly Capper Breakdown" not in field.name for field in embed.fields)


@pytest.mark.parametrize("reconciled", [0, 2])
def test_refresh_reconciles_all_scopes_but_posts_only_official_summary(monkeypatch, reconciled):
    monkeypatch.setattr(module, "RESULT_CHANNEL_ID", 303)
    monkeypatch.setattr(module, "TEAM_STATS_CHANNEL_ID", 404)
    reporting = cog()
    rows, users = [play(tracking_scope="official"), play(8, user_id=2, tracking_scope="capper")], [
        {"id": 1, "discord_user_id": "42"}, {"id": 2, "discord_user_id": "43"},
    ]
    refreshed = [play(status="loss", tracking_scope="official"), rows[1]]
    reporting.tracker_rows = Mock(side_effect=[(rows, users), (refreshed, users)])
    reporting.reconcile_reactions.return_value = reconciled
    reporting.settlement_channels.return_value = [
        ("OFFICIAL_CHANNEL_ID", 101), ("OFFICIAL_CHANNEL_ID", 101), ("TEST_CHANNEL_ID", 202),
    ]
    channels = {101: object(), 303: object(), 404: object()}
    reporting.resolve_channel.side_effect = lambda channel_id, *args, **kwargs: channels.get(channel_id)
    reporting.tracker_embed = Mock(return_value=(discord.Embed(description="Summary"), ["Ace +3u"], "Ace 1-0"))
    image = BytesIO(b"png")
    render = Mock(return_value=image)
    monkeypatch.setattr(module, "render_tracker_image", render)
    reporting.update_or_post_tracker_embed = AsyncMock()
    assert asyncio.run(reporting.refresh_tracker()) == reconciled
    reporting.reconcile_reactions.assert_awaited_once_with(rows, users, [channels[101]])
    assert reporting.tracker_rows.call_count == (2 if reconciled else 1)
    reporting.tracker_embed.assert_called_once_with([refreshed[0] if reconciled else rows[0]], users)
    assert render.call_args.args[1] == []
    sends = reporting.update_or_post_tracker_embed.await_args_list
    assert sends[0].args[0] is channels[303] and sends[0].args[2] is image
    assert len(sends) == 1
    reporting.tracked_operators.assert_not_awaited()


def test_refresh_without_results_channel_stops_before_fetch(monkeypatch):
    monkeypatch.setattr(module, "RESULT_CHANNEL_ID", None)
    reporting = cog()
    reporting.tracker_rows = Mock()
    with pytest.raises(RuntimeError, match="not configured"):
        asyncio.run(reporting.refresh_tracker())
    reporting.tracker_rows.assert_not_called()


def test_reconciliation_failure_never_publishes_stale_tracker(monkeypatch):
    monkeypatch.setattr(module, "RESULT_CHANNEL_ID", 303)
    reporting = cog()
    reporting.tracker_rows = Mock(return_value=([], []))
    reporting.reconcile_reactions.side_effect = RuntimeError("offline")
    reporting.update_or_post_tracker_embed = AsyncMock()
    with pytest.raises(RuntimeError, match="offline"):
        asyncio.run(reporting.refresh_tracker())
    reporting.update_or_post_tracker_embed.assert_not_awaited()


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("with_image", [False, True])
def test_tracker_publication_matches_bot_and_title_and_closes_file(monkeypatch, existing, with_image):
    reporting = cog()
    real_file = discord.File
    closed = Mock()
    def create(*args, **kwargs):
        file = real_file(*args, **kwargs)
        close = file.close
        def close_file():
            closed()
            close()
        return SimpleNamespace(filename=file.filename, close=close_file)
    monkeypatch.setattr(module.discord, "File", create)
    ours = SimpleNamespace(
        id=499, author=SimpleNamespace(id=reporting.bot.user.id),
        embeds=[discord.Embed(title="Summary")], edit=AsyncMock(),
    )
    stranger = SimpleNamespace(author=object(), embeds=[discord.Embed(title="Summary")], edit=AsyncMock())
    other = SimpleNamespace(author=reporting.bot.user, embeds=[discord.Embed(title="Other")], edit=AsyncMock())

    async def history(limit, oldest_first):
        assert limit is None and oldest_first is True
        for message in [stranger, other] + ([ours] if existing else []):
            yield message
    channel = SimpleNamespace(history=history, send=AsyncMock())
    image = BytesIO(b"png") if with_image else None
    asyncio.run(reporting.update_or_post_tracker_embed(channel, discord.Embed(title="Summary"), image))
    stranger.edit.assert_not_awaited()
    other.edit.assert_not_awaited()
    action = ours.edit if existing else channel.send
    action.assert_awaited_once()
    reporting.tracker_message_store.set_message_id.assert_called_once_with(499 if existing else channel.send.return_value.id)
    if existing:
        channel.send.assert_not_awaited()
    if with_image:
        file = action.call_args.kwargs["attachments"][0] if existing else action.call_args.kwargs["file"]
        assert file.filename == "unit-summary.png"
        closed.assert_called_once()
        assert action.call_args.kwargs["embed"].image.url == "attachment://unit-summary.png"
    else:
        assert "file" not in action.call_args.kwargs and "attachments" not in action.call_args.kwargs


@pytest.mark.parametrize("image_only", [False, True])
def test_tracker_refresh_reuses_summary_beyond_recent_history(image_only):
    reporting = cog()
    summary = SimpleNamespace(
        author=SimpleNamespace(id=reporting.bot.user.id),
        id=500,
        embeds=[discord.Embed() if image_only else discord.Embed(title="Summary")],
        attachments=[SimpleNamespace(filename="unit-summary.png")] if image_only else [],
        edit=AsyncMock(),
    )
    newer = [
        SimpleNamespace(author=SimpleNamespace(id=reporting.bot.user.id), embeds=[discord.Embed(title="Recap")], attachments=[])
        for _ in range(75)
    ]
    newer_tracker = SimpleNamespace(
        id=501,
        author=SimpleNamespace(id=reporting.bot.user.id),
        embeds=[discord.Embed()],
        attachments=[SimpleNamespace(filename="unit-summary.png")],
        edit=AsyncMock(),
    )

    async def history(limit, oldest_first):
        assert limit is None and oldest_first is True
        messages = [summary] + newer + [newer_tracker]
        for message in messages:
            yield message

    channel = SimpleNamespace(history=history, send=AsyncMock())

    async def check():
        for _ in range(2):
            await reporting.update_or_post_tracker_embed(
                channel, discord.Embed(title="Summary"), BytesIO(b"png"),
            )

    asyncio.run(check())
    assert summary.edit.await_count == 2
    assert summary.edit.call_args.kwargs["embed"].image.url == "attachment://unit-summary.png"
    reporting.tracker_message_store.set_message_id.assert_has_calls([call(500), call(500)])
    newer_tracker.edit.assert_not_awaited()
    channel.send.assert_not_awaited()


def test_overlapping_tracker_refreshes_reuse_newly_published_message():
    reporting = cog()
    published = None
    stored_id = None
    message = SimpleNamespace(
        id=700,
        author=SimpleNamespace(id=reporting.bot.user.id),
        embeds=[],
        attachments=[],
        edit=AsyncMock(),
    )

    async def history(limit, oldest_first):
        assert limit is None and oldest_first is True
        if published is not None:
            yield published

    async def send(**kwargs):
        nonlocal published
        published = message
        return message

    def get_message_id():
        return stored_id

    def set_message_id(message_id):
        nonlocal stored_id
        stored_id = message_id

    reporting.tracker_message_store.get_message_id.side_effect = get_message_id
    reporting.tracker_message_store.set_message_id.side_effect = set_message_id

    async def fetch_message(message_id):
        assert message_id == message.id
        return message

    channel = SimpleNamespace(
        history=history,
        fetch_message=AsyncMock(side_effect=fetch_message),
        send=AsyncMock(side_effect=send),
    )

    async def check():
        await asyncio.gather(
            reporting.update_or_post_tracker_embed(channel, discord.Embed(title="Summary")),
            reporting.update_or_post_tracker_embed(channel, discord.Embed(title="Summary")),
        )

    asyncio.run(check())
    channel.send.assert_awaited_once()
    message.edit.assert_awaited_once()
    channel.fetch_message.assert_awaited_once_with(message.id)
    reporting.tracker_message_store.set_message_id.assert_has_calls([call(message.id), call(message.id)])


def test_tracker_refresh_uses_persisted_message_id_without_history_search():
    reporting = cog()
    reporting.tracker_message_store.get_message_id.return_value = 600
    message = SimpleNamespace(id=600, edit=AsyncMock())
    channel = SimpleNamespace(
        fetch_message=AsyncMock(return_value=message),
        history=AsyncMock(),
        send=AsyncMock(),
    )

    asyncio.run(reporting.update_or_post_tracker_embed(channel, discord.Embed(title="Summary")))

    channel.fetch_message.assert_awaited_once_with(600)
    channel.history.assert_not_awaited()
    channel.send.assert_not_awaited()
    message.edit.assert_awaited_once()
    reporting.tracker_message_store.set_message_id.assert_called_once_with(600)


def test_tracker_refresh_discovers_and_persists_message_when_saved_id_is_stale():
    reporting = cog()
    reporting.tracker_message_store.get_message_id.return_value = 600
    response = SimpleNamespace(status=404, reason="Not Found")
    stale_message = discord.NotFound(response, "Message not found")
    message = SimpleNamespace(
        id=601,
        author=SimpleNamespace(id=reporting.bot.user.id),
        embeds=[discord.Embed(title="Summary")],
        attachments=[],
        edit=AsyncMock(),
    )

    async def history(limit, oldest_first):
        assert limit is None and oldest_first is True
        yield message

    channel = SimpleNamespace(
        fetch_message=AsyncMock(side_effect=stale_message),
        history=history,
        send=AsyncMock(),
    )

    asyncio.run(reporting.update_or_post_tracker_embed(channel, discord.Embed(title="Summary")))

    message.edit.assert_awaited_once()
    channel.send.assert_not_awaited()
    reporting.tracker_message_store.set_message_id.assert_has_calls([call(None), call(601)])


@pytest.mark.parametrize("failure", ["history", "edit"])
def test_tracker_lookup_or_edit_error_does_not_post_replacement(failure):
    reporting = cog()
    summary = SimpleNamespace(
        id=500, author=SimpleNamespace(id=reporting.bot.user.id), embeds=[discord.Embed(title="Summary")],
        edit=AsyncMock(side_effect=RuntimeError("offline")),
    )

    async def history(limit, oldest_first):
        if failure == "history":
            raise RuntimeError("offline")
        yield summary

    channel = SimpleNamespace(history=history, send=AsyncMock())
    with pytest.raises(RuntimeError, match="offline"):
        asyncio.run(reporting.update_or_post_tracker_embed(channel, discord.Embed(title="Summary")))
    channel.send.assert_not_awaited()


def test_tracker_send_error_propagates_and_releases_file(monkeypatch):
    reporting = cog()
    async def history(limit, oldest_first):
        if False:
            yield
    channel = SimpleNamespace(history=history, send=AsyncMock(side_effect=RuntimeError("offline")))
    real_file = discord.File
    created = []
    closed = Mock()
    def create(*args, **kwargs):
        file = real_file(*args, **kwargs)
        close = file.close
        def close_file():
            closed()
            close()
        wrapper = SimpleNamespace(filename=file.filename, close=close_file)
        created.append(wrapper)
        return wrapper
    monkeypatch.setattr(module.discord, "File", create)
    image = BytesIO(b"png")
    with pytest.raises(RuntimeError, match="offline"):
        asyncio.run(reporting.update_or_post_tracker_embed(channel, discord.Embed(title="Summary"), image))
    assert len(created) == 1
    closed.assert_called_once()


def test_bot_tracker_bridges_delegate_and_missing_owner_is_explicit(monkeypatch):
    reporting = cog()
    reporting.refresh_tracker = AsyncMock(return_value=2)
    reporting.update_or_post_tracker_embed = AsyncMock()
    monkeypatch.setattr(bot_module.bot, "get_cog", Mock(return_value=reporting))
    assert asyncio.run(bot_module.refresh_tracker_embeds()) == 2
    embed, channel = discord.Embed(title="Summary"), object()
    asyncio.run(bot_module.update_or_post_tracker_embed(channel, embed))
    reporting.update_or_post_tracker_embed.assert_awaited_once_with(channel, embed, None)
    monkeypatch.setattr(bot_module.bot, "get_cog", Mock(return_value=None))
    with pytest.raises(RuntimeError, match="not loaded"):
        asyncio.run(bot_module.refresh_tracker_embeds())
