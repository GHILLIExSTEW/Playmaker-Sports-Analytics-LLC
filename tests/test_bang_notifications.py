import asyncio
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

import src.bang_notifications as module
import src.bot as bot_module
from src.bang_notifications import BangNotifications
from src.services import bang_service

pytestmark = pytest.mark.usefixtures("mock_play_cards")

@pytest.fixture
def env(monkeypatch):
    for name, value in {
        "GUILD_ID": 123, "OFFICIAL_CHANNEL_ID": 111, "WINNING_SLIPS_CHANNEL_ID": 101,
        "TEST_CHANNEL_ID": 303, "HIGHROLLER_ROLE_ID": 789,
    }.items():
        monkeypatch.setattr(module, name, value)
    role = SimpleNamespace(mentionable=True, is_default=lambda: False)
    guild = SimpleNamespace(id=123, me=object(), icon=Mock(), get_role=Mock(return_value=role))
    permissions = SimpleNamespace(**dict.fromkeys(
        ("view_channel", "read_message_history", "send_messages", "embed_links", "attach_files", "mention_everyone"), True,
    ))
    channels, messages = {}, {}
    author = SimpleNamespace(id=999, display_name="Playmaker Picks", display_avatar=SimpleNamespace(url="https://cdn.example/bot.png"))
    for channel_id in (101, 111, 202, 303):
        channel = SimpleNamespace(id=channel_id, guild=guild, permissions_for=Mock(return_value=permissions))

        async def send(channel_id=channel_id, **kwargs):
            message = SimpleNamespace(id=900 + channel_id, author=author, embeds=[kwargs["embed"]])

            async def edit(**changes):
                message.embeds = [changes["embed"]]

            message.edit = AsyncMock(side_effect=edit)
            messages[(channel_id, message.id)] = message
            return message

        async def fetch(message_id, channel_id=channel_id):
            return messages[(channel_id, message_id)]

        async def history(limit, channel_id=channel_id):
            for (stored_channel_id, _), message in list(messages.items())[:limit]:
                if stored_channel_id == channel_id:
                    yield message

        channel.send, channel.fetch_message = AsyncMock(side_effect=send), AsyncMock(side_effect=fetch)
        channel.history = history
        channels[channel_id] = channel
    source_embed = discord.Embed()
    source_embed.set_author(name="Personal Capper")
    attachment = SimpleNamespace(
        content_type="image/png",
        to_file=AsyncMock(side_effect=lambda: discord.File(BytesIO(b"slip"), filename="slip.png")),
    )
    source = SimpleNamespace(
        id=500, guild=guild, attachments=[attachment], embeds=[source_embed],
        jump_url="https://discord.com/channels/123/111/500",
    )
    messages[(111, 500)] = source
    messages[(303, 500)] = source
    play = {
        "id": 7, "message_id": "500", "status": "win", "tracking_scope": "official",
        "units": 2, "odds": 109, "play_text": "Three-way parlay", "team_name": "Wolves",
        "created_at": "2026-10-16T23:00:00Z",
    }
    records = {}

    def claim(play_id, channel_id, is_test):
        if channel_id in records:
            return None
        records[channel_id] = {
            "play_id": play_id, "channel_id": str(channel_id), "attempt_id": "attempt-1",
            "is_test": is_test, "state": "claimed", "message_id": None, "displayed_status": None,
            "updated_at": "2026-10-16T23:00:00Z", "last_error": None,
        }
        return "attempt-1"

    def complete(play_id, channel_id, attempt_id, message_id, status):
        records[channel_id].update(state="delivered", message_id=str(message_id), displayed_status=status)

    def fail(play_id, channel_id, attempt_id, error, uncertain):
        if records[channel_id]["state"] == "claimed":
            records[channel_id].update(state="uncertain" if uncertain else "failed", last_error=error)

    def recover(play_id, channel_id, attempt_id, actor_id, message_id):
        row = records[channel_id]
        if row["message_id"]:
            raise RuntimeError("A delivered message is already tracked.")
        row.update(attempt_id="attempt-2", message_id=str(message_id) if message_id else None,
                   state="delivered" if message_id else "claimed", displayed_status=None)
        return "attempt-2"

    for name, function in {
        "claim": claim, "complete": complete, "fail": fail, "recover": recover,
        "deliveries": lambda play_id: list(records.values()), "stale": lambda is_test, test_channel_id: [],
        "pending": lambda: [], "finish_event": lambda play_id, revision: None,
        "recent_plays": lambda: [dict(play)],
    }.items():
        monkeypatch.setattr(bang_service, name, Mock(side_effect=function))
    bot = Mock(user=author, get_guild=Mock(return_value=guild))
    official = Mock(_fetch_play=Mock(side_effect=lambda play_id: dict(play)))
    resolve = AsyncMock(side_effect=lambda channel_id, *args, **kwargs: channels[channel_id])
    cog = BangNotifications(bot, official=official, get_testing=Mock(return_value=False),
                            resolve_channel=resolve, staff_alert=AsyncMock())
    return SimpleNamespace(
        cog=cog, play=play, source=source, guild=guild, channels=channels, permissions=permissions,
        records=records, messages=messages, role=role, author=author,
    )


def click(env, manager=True, guild=None):
    return SimpleNamespace(
        guild=env.guild if guild is None else guild, guild_id=123,
        user=SimpleNamespace(id=42, bot=False, roles=[], guild_permissions=SimpleNamespace(manage_guild=manager)),
        response=SimpleNamespace(defer=AsyncMock(), edit_message=AsyncMock(), send_message=AsyncMock()),
        message=SimpleNamespace(edit=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


def test_recent_plays_query_is_official_published_newest_first_and_limited(monkeypatch):
    query = Mock()
    for method in ("select", "eq", "order", "limit"):
        getattr(query, method).return_value = query
    query.not_ = SimpleNamespace(is_=Mock(return_value=query))
    plays = [{"id": play_id} for play_id in range(10, 0, -1)]
    query.execute.return_value = SimpleNamespace(data=plays)
    client = Mock()
    client.table.return_value = query
    monkeypatch.setattr(bang_service.supabase_service, "_ensure_client", lambda: client)

    assert bang_service.recent_plays() == plays

    client.table.assert_called_once_with("plays")
    query.select.assert_called_once_with(
        "id,created_at,team_name,play_text,status,units,odds,message_id,tracking_scope",
    )
    query.eq.assert_called_once_with("tracking_scope", "official")
    query.not_.is_.assert_called_once_with("message_id", "null")
    assert query.order.call_args_list == [
        (("created_at",), {"desc": True}),
        (("id",), {"desc": True}),
    ]
    query.limit.assert_called_once_with(10)


def test_bang_branding_image_profit_and_highroller_only_once(env):
    asyncio.run(env.cog.send(env.source, 7))
    sent = env.channels[101].send.call_args.kwargs
    assert sent["content"] == "## 💰 BANG! 💸 🤑\n<@&789>"
    assert sent["allowed_mentions"].to_dict() == {"roles": [789], "parse": []}
    assert sent["allowed_mentions"].replied_user is False
    assert sent["embed"].author.name == "Playmaker Picks"
    assert sent["embed"].author.icon_url == env.author.display_avatar.url
    assert "Official play WIN" in sent["embed"].footer.text
    assert sent["embed"].description == f"[Official Playmaker Picks]({env.source.jump_url})"
    assert sent["embed"].fields == []
    assert sent["embed"].image.url == "attachment://playmaker-card-7.png"
    assert sent["file"].filename == "playmaker-card-7.png"
    bang_service.complete.assert_called_once_with(7, 101, "attempt-1", 1001, "win")
    asyncio.run(env.cog.send(env.source, 7))
    env.channels[101].send.assert_awaited_once()
    env.channels[202].send.assert_not_awaited()
    env.cog.staff_alert.assert_not_awaited()


@pytest.mark.parametrize("odds,profit", [(-200, "+1u"), (150, "+3u"), (109, "+2.18u")])
def test_profit_uses_repository_settlement_math(env, odds, profit, mock_play_cards):
    env.play["odds"] = odds
    asyncio.run(env.cog.send(env.source, 7))
    card_play = mock_play_cards.call_args.args[0]
    assert module.SettlementService.tally_for_result(card_play["status"], card_play["units"], card_play["odds"]) == float(profit.removesuffix("u"))


@pytest.mark.parametrize("scope,status", [("capper", "win"), ("test", "win"), ("official", "open"), ("official", "loss")])
def test_non_official_or_non_winning_play_does_not_create_bang(env, scope, status):
    env.play.update(tracking_scope=scope, status=status)
    asyncio.run(env.cog.send(env.source, 7))
    bang_service.claim.assert_not_called()
    env.channels[101].send.assert_not_awaited()


def test_embed_only_image_and_missing_image_are_passed_to_card_renderer(env, mock_play_cards):
    env.source.attachments = []
    env.source.embeds[0].set_image(url="https://cdn.example/slip.png")
    asyncio.run(env.cog.send(env.source, 7))
    assert mock_play_cards.call_args.kwargs["source"] is env.source
    assert env.channels[101].send.call_args.kwargs["embed"].image.url == "attachment://playmaker-card-7.png"
    env.cog.staff_alert.assert_not_awaited()
    env.records.clear()
    env.source.embeds = []
    asyncio.run(env.cog.send(env.source, 7))
    assert mock_play_cards.call_args.kwargs["source"].embeds == []
    env.cog.staff_alert.assert_not_awaited()


@pytest.mark.parametrize("status", ["open", "regraded", "loss", "void", "partial"])
def test_corrections_update_existing_message_without_ping_and_rewin_does_not_duplicate(env, status):
    asyncio.run(env.cog.send(env.source, 7))
    message = env.messages[(101, 1001)]
    env.play["status"] = status
    asyncio.run(env.cog.send(None, 7))
    updated = message.edit.call_args.kwargs
    assert "BANG CORRECTION" in updated["content"]
    assert status.upper() in updated["embed"].title
    assert "no longer valid" in updated["embed"].description
    assert updated["embed"].image.url == "attachment://playmaker-card-7.png"
    assert updated["allowed_mentions"].to_dict() == {"parse": []}
    assert env.records[101]["displayed_status"] == status
    env.play["status"] = "win"
    asyncio.run(env.cog.send(None, 7))
    assert "CORRECTION" not in message.edit.call_args.kwargs["content"]
    assert env.records[101]["displayed_status"] == "win"
    env.channels[101].send.assert_awaited_once()


def test_previously_misclassified_capper_bang_is_corrected(env):
    asyncio.run(env.cog.send(env.source, 7))
    env.play["tracking_scope"] = "capper"
    asyncio.run(env.cog.send(None, 7))
    assert "NOT AN OFFICIAL PLAY" in env.messages[(101, 1001)].edit.call_args.kwargs["embed"].title
    env.channels[101].send.assert_awaited_once()


def test_failed_correction_alerts_and_does_not_mark_updated_or_republish(env):
    asyncio.run(env.cog.send(env.source, 7))
    env.messages[(101, 1001)].edit.side_effect = RuntimeError("Discord unavailable")
    env.play["status"] = "loss"
    asyncio.run(env.cog.send(None, 7))
    assert env.records[101]["displayed_status"] == "win"
    env.channels[101].send.assert_awaited_once()
    env.cog.staff_alert.assert_awaited_once()


@pytest.mark.parametrize("failure", ["send", "complete"])
def test_uncertain_delivery_is_never_automatically_retried(env, failure):
    if failure == "send":
        env.channels[101].send.side_effect = RuntimeError("Connection lost")
    else:
        bang_service.complete.side_effect = RuntimeError("Database unavailable")
    asyncio.run(env.cog.send(env.source, 7))
    assert env.records[101]["state"] == "uncertain"
    asyncio.run(env.cog.send(env.source, 7))
    env.channels[101].send.assert_awaited_once()
    assert env.cog.staff_alert.await_count == 2


def test_definitive_discord_rejection_is_recorded_as_failed(env):
    env.channels[101].send.side_effect = discord.Forbidden(Mock(status=403, reason="Forbidden"), "no access")
    asyncio.run(env.cog.send(env.source, 7))
    assert env.records[101]["state"] == "failed"
    env.channels[202].send.assert_not_awaited()


def test_testing_never_pings_or_touches_live_delivery(env):
    asyncio.run(env.cog.send(env.source, 7))
    env.cog.get_testing.return_value = True
    env.play["status"] = "loss"
    asyncio.run(env.cog.send(None, 7))
    env.messages[(101, 1001)].edit.assert_not_awaited()
    env.play["status"] = "win"
    asyncio.run(env.cog.send(env.source, 7))
    kwargs = env.channels[303].send.call_args.kwargs
    assert kwargs["allowed_mentions"].to_dict() == {"parse": []}
    assert kwargs["allowed_mentions"].replied_user is False
    assert "<@&" not in kwargs["content"]
    assert env.records[303]["is_test"] is True
    env.channels[101].send.assert_awaited_once()


@pytest.mark.parametrize("guild", [None, SimpleNamespace(id=456)])
def test_wrong_guild_does_not_claim(env, guild):
    env.source.guild = guild
    asyncio.run(env.cog.send(env.source, 7))
    env.cog.official._fetch_play.assert_not_called()
    bang_service.claim.assert_not_called()


@pytest.mark.parametrize("permission", ["view_channel", "send_messages", "read_message_history", "embed_links", "attach_files"])
def test_missing_permission_is_explicit_and_does_not_claim(env, permission):
    setattr(env.permissions, permission, False)
    asyncio.run(env.cog.send(env.source, 7))
    bang_service.claim.assert_not_called()
    assert permission in env.cog.staff_alert.call_args.args[1]


@pytest.mark.parametrize("role_missing", [True, False])
def test_invalid_role_configuration_prevents_unknown_or_unpingable_role(env, role_missing):
    if role_missing:
        env.guild.get_role.return_value = None
    else:
        env.role.mentionable = False
        env.permissions.mention_everyone = False
    asyncio.run(env.cog.send(env.source, 7))
    bang_service.claim.assert_not_called()
    assert "HIGHROLLER" in env.cog.staff_alert.call_args.args[1]


def test_startup_checks_source_destination_role_and_database(env):
    asyncio.run(env.cog.validate_configuration())
    assert [call.args[1] for call in env.cog.resolve_channel.call_args_list] == ["OFFICIAL_CHANNEL_ID", "WINNING_SLIPS_CHANNEL_ID"]
    bang_service.stale.assert_called_once_with(False, 303)
    env.cog.staff_alert.assert_not_awaited()


def test_startup_database_migration_failure_alerts(env):
    bang_service.stale.side_effect = RuntimeError("Migration missing")
    asyncio.run(env.cog.validate_configuration())
    assert "Migration missing" in env.cog.staff_alert.call_args.args[1]


def test_startup_missing_server_icon_alerts_staff(env):
    env.guild.icon = None
    asyncio.run(env.cog.validate_configuration())
    assert "server icon" in env.cog.staff_alert.call_args.args[1]


def test_background_scan_repairs_corrections_but_never_creates_new_notifications(env):
    asyncio.run(env.cog.send(env.source, 7))
    env.play["status"] = "void"
    bang_service.stale.side_effect = lambda is_test, test_channel_id: [{"play_id": 7}]
    asyncio.run(env.cog.refresh_bangs.coro(env.cog))
    assert env.records[101]["displayed_status"] == "void"
    env.records.clear()
    env.play["status"] = "win"
    asyncio.run(env.cog.refresh_bangs.coro(env.cog))
    env.channels[101].send.assert_awaited_once()


def test_durable_event_scan_catches_script_settlements_and_acknowledges_revision(env):
    bang_service.pending.side_effect = lambda: [{"play_id": 7, "revision": "revision-1"}]
    asyncio.run(env.cog.refresh_bangs.coro(env.cog))
    env.channels[101].send.assert_awaited_once()
    bang_service.finish_event.assert_called_once_with(7, "revision-1")


def test_event_failure_stays_pending_and_testing_mode_does_not_consume_live_events(env):
    bang_service.pending.side_effect = lambda: [{"play_id": 7, "revision": "revision-1"}]
    env.cog.official._fetch_play.side_effect = RuntimeError("Database unavailable")
    asyncio.run(env.cog.refresh_bangs.coro(env.cog))
    bang_service.finish_event.assert_not_called()
    bang_service.pending.reset_mock()
    env.cog.get_testing.return_value = True
    asyncio.run(env.cog.refresh_bangs.coro(env.cog))
    bang_service.pending.assert_not_called()
    bang_service.finish_event.assert_not_called()


def test_bang_commands_show_recent_play_dropdown_without_parameters(env):
    for command in ("status_command", "retry_command", "recover_command"):
        request = click(env)
        asyncio.run(getattr(BangNotifications, command).callback(env.cog, request))
        bang_service.recent_plays.assert_called()
        view = request.followup.send.call_args.kwargs["view"]
        assert view.children[0].options[0].value == "7"
        assert request.followup.send.call_args.kwargs["ephemeral"]


def test_selected_status_dropdown_routes_to_status(env):
    request = click(env)
    view = module.RecentBangPlayView(env.cog, "status", request.user.id, [env.play])
    dropdown = view.children[0]
    dropdown._values = ["7"]

    asyncio.run(dropdown.callback(request))

    request.response.edit_message.assert_awaited_once_with(content="Selected Play #7.", view=None)
    assert "Play #7: official / win" in request.followup.send.call_args.args[0]


def test_staff_status_reports_uncertain_delivery_and_how_to_recover(env):
    bang_service.claim(7, 101, False)
    env.records[101].update(state="uncertain", last_error="Connection lost")
    request = click(env)
    asyncio.run(env.cog.show_status(request, 7))
    text = request.followup.send.call_args.args[0]
    assert "uncertain" in text and "Connection lost" in text and "/bang_recover" in text
    assert request.followup.send.call_args.kwargs["ephemeral"]


@pytest.mark.parametrize("command", ["status_command", "retry_command", "recover_command"])
def test_recovery_commands_require_staff_authority(env, command):
    request = click(env, manager=False)
    asyncio.run(getattr(BangNotifications, command).callback(env.cog, request))
    request.response.send_message.assert_awaited_once()
    bang_service.deliveries.assert_not_called()
    bang_service.recover.assert_not_called()


def test_retry_requires_explicit_confirmation(env):
    request = click(env)
    asyncio.run(env.cog.handle_selected_play(request, "retry", 7))
    bang_service.recover.assert_not_called()
    env.channels[101].send.assert_not_awaited()
    assert "Only retry if you confirmed no BANG exists" in request.followup.send.call_args.args[0]
    confirm_view = request.followup.send.call_args.kwargs["view"]
    assert confirm_view.children[0].label == "I checked: no BANG exists"
    asyncio.run(confirm_view.children[0].callback(request))
    env.channels[101].send.assert_awaited_once()
    bang_service.recover.assert_not_called()


def test_retry_refuses_already_delivered_message(env):
    asyncio.run(env.cog.send(env.source, 7))
    request = click(env)
    asyncio.run(env.cog.retry_selected_play(request, 7))
    env.channels[101].send.assert_awaited_once()
    assert "already tracked" in request.followup.send.call_args.args[0]


def test_staff_can_retry_checked_uncertain_delivery_with_rotated_token(env):
    bang_service.claim(7, 101, False)
    env.records[101]["state"] = "uncertain"
    request = click(env)
    asyncio.run(env.cog.retry_selected_play(request, 7))
    bang_service.recover.assert_called_once_with(7, 101, "attempt-1", 42, None)
    assert env.records[101]["attempt_id"] == "attempt-2"
    assert env.records[101]["state"] == "delivered"
    env.channels[101].send.assert_awaited_once()


def test_staff_links_existing_uncertain_bang_without_resending(env):
    asyncio.run(env.cog.send(env.source, 7))
    env.records[101].update(state="uncertain", message_id=None, displayed_status=None)
    env.play["status"] = "loss"
    request = click(env)
    asyncio.run(env.cog.recover_selected_play(request, 7))
    bang_service.recover.assert_called_once_with(7, 101, "attempt-1", 42, 1001)
    assert env.records[101]["displayed_status"] == "loss"
    assert env.messages[(101, 1001)].edit.call_args.kwargs["allowed_mentions"].to_dict() == {"parse": []}
    env.channels[101].send.assert_awaited_once()


@pytest.mark.parametrize("bad_message", ["other_author", "other_play", "not_bang"])
def test_recovery_rejects_unrelated_messages(env, bad_message):
    asyncio.run(env.cog.send(env.source, 7))
    env.records[101].update(state="uncertain", message_id=None, displayed_status=None)
    message = env.messages[(101, 1001)]
    if bad_message == "other_author":
        message.author = SimpleNamespace(id=12345)
    elif bad_message == "other_play":
        message.embeds[0].title = "BANG Play #70 won"
    else:
        message.embeds[0].title = "Play #7 Open"
    request = click(env)
    asyncio.run(env.cog.recover_selected_play(request, 7))
    bang_service.recover.assert_not_called()


def test_claim_does_not_retry_uncertain_database_commit(monkeypatch):
    client = Mock()
    client.rpc.return_value.execute.side_effect = RuntimeError("Connection lost")
    monkeypatch.setattr(bang_service.supabase_service, "_ensure_client", lambda: client)
    with pytest.raises(RuntimeError, match="Connection lost"):
        bang_service.claim(7, 101, False)
    client.rpc.return_value.execute.assert_called_once()


def test_recover_does_not_retry_uncertain_database_commit(monkeypatch):
    client = Mock()
    client.rpc.return_value.execute.side_effect = RuntimeError("Connection lost")
    monkeypatch.setattr(bang_service.supabase_service, "_ensure_client", lambda: client)
    with pytest.raises(RuntimeError, match="Connection lost"):
        bang_service.recover(7, 101, "attempt", 42, None)
    client.rpc.return_value.execute.assert_called_once()


def test_bot_bridge_requires_loaded_bang_cog(monkeypatch):
    monkeypatch.setattr(bot_module.bot, "get_cog", Mock(return_value=None))
    with pytest.raises(RuntimeError, match="not loaded"):
        asyncio.run(bot_module.send_bang_notifications(None, 7))


def test_bot_bridge_delegates_to_bang_cog(env, monkeypatch):
    monkeypatch.setattr(bot_module.bot, "get_cog", Mock(return_value=env.cog))
    env.cog.send = AsyncMock()
    asyncio.run(bot_module.send_bang_notifications(env.source, 7))
    env.cog.send.assert_awaited_once_with(env.source, 7)
