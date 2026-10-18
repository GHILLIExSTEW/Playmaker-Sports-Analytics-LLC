import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

pytestmark = pytest.mark.usefixtures("mock_play_cards")

import src.bot as bot_module
import src.play_presentation as module
from src.play_presentation import PlayPresentation, build_engagement_view


def cog():
    presentation = PlayPresentation(
        Mock(user=SimpleNamespace(id=999)), official=Mock(), features=Mock(),
        get_testing=Mock(return_value=False), play_post_target=Mock(return_value=("CONFIRMATION_CHANNEL_ID", 101)),
        resolve_channel=AsyncMock(return_value=SimpleNamespace(send=AsyncMock())),
        staff_alert=AsyncMock(),
    )
    presentation.capper_webhook = AsyncMock(return_value=SimpleNamespace(id=777, send=AsyncMock(), edit_message=AsyncMock()))
    return presentation


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
    request = SimpleNamespace(guild=SimpleNamespace(id=123), user=SimpleNamespace(display_name="Ace", display_avatar=SimpleNamespace(url="https://example.com/avatar.png")))
    data = {**payload(), "image_url": "https://example.com/slip.png"}
    asyncio.run(presentation.publish_play_message(request, data, ("OFFICIAL_CHANNEL_ID", 303)))
    presentation.resolve_channel.assert_awaited_once_with(303, "OFFICIAL_CHANNEL_ID", required=True)
    embed = presentation.resolve_channel.return_value.send.call_args.kwargs["embed"]
    assert embed.author.name == "Ace"
    assert embed.image.url == data["image_url"]


def test_publishing_capper_play_mentions_highroller_and_rookie_roles_only(monkeypatch, mock_play_cards):
    monkeypatch.setattr(module, "HIGHROLLER_ROLE_ID", 789)
    monkeypatch.setattr(module, "ROOKIE_ROLE_ID", 456)
    presentation = cog()
    request = SimpleNamespace(guild=SimpleNamespace(id=123), user=SimpleNamespace(display_name="Ace", display_avatar=SimpleNamespace(url="https://example.com/avatar.png")))
    data = {**payload(), "tracking_scope": "capper"}
    asyncio.run(presentation.publish_play_message(request, data, ("CAPPER_CHANNEL_ID", 303)))
    sent = presentation.capper_webhook.return_value.send.call_args.kwargs
    assert sent["content"] == "<@&789> <@&456>"
    assert sent["allowed_mentions"].to_dict() == {"roles": [789, 456], "parse": []}
    assert sent["allowed_mentions"].replied_user is False
    assert sent["embed"].author.name is None
    assert sent["username"] == "Ace"
    assert sent["avatar_url"] == request.user.display_avatar.url
    assert sent["wait"] is True
    assert sent["embed"].image.url.startswith("attachment://playmaker-card-")
    assert sent["embed"].fields == []
    assert sent["embed"].description is None
    assert "file" in sent
    assert mock_play_cards.call_args.args[0]["tracking_scope"] == "capper"


def test_publishing_capper_play_removes_highroller_mentions_from_embed(monkeypatch, mock_play_cards):
    monkeypatch.setattr(module, "HIGHROLLER_ROLE_ID", 789)
    presentation = cog()
    request = SimpleNamespace(guild=SimpleNamespace(id=123), user=SimpleNamespace(display_name="Ace", display_avatar=SimpleNamespace(url="https://example.com/avatar.png")))
    data = {
        **payload(),
        "tracking_scope": "capper",
        "legs": 1,
        "summary": "<@&789>",
        "play_text": "Pick <@&789>",
    }
    asyncio.run(presentation.publish_play_message(request, data, ("CAPPER_CHANNEL_ID", 303)))
    embed = presentation.capper_webhook.return_value.send.call_args.kwargs["embed"]
    assert embed.description is None
    assert embed.fields == []
    assert mock_play_cards.call_args.args[0]["play_text"] == "Pick"


def test_publishing_official_play_does_not_add_rookie_mention(monkeypatch):
    presentation = cog()
    presentation.bot.user = SimpleNamespace(
        display_name="Playmaker Picks Team",
        display_avatar=SimpleNamespace(url="https://example.com/bot.png"),
    )
    request = SimpleNamespace(guild=SimpleNamespace(id=123), user=SimpleNamespace(display_name="Ace", display_avatar=SimpleNamespace(url="https://example.com/avatar.png")))
    data = {**payload(), "tracking_scope": "official"}
    asyncio.run(presentation.publish_play_message(request, data, ("OFFICIAL_CHANNEL_ID", 303)))
    sent = presentation.resolve_channel.return_value.send.call_args.kwargs
    assert "content" not in sent and "allowed_mentions" not in sent
    assert sent["embed"].author.name == presentation.bot.user.display_name


def test_publish_missing_channel_raises_without_claiming_success():
    presentation = cog()
    presentation.resolve_channel.return_value = None
    with pytest.raises(RuntimeError, match="unavailable"):
        asyncio.run(presentation.publish_play_message(Mock(), payload()))


@pytest.mark.parametrize("operation", ["settle", "refresh"])
def test_capper_card_regenerates_on_settlement_and_refresh(operation, mock_play_cards):
    presentation = cog()
    play = {
        "id": 7, "message_id": "500", "tracking_scope": "capper",
        "status": "win", "units": 2, "odds": -110,
    }
    presentation.official._fetch_play.return_value = play
    presentation.official.get_play_legs.return_value = [{"selection": "Purdue -2.5"}]
    original = discord.Embed()
    original.set_author(name="Ace")
    message = SimpleNamespace(
        id=500, guild=SimpleNamespace(id=123), author=SimpleNamespace(id=999),
        embeds=[original], attachments=[], edit=AsyncMock(),
    )
    presentation.fetch_guild_message = AsyncMock(return_value=message)
    if operation == "settle":
        asyncio.run(presentation.update_play_message(message, 7, "win"))
    else:
        asyncio.run(presentation.refresh_play_card(message.guild, 7))
    mock_play_cards.assert_awaited_once_with(
        play, message.guild, source=message, legs=presentation.official.get_play_legs.return_value,
    )
    edited = message.edit.call_args.kwargs
    assert edited["embed"].author.name == "Ace"
    assert edited["embed"].image.url.startswith("attachment://playmaker-card-")
    assert edited["attachments"]
    assert "view" not in edited and "content" not in edited
    presentation.staff_alert.assert_not_awaited()


def test_capper_repost_renders_original_slip_and_preserves_capper_identity(monkeypatch, mock_play_cards):
    monkeypatch.setattr(module, "HIGHROLLER_ROLE_ID", 789)
    monkeypatch.setattr(module, "ROOKIE_ROLE_ID", 456)
    presentation = cog()
    source = SimpleNamespace(
        content="", embeds=[], attachments=[SimpleNamespace(content_type="image/png", read=AsyncMock(return_value=b"original"))],
        delete=AsyncMock(),
    )
    channel = SimpleNamespace(
        guild=SimpleNamespace(id=123), fetch_message=AsyncMock(return_value=source), send=AsyncMock(),
    )
    author = SimpleNamespace(display_name="Ace", display_avatar=SimpleNamespace(url="https://example.com/avatar.png"))
    data = {**payload(), "tracking_scope": "capper"}
    asyncio.run(presentation.repost_official_play(channel, 500, data, author))
    sent = presentation.capper_webhook.return_value.send.call_args.kwargs
    assert sent["content"] == "<@&789> <@&456>"
    assert sent["allowed_mentions"].to_dict() == {"roles": [789, 456], "parse": []}
    assert sent["embed"].author.name is None
    assert sent["username"] == "Ace"
    assert sent["embed"].fields == []
    assert sent["file"]
    assert mock_play_cards.call_args.kwargs["slip"] == b"original"
    assert mock_play_cards.call_args.kwargs["source"] is source
    assert mock_play_cards.call_args.args[0]["tracking_scope"] == "capper"
    source.delete.assert_awaited_once()


@pytest.mark.parametrize("operation", ["settle", "refresh"])
def test_webhook_capper_cards_edit_through_owned_webhook(operation):
    presentation = cog()
    presentation.official._fetch_play.return_value = {
        "id": 7, "message_id": "500", "tracking_scope": "capper", "status": "win", "units": 2, "odds": 100,
    }
    presentation.official.get_play_legs.return_value = []
    original = discord.Embed()
    original.set_author(name="Ace", icon_url="https://example.com/avatar.png")
    message = SimpleNamespace(
        id=500, webhook_id=777, channel=SimpleNamespace(id=303), guild=SimpleNamespace(id=123),
        author=SimpleNamespace(id=777), embeds=[original], attachments=[], edit=AsyncMock(),
    )
    presentation.fetch_guild_message = AsyncMock(return_value=message)
    if operation == "settle":
        asyncio.run(presentation.update_play_message(message, 7, "win"))
    else:
        asyncio.run(presentation.refresh_play_card(message.guild, 7))
    presentation.capper_webhook.return_value.edit_message.assert_awaited_once()
    assert presentation.capper_webhook.return_value.edit_message.call_args.kwargs["embed"].author.name is None
    message.edit.assert_not_awaited()
    presentation.staff_alert.assert_not_awaited()


def test_unrelated_webhook_cannot_be_edited():
    presentation = cog()
    message = SimpleNamespace(id=500, webhook_id=888, channel=SimpleNamespace(id=303), edit=AsyncMock())
    with pytest.raises(ValueError, match="unrelated webhook"):
        asyncio.run(presentation.edit_play_message(message, embed=discord.Embed()))
    presentation.capper_webhook.return_value.edit_message.assert_not_awaited()


def test_webhook_embed_only_edit_keeps_image_bound_to_attachment():
    presentation = cog()
    embed = discord.Embed()
    embed.set_author(name="Ace")
    embed.set_image(url="https://cdn.discordapp.com/card.png")
    message = SimpleNamespace(
        id=500, webhook_id=777, channel=SimpleNamespace(id=303),
        attachments=[SimpleNamespace(url=embed.image.url, filename="card.png")],
    )
    asyncio.run(presentation.edit_play_message(message, embed=embed))
    edited = presentation.capper_webhook.return_value.edit_message.call_args.kwargs["embed"]
    assert edited.author.name is None
    assert edited.image.url == "attachment://card.png"


def test_capper_webhook_reuses_only_bot_owned_named_hook():
    presentation = cog()
    del presentation.capper_webhook
    own = SimpleNamespace(id=777, name="Playmaker Capper Plays", user=SimpleNamespace(id=999), token="test")
    unrelated = SimpleNamespace(id=888, name=own.name, user=SimpleNamespace(id=42), token="test")
    channel = SimpleNamespace(id=303, webhooks=AsyncMock(return_value=[unrelated, own]), create_webhook=AsyncMock())

    async def run():
        assert await presentation.capper_webhook(channel) is own
        assert await presentation.capper_webhook(channel) is own
    asyncio.run(run())
    channel.webhooks.assert_awaited_once()
    channel.create_webhook.assert_not_awaited()


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

    async def check():
        assert await bot_module.publish_play_message(user, payload(), ("OFFICIAL_CHANNEL_ID", 101)) is message
        await bot_module.update_play_message(message, 7, "win")
        assert await bot_module.fetch_guild_message(guild, 500) is message
        await bot_module.refresh_play_card(guild, 7)
        assert await bot_module.repost_official_play(channel, 500, payload(), user) is message
        await bot_module.announce_new_play(payload(), "Ace", message, message)
    asyncio.run(check())
    presentation.publish_play_message.assert_awaited_once_with(user, payload(), ("OFFICIAL_CHANNEL_ID", 101))
    presentation.update_play_message.assert_awaited_once_with(message, 7, "win")
    presentation.fetch_guild_message.assert_awaited_once_with(guild, 500)
    presentation.refresh_play_card.assert_awaited_once_with(guild, 7)
    presentation.repost_official_play.assert_awaited_once_with(channel, 500, payload(), user)
    presentation.announce_new_play.assert_awaited_once_with(payload(), "Ace", message, message)


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
