import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

import src.bot as bot_module
import src.play_submission as module
from src.play_submission import AutoImageView, AutoUnitsModal, ConfirmImageView, LegModal, PlayModal, PlaySubmission


def submission():
    return PlaySubmission(
        official=Mock(create_play_record=Mock(return_value={"play_id": 7})),
        get_testing=Mock(return_value=False), is_tracked_operator=Mock(return_value=True),
        publish_play=AsyncMock(return_value=SimpleNamespace(id=101)),
        official_post_target=Mock(return_value=("OFFICIAL_CHANNEL_ID", 777)),
        confirm_recorded=AsyncMock(), repost_play=AsyncMock(return_value=None),
        announce_play=AsyncMock(), staff_alert=AsyncMock(), images=Mock(),
    )


def request(user_id=42, roles=()):
    return SimpleNamespace(
        id=999, channel_id=777, guild=SimpleNamespace(id=123),
        user=SimpleNamespace(
            id=user_id, display_name="Ace", roles=[SimpleNamespace(id=role) for role in roles],
        ),
        channel=None, message=None,
        response=SimpleNamespace(
            defer=AsyncMock(), send_message=AsyncMock(), send_modal=AsyncMock(), edit_message=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock(), edit_message=AsyncMock()),
    )


def parsed():
    return {"units": 2, "team_name": "Home", "legs": [{"selection": "Home ML", "odds": 150}], "image_url": "https://cdn/slip"}


def message(channel_id=777):
    return SimpleNamespace(
        id=8, author=SimpleNamespace(id=42, bot=False, mention="<@42>"), guild=object(),
        channel=SimpleNamespace(id=channel_id, send=AsyncMock()), content="2u",
        attachments=[SimpleNamespace(content_type="image/png", url="https://cdn/slip")],
    )


def test_manual_record_preserves_create_publish_link_confirm_order():
    cog = submission()
    events = []
    cog.official.create_play_record.side_effect = lambda **kwargs: events.append("create") or {"play_id": 7}
    cog.official.attach_message_id.side_effect = lambda *args: events.append("link")

    async def publish(*args):
        events.append("publish")
        return SimpleNamespace(id=101)

    async def confirm(*args):
        events.append("confirm")

    async def announce(*args):
        events.append("announce")

    cog.publish_play.side_effect = publish
    cog.confirm_recorded.side_effect = confirm
    cog.announce_play.side_effect = announce
    interaction = request()
    asyncio.run(cog.record_modal_play(interaction, 2, 1, [150], "Home", "Home ML", parsed()["legs"]))
    assert events == ["create", "publish", "link", "confirm", "announce"]
    assert cog.official.create_play_record.call_args.kwargs["discord_user_id"] == "42"
    cog.publish_play.assert_awaited_once_with(interaction, {"play_id": 7}, ("OFFICIAL_CHANNEL_ID", 777))
    cog.official.attach_message_id.assert_called_once_with(7, 101)


@pytest.mark.parametrize("failure", ["database", "validation", "publish"])
def test_manual_record_failure_does_not_claim_success(failure):
    cog = submission()
    if failure == "database":
        cog.official.create_play_record.side_effect = RuntimeError("database unavailable")
    elif failure == "validation":
        cog.official.create_play_record.return_value = {"error": "Invalid units"}
    else:
        cog.publish_play.side_effect = RuntimeError("channel unavailable")
    interaction = request()
    asyncio.run(cog.record_modal_play(interaction, 2, 1, [150], "", "", []))
    assert interaction.followup.send.call_args.kwargs["ephemeral"]
    cog.official.attach_message_id.assert_not_called()
    cog.confirm_recorded.assert_not_awaited()
    cog.announce_play.assert_not_awaited()


@pytest.mark.parametrize("deny", ["guild", "role", "channel"])
def test_play_command_preserves_permissions_and_channel(monkeypatch, deny):
    monkeypatch.setattr(module, "OFFICIAL_ROLE_IDS", {88})
    monkeypatch.setattr(module, "OFFICIAL_CHANNEL_ID", 777)
    cog = submission()
    interaction = request(roles=[88])
    if deny == "guild":
        interaction.guild = None
    elif deny == "role":
        interaction.user.roles = []
    else:
        interaction.channel_id = 888
    asyncio.run(cog.play_command.callback(cog, interaction))
    interaction.response.send_modal.assert_not_awaited()
    assert interaction.response.send_message.call_args.kwargs["ephemeral"]


def test_play_command_opens_cog_owned_modal(monkeypatch):
    monkeypatch.setattr(module, "OFFICIAL_ROLE_IDS", {88})
    monkeypatch.setattr(module, "OFFICIAL_CHANNEL_ID", 777)
    cog = submission()
    interaction = request(roles=[88])
    asyncio.run(cog.play_command.callback(cog, interaction))
    modal = interaction.response.send_modal.call_args.args[0]
    assert isinstance(modal, PlayModal) and modal.submission is cog


@pytest.mark.parametrize("legs", [1, 3])
def test_manual_modal_saves_first_leg_and_continues_or_records(legs):
    cog = submission()
    cog.record_modal_play = AsyncMock()
    cog.official.get_draft_legs.return_value = [{"leg_number": 1, "selection": "Home", "odds": 150}]
    interaction = request()

    async def check():
        modal = PlayModal(cog)
        for name, value in {
            "units": "2", "legs": str(legs), "leg_one": "Home", "leg_one_odds": "+150", "team_name": "Home",
        }.items():
            getattr(modal, name)._value = value
        await modal.on_submit(interaction)

    asyncio.run(check())
    args = cog.official.save_draft_leg.call_args.args
    assert args[1:] == ("42", 2, legs, 1, "Home", 150, "Home")
    if legs == 1:
        cog.record_modal_play.assert_awaited_once()
        cog.official.clear_draft_legs.assert_called_once_with(args[0], "42")
    else:
        cog.record_modal_play.assert_not_awaited()
        view = interaction.followup.send.call_args.kwargs["view"]
        assert view.submission is cog and view.leg_number == 2
        cog.official.clear_draft_legs.assert_not_called()


def test_final_leg_records_from_database_draft_and_clears():
    cog = submission()
    cog.record_modal_play = AsyncMock()
    rows = [{"leg_number": 1, "selection": "Home", "odds": 150}, {"leg_number": 2, "selection": "Away", "odds": -110}]
    cog.official.get_draft_legs.return_value = rows
    interaction = request()

    async def check():
        modal = LegModal(cog, "draft", 2, 2, [150], "", 2, ["Home"])
        modal.leg_details._value, modal.leg_odds._value = "Away", "-110"
        await modal.on_submit(interaction)

    asyncio.run(check())
    assert cog.record_modal_play.call_args.args[3] == [150, -110]
    assert cog.record_modal_play.call_args.args[-1] is rows
    cog.official.clear_draft_legs.assert_called_once_with("draft", "42")


def test_image_intake_opens_review_without_recording(monkeypatch):
    monkeypatch.setattr(module, "IMAGE_INPUT_CHANNEL_ID", 777)
    cog = submission()
    cog.images.extract_play.return_value = parsed()
    source = message()

    async def check():
        assert not await cog.handle_message(source)

    asyncio.run(check())
    view = source.channel.send.call_args.kwargs["view"]
    assert isinstance(view, AutoImageView)
    assert view.submission is cog and view.owner_id == 42 and view.source_message_id == 8
    cog.official.create_play_record.assert_not_called()
    cog.images.extract_play.assert_called_once_with("https://cdn/slip", "2u")


def test_test_channel_consumes_message_without_recording(monkeypatch):
    monkeypatch.setattr(module, "TEST_CHANNEL_ID", 777)
    cog = submission()
    cog.get_testing.return_value = True
    cog.images.extract_play.return_value = parsed()
    source = message()
    assert asyncio.run(cog.handle_message(source))
    assert "Nothing will be recorded" in source.channel.send.call_args.args[0]
    cog.official.create_play_record.assert_not_called()


def test_test_channel_without_image_still_consumes_routing(monkeypatch):
    monkeypatch.setattr(module, "TEST_CHANNEL_ID", 777)
    cog = submission()
    cog.get_testing.return_value = True
    source = message()
    source.attachments = []
    assert asyncio.run(cog.handle_message(source))
    cog.images.extract_play.assert_not_called()


def test_other_channel_does_not_parse(monkeypatch):
    monkeypatch.setattr(module, "IMAGE_INPUT_CHANNEL_ID", 888)
    cog = submission()
    assert not asyncio.run(cog.handle_message(message()))
    cog.images.extract_play.assert_not_called()


@pytest.mark.parametrize("reposted", [True, False])
def test_image_confirmation_keeps_repost_or_source_tracking(reposted):
    cog = submission()
    cog.official.get_play_for_message.return_value = None
    repost = SimpleNamespace(id=202) if reposted else None
    cog.repost_play.return_value = repost
    interaction = request()
    interaction.channel = SimpleNamespace(fetch_message=AsyncMock(return_value=SimpleNamespace(id=8)))
    interaction.message = SimpleNamespace(delete=AsyncMock())

    async def check():
        view = ConfirmImageView(cog, 42, parsed(), 8)
        await view.confirm.callback(interaction)
        assert view.is_finished()
        await view.confirm.callback(interaction)
        cog.official.create_play_record.assert_called_once()

    asyncio.run(check())
    cog.official.attach_message_id.assert_called_once_with(7, 202 if reposted else 8)
    cog.confirm_recorded.assert_awaited_once()
    interaction.message.delete.assert_awaited_once()
    if reposted:
        cog.announce_play.assert_not_awaited()
    else:
        assert cog.announce_play.call_args.args[-1].id == 8


def test_concurrent_image_confirmation_only_creates_once():
    cog = submission()
    interaction = request()

    async def check():
        view = ConfirmImageView(cog, 42, parsed())
        await asyncio.gather(view.confirm.callback(interaction), view.confirm.callback(request()))
        cog.official.create_play_record.assert_called_once()

    asyncio.run(check())


def test_image_confirmation_rejects_other_uploader():
    cog = submission()
    stranger = request(43)

    async def check():
        view = ConfirmImageView(cog, 42, parsed())
        assert not await view.interaction_check(stranger)
        assert await view.interaction_check(request())

    asyncio.run(check())
    assert stranger.response.send_message.call_args.kwargs["ephemeral"]
    cog.official.create_play_record.assert_not_called()


def test_image_review_carries_owner_through_units_entry():
    cog = submission()
    values = parsed()
    values["units"] = None
    interaction = request()
    interaction.message = SimpleNamespace(id=303)

    async def check():
        view = AutoImageView(cog, 42, values, 8)
        await view.review_image.callback(interaction)
        modal = interaction.response.send_modal.call_args.args[0]
        assert isinstance(modal, AutoUnitsModal) and modal.submission is cog
        modal.units._value = "2"
        await modal.on_submit(interaction)
        confirmation = interaction.followup.edit_message.call_args.kwargs["view"]
        assert confirmation.owner_id == 42 and confirmation.submission is cog

    asyncio.run(check())


def test_bot_routes_submission_once_then_prefix_commands(monkeypatch):
    cog = submission()
    cog.handle_message = AsyncMock(return_value=False)
    monkeypatch.setattr(bot_module.bot, "get_cog", lambda name: cog)
    monkeypatch.setattr(bot_module.bot, "process_commands", AsyncMock())
    monkeypatch.setattr(bot_module, "member_bet_vault", None)
    source = message()
    asyncio.run(bot_module.on_message(source))
    cog.handle_message.assert_awaited_once_with(source)
    bot_module.bot.process_commands.assert_awaited_once_with(source)
    cog.handle_message.return_value = True
    bot_module.bot.process_commands.reset_mock()
    asyncio.run(bot_module.on_message(source))
    bot_module.bot.process_commands.assert_not_awaited()


def test_image_extraction_failure_notifies_and_alerts(monkeypatch):
    monkeypatch.setattr(module, "IMAGE_INPUT_CHANNEL_ID", 777)
    cog = submission()
    cog.images.extract_play.side_effect = RuntimeError("unreadable")
    source = message()
    source.jump_url = "https://discord/message"
    assert not asyncio.run(cog.handle_message(source))
    assert "could not read" in source.channel.send.call_args.args[0]
    cog.staff_alert.assert_awaited_once()
    cog.official.create_play_record.assert_not_called()
