import asyncio
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import src.bot as bot_module
import src.play_presentation as presentation_module
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
    monkeypatch.setattr(presentation_module, "WINNING_SLIPS_CHANNEL_ID", 101)
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


def test_bang_uploads_slip_to_winning_slips_with_highroller_mention(monkeypatch, load_presentation):
    channels, complete, _ = setup(monkeypatch)
    message = source()
    load_presentation()
    monkeypatch.setattr(presentation_module, "WINNING_SLIPS_CHANNEL_ID", 101)
    asyncio.run(bot_module.send_bang_notifications(message, 7))
    kwargs = channels[101].send.call_args.kwargs
    assert kwargs["content"] == f"## 💰 BANG! 💸 🤑\n<@&{bot_module.HIGHROLLER_ROLE_ID}>"
    assert "💰 BANG!" in kwargs["embed"].title
    assert "Official play WIN" in kwargs["embed"].footer.text
    assert kwargs["embed"].image.url == "attachment://slip.png"
    assert kwargs["file"].filename == "slip.png"
    assert [role.id for role in kwargs["allowed_mentions"].roles] == [bot_module.HIGHROLLER_ROLE_ID]
    assert not kwargs["allowed_mentions"].users and not kwargs["allowed_mentions"].everyone
    assert kwargs["embed"].description == f"[📕│𝑽𝑰𝑷-𝑷𝑰𝑪𝑲𝑺]({message.jump_url})"
    channels[202].send.assert_not_awaited()
    bot_module.resolve_channel.assert_awaited_once_with(101, "WINNING_SLIPS_CHANNEL_ID", required=True)
    message.attachments[0].to_file.assert_awaited_once()
    complete.assert_called_once_with(7, 101, 900)
    asyncio.run(bot_module.send_bang_notifications(message, 7))
    channels[101].send.assert_awaited_once()
    channels[202].send.assert_not_awaited()


def test_bang_preserves_embed_only_image_and_missing_image_reports_explicitly(monkeypatch, load_presentation):
    channels, _, alert = setup(monkeypatch)
    message = source(False)
    load_presentation()
    message.embeds[0].set_image(url="https://cdn.example/slip.png")
    asyncio.run(bot_module.send_bang_notifications(message, 7))
    assert channels[101].send.call_args.kwargs["embed"].image.url == "https://cdn.example/slip.png"
    alert.assert_not_awaited()
    asyncio.run(bot_module.send_bang_notifications(source(False), 8))
    assert "No slip image" in channels[101].send.call_args.kwargs["embed"].description
    alert.assert_awaited_once()


def test_winning_slips_destination_failure_does_not_fall_back_to_free_chat(monkeypatch, load_presentation):
    channels, complete, alert = setup(monkeypatch)
    channels[101].send.side_effect = RuntimeError("Unavailable")
    load_presentation()
    asyncio.run(bot_module.send_bang_notifications(source(), 7))
    channels[202].send.assert_not_awaited()
    complete.assert_not_called()
    alert.assert_awaited_once()


def test_testing_does_not_ping_or_send_to_live_channels(monkeypatch, load_presentation):
    _, _, _ = setup(monkeypatch)
    monkeypatch.setattr(bot_module, "testing_enabled", True)
    monkeypatch.setattr(presentation_module, "TEST_CHANNEL_ID", 303)
    channel = SimpleNamespace(id=303, send=AsyncMock(return_value=SimpleNamespace(id=999)))
    resolve = AsyncMock(return_value=channel)
    monkeypatch.setattr(bot_module, "resolve_channel", resolve)
    load_presentation()
    asyncio.run(bot_module.send_bang_notifications(source(), 7))
    resolve.assert_awaited_once_with(303, "TEST_CHANNEL_ID", required=True)
    assert channel.send.call_args.kwargs["allowed_mentions"].roles is False


@pytest.mark.parametrize(
    ("tracking_scope", "bang_expected"),
    [("official", True), ("capper", False)],
)
def test_reaction_requires_settlement_authority_and_only_official_win_posts_bang(
    monkeypatch, load_settlement, tracking_scope, bang_expected,
):
    setup(monkeypatch)
    play = {"id": 7, "discord_user_id": "42", "status": "open", "tracking_scope": tracking_scope}
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
    load_settlement()
    for user_id, emoji in [(43, "✅"), (42, "❌"), (42, "✅")]:
        payload = SimpleNamespace(user_id=user_id, emoji=emoji, channel_id=101, message_id=500,
                                  member=SimpleNamespace(id=user_id, bot=False))
        asyncio.run(bot_module.on_raw_reaction_add(payload))
    assert [call.args for call in settle.call_args_list] == [(7, "loss"), (7, "win")]
    if bang_expected:
        bang.assert_awaited_once_with(message, 7)
    else:
        bang.assert_not_awaited()


def test_claim_does_not_retry_uncertain_database_commit(monkeypatch):
    client = Mock()
    client.rpc.return_value.execute.side_effect = RuntimeError("Connection lost")
    monkeypatch.setattr(bang_service.supabase_service, "_ensure_client", lambda: client)
    with pytest.raises(RuntimeError, match="Connection lost"):
        bang_service.claim(7, 101)
    client.rpc.return_value.execute.assert_called_once()


def test_other_bot_reactions_do_not_trigger_settlement_or_bang(monkeypatch, load_settlement):
    monkeypatch.setattr(type(bot_module.bot), "user", property(lambda self: SimpleNamespace(id=999)))
    lookup = Mock()
    monkeypatch.setattr(bot_module.official_play_service, "get_play_for_message", lookup)
    payload = SimpleNamespace(user_id=888, member=SimpleNamespace(bot=True))
    load_settlement()
    asyncio.run(bot_module.on_raw_reaction_add(payload))
    lookup.assert_not_called()
