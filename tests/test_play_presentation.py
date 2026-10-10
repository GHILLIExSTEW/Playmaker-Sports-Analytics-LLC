import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

import src.bot as bot_module
import src.play_presentation as module
from src.play_presentation import PlayPresentation, build_engagement_view


def cog():
    return PlayPresentation(
        Mock(user=SimpleNamespace(id=999)), official=Mock(), features=Mock(),
        get_testing=Mock(return_value=False), play_post_target=Mock(return_value=("CONFIRMATION_CHANNEL_ID", 101)),
        resolve_channel=AsyncMock(return_value=SimpleNamespace(send=AsyncMock())),
        staff_alert=AsyncMock(),
    )


def click(custom_id="pm:tail:7"):
    return SimpleNamespace(
        type=discord.InteractionType.component, data={"custom_id": custom_id},
        user=SimpleNamespace(id=42),
        message=None,
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock(), is_done=Mock(return_value=False)),
        followup=SimpleNamespace(send=AsyncMock()), edit_original_response=AsyncMock(),
    )


def payload():
    return {"play_id": 7, "summary": "Pick", "units": 1, "odds": 150, "to_win": 1.5}


def http_error():
    return discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Missing permissions")


@pytest.mark.parametrize("status", ["open", "regraded"])
@pytest.mark.parametrize("tailing", [True, False])
def test_tail_toggle_preserves_member_identity_and_private_confirmation(status, tailing):
    presentation = cog()
    presentation.official._fetch_play.return_value = {"status": status}
    presentation.features.toggle_tail.return_value = (tailing, 3)
    request = click()
    asyncio.run(presentation.handle_tail_click(request, 7))
    presentation.features.toggle_tail.assert_called_once_with(7, 42)
    request.response.defer.assert_awaited_once()
    assert request.followup.send.call_args.kwargs["ephemeral"]
    text = request.followup.send.call_args.args[0]
    assert ("You're tailing" if tailing else "Removed your tail") in text


@pytest.mark.parametrize("status", ["win", "loss", "void", "partial"])
def test_closed_play_tail_is_not_mutated(status):
    presentation = cog()
    presentation.official._fetch_play.return_value = {"status": status}
    request = click()
    asyncio.run(presentation.handle_tail_click(request, 7))
    presentation.features.toggle_tail.assert_not_called()
    assert "tails are closed" in request.followup.send.call_args.args[0]


def test_tail_count_update_keeps_other_buttons_and_does_not_store_view():
    presentation = cog()
    presentation.official._fetch_play.return_value = {"status": "open"}
    presentation.features.toggle_tail.return_value = (True, 4)
    request = click()
    request.message = SimpleNamespace(components=[discord.components.ActionRow({
        "type": 1, "components": [
            {"type": 2, "style": 3, "label": "Tail (0)", "custom_id": "pm:tail:7"},
            {"type": 2, "style": 2, "label": "Details", "custom_id": "details:7"},
        ],
    })])
    asyncio.run(presentation.handle_tail_click(request, 7))
    view = request.edit_original_response.call_args.kwargs["view"]
    assert [(item.custom_id, item.label) for item in view.children] == [("pm:tail:7", "Tail (4)"), ("details:7", "Details")]
    assert view.is_finished()


def test_tail_dispatch_owns_existing_button_id():
    presentation = cog()
    presentation.handle_tail_click = AsyncMock()
    request = click()
    asyncio.run(presentation.on_play_feature_interaction(request))
    presentation.handle_tail_click.assert_awaited_once_with(request, 7)


@pytest.mark.parametrize("custom_id", ["pm:as:7:win", "pm:asx:7", "pm:insight:7", "unrelated:7"])
def test_listener_ignores_other_workflow_buttons(custom_id):
    presentation = cog()
    presentation.handle_tail_click = AsyncMock()
    request = click(custom_id)
    asyncio.run(presentation.on_play_feature_interaction(request))
    presentation.handle_tail_click.assert_not_awaited()
    request.response.send_message.assert_not_awaited()
    request.followup.send.assert_not_awaited()


def test_noncomponent_interaction_is_ignored():
    presentation = cog()
    request = click()
    request.type = discord.InteractionType.application_command
    asyncio.run(presentation.on_play_feature_interaction(request))
    presentation.official._fetch_play.assert_not_called()


def test_retired_follow_buttons_remain_private_and_do_not_subscribe():
    presentation = cog()
    request = click("pm:follow:7")
    asyncio.run(presentation.on_play_feature_interaction(request))
    request.response.send_message.assert_awaited_once_with("Follow alerts have been retired.", ephemeral=True)
    presentation.official._fetch_play.assert_not_called()
    presentation.features.toggle_tail.assert_not_called()


@pytest.mark.parametrize("custom_id", ["pm:tail:bad", "pm:tail:0", "pm:tail:7:extra", "pm:tail:"])
def test_malformed_tail_button_reports_error_without_mutation(custom_id, caplog):
    presentation = cog()
    request = click(custom_id)
    asyncio.run(presentation.on_play_feature_interaction(request))
    presentation.features.toggle_tail.assert_not_called()
    assert request.response.send_message.call_args.kwargs["ephemeral"]
    assert "play_feature_interaction_failed" in caplog.text


def test_tail_database_failure_reports_after_defer():
    presentation = cog()
    presentation.official._fetch_play.side_effect = RuntimeError("offline")
    request = click()
    request.response.is_done.return_value = True
    asyncio.run(presentation.on_play_feature_interaction(request))
    presentation.features.toggle_tail.assert_not_called()
    assert "Something went wrong" in request.followup.send.call_args.args[0]
    assert request.followup.send.call_args.kwargs["ephemeral"]


def test_publishing_preserves_author_image_and_explicit_target():
    presentation = cog()
    request = SimpleNamespace(user=SimpleNamespace(display_name="Ace", display_avatar=SimpleNamespace(url="https://example.com/avatar.png")))
    data = {**payload(), "image_url": "https://example.com/slip.png"}
    asyncio.run(presentation.publish_play_message(request, data, ("OFFICIAL_CHANNEL_ID", 303)))
    presentation.resolve_channel.assert_awaited_once_with(303, "OFFICIAL_CHANNEL_ID", required=True)
    embed = presentation.resolve_channel.return_value.send.call_args.kwargs["embed"]
    assert embed.author.name == "Ace"
    assert embed.image.url == data["image_url"]


def test_publish_missing_channel_raises_without_claiming_success():
    presentation = cog()
    presentation.resolve_channel.return_value = None
    with pytest.raises(RuntimeError, match="unavailable"):
        asyncio.run(presentation.publish_play_message(Mock(), payload()))


def test_settled_card_http_failure_alerts_without_undoing_result():
    presentation = cog()
    message = SimpleNamespace(id=500, embeds=[], edit=AsyncMock(side_effect=http_error()))
    asyncio.run(presentation.update_play_message(message, 7, "win"))
    presentation.staff_alert.assert_awaited_once()
    presentation.official.settle_play.assert_not_called()


@pytest.mark.parametrize("author", [999, 42])
def test_refresh_edits_only_bot_owned_cards_from_current_record(author):
    presentation = cog()
    presentation.official._fetch_play.return_value = {
        "id": 7, "message_id": "500", "status": "regraded", "units": 2, "odds": -110,
    }
    message = SimpleNamespace(author=SimpleNamespace(id=author), embeds=[], edit=AsyncMock())
    presentation.fetch_guild_message = AsyncMock(return_value=message)
    asyncio.run(presentation.refresh_play_card(None, 7))
    if author == 999:
        assert message.edit.call_args.kwargs["embed"].title == "Play #7 • Regraded"
    else:
        message.edit.assert_not_awaited()


def test_refresh_database_failure_alerts_explicitly():
    presentation = cog()
    presentation.official._fetch_play.side_effect = RuntimeError("offline")
    asyncio.run(presentation.refresh_play_card(None, 7))
    presentation.staff_alert.assert_awaited_once()


def test_fetch_message_skips_missing_and_forbidden_channels():
    presentation = cog()
    found = object()
    guild = SimpleNamespace(text_channels=[
        SimpleNamespace(fetch_message=AsyncMock(side_effect=http_error())),
        SimpleNamespace(fetch_message=AsyncMock(return_value=found)),
    ])
    assert asyncio.run(presentation.fetch_guild_message(guild, 500)) is found
    assert asyncio.run(presentation.fetch_guild_message(None, 500)) is None


def test_repost_send_failure_closes_file_and_preserves_original():
    presentation = cog()
    file = SimpleNamespace(filename="slip.png", close=Mock())
    source = SimpleNamespace(
        content="", attachments=[SimpleNamespace(content_type="image/png", to_file=AsyncMock(return_value=file))],
        delete=AsyncMock(),
    )
    channel = SimpleNamespace(fetch_message=AsyncMock(return_value=source), send=AsyncMock(side_effect=RuntimeError("offline")))
    author = SimpleNamespace(display_name="Ace", display_avatar=SimpleNamespace(url="https://example.com/avatar.png"))
    assert asyncio.run(presentation.repost_official_play(channel, 500, payload(), author)) is None
    file.close.assert_called_once()
    source.delete.assert_not_awaited()
    presentation.staff_alert.assert_awaited_once()


def test_failed_source_deletion_keeps_successful_repost_and_alerts():
    presentation = cog()
    source = SimpleNamespace(content="", attachments=[], delete=AsyncMock(side_effect=http_error()))
    sent = SimpleNamespace(id=501)
    channel = SimpleNamespace(fetch_message=AsyncMock(return_value=source), send=AsyncMock(return_value=sent))
    author = SimpleNamespace(display_name="Ace", display_avatar=SimpleNamespace(url="https://example.com/avatar.png"))
    assert asyncio.run(presentation.repost_official_play(channel, 500, payload(), author)) is sent
    presentation.staff_alert.assert_awaited_once()
    assert "was reposted" in presentation.staff_alert.call_args.args[1]


@pytest.mark.parametrize("separate", [False, True])
def test_tail_bar_uses_card_or_reply_without_author_ping(separate):
    presentation = cog()
    card = SimpleNamespace(id=500, edit=AsyncMock())
    tracked = SimpleNamespace(id=501, reply=AsyncMock()) if separate else None
    asyncio.run(presentation.announce_new_play(payload(), "Ace", card, tracked))
    if separate:
        tracked.reply.assert_awaited_once()
        assert tracked.reply.call_args.kwargs["mention_author"] is False
        view = tracked.reply.call_args.kwargs["view"]
        card.edit.assert_not_awaited()
    else:
        view = card.edit.call_args.kwargs["view"]
    assert view.children[0].custom_id == "pm:tail:7" and view.is_finished()


def test_tail_bar_failure_reports_without_claiming_record_failed():
    presentation = cog()
    card = SimpleNamespace(id=500, edit=AsyncMock(side_effect=http_error()))
    asyncio.run(presentation.announce_new_play(payload(), "Ace", card, None))
    presentation.staff_alert.assert_awaited_once()
    assert "was recorded" in presentation.staff_alert.call_args.args[1]


def test_bot_bridges_delegate_to_loaded_owner(load_presentation):
    presentation = load_presentation()
    message = SimpleNamespace(id=500)
    user = SimpleNamespace(id=42)
    guild = SimpleNamespace(id=123)
    channel = SimpleNamespace(id=101)
    presentation.publish_play_message = AsyncMock(return_value=message)
    presentation.update_play_message = AsyncMock()
    presentation.fetch_guild_message = AsyncMock(return_value=message)
    presentation.refresh_play_card = AsyncMock()
    presentation.repost_official_play = AsyncMock(return_value=message)
    presentation.announce_new_play = AsyncMock()
    presentation.send_bang_notifications = AsyncMock()

    async def check():
        assert await bot_module.publish_play_message(user, payload(), ("OFFICIAL_CHANNEL_ID", 101)) is message
        await bot_module.update_play_message(message, 7, "win")
        assert await bot_module.fetch_guild_message(guild, 500) is message
        await bot_module.refresh_play_card(guild, 7)
        assert await bot_module.repost_official_play(channel, 500, payload(), user) is message
        await bot_module.announce_new_play(payload(), "Ace", message, message)
        await bot_module.send_bang_notifications(message, 7)
    asyncio.run(check())
    presentation.publish_play_message.assert_awaited_once_with(user, payload(), ("OFFICIAL_CHANNEL_ID", 101))
    presentation.update_play_message.assert_awaited_once_with(message, 7, "win")
    presentation.fetch_guild_message.assert_awaited_once_with(guild, 500)
    presentation.refresh_play_card.assert_awaited_once_with(guild, 7)
    presentation.repost_official_play.assert_awaited_once_with(channel, 500, payload(), user)
    presentation.announce_new_play.assert_awaited_once_with(payload(), "Ace", message, message)
    presentation.send_bang_notifications.assert_awaited_once_with(message, 7)


def test_bang_wrong_guild_does_not_claim_or_publish(monkeypatch):
    presentation = cog()
    monkeypatch.setattr(module, "GUILD_ID", 123)
    claim = Mock()
    monkeypatch.setattr(module.bang_service, "claim", claim)
    for guild in [None, SimpleNamespace(id=456)]:
        asyncio.run(presentation.send_bang_notifications(SimpleNamespace(guild=guild), 7))
    claim.assert_not_called()
    presentation.resolve_channel.assert_not_awaited()


def test_missing_presentation_cog_is_explicit(monkeypatch):
    monkeypatch.setattr(bot_module.bot, "get_cog", Mock(return_value=None))
    with pytest.raises(RuntimeError, match="not loaded"):
        bot_module.get_play_presentation()


def test_engagement_view_uses_persistent_id():
    async def build():
        return build_engagement_view(7, 3)
    view = asyncio.run(build())
    assert view.children[0].custom_id == "pm:tail:7" and view.children[0].label == "Tail (3)"
    assert view.is_finished()
