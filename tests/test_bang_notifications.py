import asyncio
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import src.bot as bot_module
import pytest
from src.services import bang_service


def source(image=True):
    embed = bot_module.discord.Embed()
    embed.set_author(name="Capper")
    attachment = SimpleNamespace(
        content_type="image/png",
        to_file=AsyncMock(side_effect=lambda: bot_module.discord.File(BytesIO(b"slip"), filename="slip.png")),
    )
    return SimpleNamespace(
        id=500, guild=SimpleNamespace(id=bot_module.GUILD_ID), attachments=[attachment] if image else [],
        embeds=[embed], jump_url="https://discord.com/channels/1/2/500",
    )


def setup(monkeypatch):
    monkeypatch.setattr(bot_module, "testing_enabled", False)
    channels = {101: SimpleNamespace(id=101, send=AsyncMock(return_value=SimpleNamespace(id=900))),
                202: SimpleNamespace(id=202, send=AsyncMock(return_value=SimpleNamespace(id=901)))}
    monkeypatch.setattr(bot_module, "VIP_CHAT_CHANNEL_ID", 101)
    monkeypatch.setattr(bot_module, "FREE_CHAT_CHANNEL_ID", 202)
    monkeypatch.setattr(bot_module, "resolve_channel", AsyncMock(side_effect=lambda channel_id, *args, **kwargs: channels[channel_id]))
    claims = set()

    def claim(play_id, channel_id):
        key = play_id, channel_id
        if key in claims:
            return False
        claims.add(key)
        return True

    monkeypatch.setattr(bang_service, "claim", claim)
    complete = Mock()
    monkeypatch.setattr(bang_service, "complete", complete)
    alert = AsyncMock()
    monkeypatch.setattr(bot_module, "send_staff_alert", alert)
    return channels, complete, alert


def test_bang_uploads_slip_to_both_channels_with_only_correct_role_mentions(monkeypatch):
    channels, complete, _ = setup(monkeypatch)
    message = source()
    asyncio.run(bot_module.send_bang_notifications(message, 7))
    for channel_id, role_id in [(101, bot_module.HIGHROLLER_ROLE_ID), (202, bot_module.ROOKIE_ROLE_ID)]:
        kwargs = channels[channel_id].send.call_args.kwargs
        assert kwargs["content"] == f"BANG! <@&{role_id}>"
        assert kwargs["embed"].image.url == "attachment://slip.png"
        assert kwargs["file"].filename == "slip.png"
        assert [role.id for role in kwargs["allowed_mentions"].roles] == [role_id]
        assert not kwargs["allowed_mentions"].users and not kwargs["allowed_mentions"].everyone
        assert message.jump_url in kwargs["embed"].description
    assert message.attachments[0].to_file.await_count == 2
    assert complete.call_count == 2
    asyncio.run(bot_module.send_bang_notifications(message, 7))
    assert all(channel.send.await_count == 1 for channel in channels.values())


def test_bang_preserves_embed_only_image_and_missing_image_reports_explicitly(monkeypatch):
    channels, _, alert = setup(monkeypatch)
    message = source(False)
    message.embeds[0].set_image(url="https://cdn.example/slip.png")
    asyncio.run(bot_module.send_bang_notifications(message, 7))
    assert channels[101].send.call_args.kwargs["embed"].image.url == "https://cdn.example/slip.png"
    alert.assert_not_awaited()
    asyncio.run(bot_module.send_bang_notifications(source(False), 8))
    assert "No slip image" in channels[101].send.call_args.kwargs["embed"].description
    alert.assert_awaited_once()


def test_one_destination_failure_still_delivers_to_other(monkeypatch):
    channels, complete, alert = setup(monkeypatch)
    channels[101].send.side_effect = RuntimeError("Unavailable")
    asyncio.run(bot_module.send_bang_notifications(source(), 7))
    channels[202].send.assert_awaited_once()
    complete.assert_called_once_with(7, 202, 901)
    alert.assert_awaited_once()


def test_testing_does_not_ping_or_send_to_live_channels(monkeypatch):
    _, _, _ = setup(monkeypatch)
    monkeypatch.setattr(bot_module, "testing_enabled", True)
    monkeypatch.setattr(bot_module, "TEST_CHANNEL_ID", 303)
    channel = SimpleNamespace(id=303, send=AsyncMock(return_value=SimpleNamespace(id=999)))
    resolve = AsyncMock(return_value=channel)
    monkeypatch.setattr(bot_module, "resolve_channel", resolve)
    asyncio.run(bot_module.send_bang_notifications(source(), 7))
    resolve.assert_awaited_once_with(303, "TEST_CHANNEL_ID", required=True)
    assert channel.send.call_args.kwargs["allowed_mentions"].roles is False


def test_reaction_requires_settlement_authority_and_only_win_posts_bang(monkeypatch):
    setup(monkeypatch)
    play = {"id": 7, "discord_user_id": "42", "status": "open"}
    monkeypatch.setattr(bot_module.official_play_service, "get_play_for_message", lambda message_id: play)
    settle = Mock()
    monkeypatch.setattr(bot_module.official_play_service, "settle_play", settle)
    monkeypatch.setattr(type(bot_module.bot), "user", property(lambda self: SimpleNamespace(id=999)))
    monkeypatch.setattr(bot_module, "settlement_channel_settings", lambda: [("test", 101)])
    message = source()
    channel = SimpleNamespace(guild=message.guild, fetch_message=AsyncMock(return_value=message))
    monkeypatch.setattr(bot_module.bot, "get_channel", lambda channel_id: channel)
    monkeypatch.setattr(bot_module, "user_can_settle", lambda user, owner, guild: user.id == 42)
    monkeypatch.setattr(bot_module, "update_play_message", AsyncMock())
    bang = AsyncMock()
    monkeypatch.setattr(bot_module, "send_bang_notifications", bang)
    for user_id, emoji in [(43, "✅"), (42, "❌"), (42, "✅")]:
        payload = SimpleNamespace(user_id=user_id, emoji=emoji, channel_id=101, message_id=500,
                                  member=SimpleNamespace(id=user_id, bot=False))
        asyncio.run(bot_module.on_raw_reaction_add(payload))
    assert [call.args for call in settle.call_args_list] == [(7, "loss"), (7, "win")]
    bang.assert_awaited_once_with(message, 7)


def test_claim_does_not_retry_uncertain_database_commit(monkeypatch):
    client = Mock()
    client.rpc.return_value.execute.side_effect = RuntimeError("Connection lost")
    monkeypatch.setattr(bang_service.supabase_service, "_ensure_client", lambda: client)
    with pytest.raises(RuntimeError, match="Connection lost"):
        bang_service.claim(7, 101)
    client.rpc.return_value.execute.assert_called_once()


def test_other_bot_reactions_do_not_trigger_settlement_or_bang(monkeypatch):
    monkeypatch.setattr(type(bot_module.bot), "user", property(lambda self: SimpleNamespace(id=999)))
    lookup = Mock()
    monkeypatch.setattr(bot_module.official_play_service, "get_play_for_message", lookup)
    payload = SimpleNamespace(user_id=888, member=SimpleNamespace(bot=True))
    asyncio.run(bot_module.on_raw_reaction_add(payload))
    lookup.assert_not_called()
