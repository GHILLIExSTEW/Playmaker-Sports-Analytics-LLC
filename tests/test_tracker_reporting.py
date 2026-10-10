import asyncio
from datetime import date, datetime
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

import src.bot as bot_module
import src.reporting as module
from src.reporting import Reporting, TRACKER_TIMEZONE, build_official_tracker_embed


def cog():
    return Reporting(
        Mock(user=SimpleNamespace(id=999), is_ready=Mock(return_value=False)),
        features=Mock(), rankings=Mock(), get_tracker_start=Mock(return_value=None),
        settlement_channels=Mock(return_value=[("OFFICIAL_CHANNEL_ID", 101)]),
        reconcile_reactions=AsyncMock(return_value=0),
        tracked_operators=AsyncMock(return_value={42}),
        resolve_channel=AsyncMock(), can_manage_plays=Mock(), staff_alert=AsyncMock(),
    )


def play(play_id=7, **overrides):
    return {
        "id": play_id, "user_id": 1, "message_id": str(play_id), "status": "win", "units": 2, "odds": 150,
        "created_at": "2026-10-01T12:00:00+00:00", "settled_at": "2026-10-02T12:00:00+00:00",
        **overrides,
    }


def test_tracker_fetch_paginates_both_tables_without_truncation(monkeypatch):
    client = Mock()
    queries = {"plays": Mock(), "users": Mock()}
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
    assert "1-1" in breakdown and "+2.00u" in breakdown
    assert "50% win rate" in top[0]


@pytest.mark.parametrize("reconciled", [0, 2])
def test_refresh_reloads_after_reconciliation_filters_operators_and_posts_both(monkeypatch, reconciled):
    monkeypatch.setattr(module, "RESULT_CHANNEL_ID", 303)
    monkeypatch.setattr(module, "TEAM_STATS_CHANNEL_ID", 404)
    reporting = cog()
    rows, users = [play(), play(8, user_id=2)], [
        {"id": 1, "discord_user_id": "42"}, {"id": 2, "discord_user_id": "43"},
    ]
    refreshed = [play(status="loss"), rows[1]]
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
    assert render.call_args.args[1][-1] == ("🏆 Monthly Playmaker Breakdown", "Ace 1-0")
    sends = reporting.update_or_post_tracker_embed.await_args_list
    assert sends[0].args[0] is channels[303] and sends[0].args[2] is image
    assert sends[1].args[0] is channels[404] and sends[1].args[1].description == "Ace +3u"


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
    ours = SimpleNamespace(author=reporting.bot.user, embeds=[discord.Embed(title="Summary")], edit=AsyncMock())
    stranger = SimpleNamespace(author=object(), embeds=[discord.Embed(title="Summary")], edit=AsyncMock())
    other = SimpleNamespace(author=reporting.bot.user, embeds=[discord.Embed(title="Other")], edit=AsyncMock())

    async def history(limit):
        assert limit == 50
        for message in [stranger, other] + ([ours] if existing else []):
            yield message
    channel = SimpleNamespace(history=history, send=AsyncMock())
    image = BytesIO(b"png") if with_image else None
    asyncio.run(reporting.update_or_post_tracker_embed(channel, discord.Embed(title="Summary"), image))
    stranger.edit.assert_not_awaited()
    other.edit.assert_not_awaited()
    action = ours.edit if existing else channel.send
    action.assert_awaited_once()
    if existing:
        channel.send.assert_not_awaited()
    if with_image:
        file = action.call_args.kwargs["attachments"][0] if existing else action.call_args.kwargs["file"]
        assert file.filename == "unit-summary.png"
        closed.assert_called_once()
        assert action.call_args.kwargs["embed"].image.url == "attachment://unit-summary.png"
    else:
        assert "file" not in action.call_args.kwargs and "attachments" not in action.call_args.kwargs


def test_tracker_send_error_propagates_and_releases_file(monkeypatch):
    reporting = cog()
    async def history(limit):
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
