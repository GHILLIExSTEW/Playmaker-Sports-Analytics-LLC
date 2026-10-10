import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import src.bot as bot_module
from src.play_submission import ConfirmImageView, PlaySubmission


class CapturingChannel:
    def __init__(self):
        self.sent = []

    async def send(self, **kwargs):
        self.sent.append(kwargs)


def test_confirmation_uses_dedicated_channel(monkeypatch, load_settlement):
    confirmation_channel = CapturingChannel()
    requested_channel_ids = []

    async def fetch_channel(channel_id):
        requested_channel_ids.append(channel_id)
        return confirmation_channel

    monkeypatch.setattr(bot_module, "CONFIRMATION_CHANNEL_ID", 101)
    monkeypatch.setattr(bot_module.bot, "get_channel", lambda channel_id: None)
    monkeypatch.setattr(bot_module.bot, "fetch_channel", fetch_channel)
    monkeypatch.setattr(bot_module.official_play_service, "get_play_legs", lambda play_id: [])
    monkeypatch.setattr(bot_module, "send_insight_request", AsyncMock(return_value=False))

    followup = SimpleNamespace(sent=[])

    async def send_followup(content=None, **kwargs):
        kwargs["content"] = content
        followup.sent.append(kwargs)

    followup.send = send_followup
    interaction = SimpleNamespace(user=SimpleNamespace(id=1, display_name="Test User"), followup=followup)
    payload = {"play_id": 1, "summary": "Test play", "units": 1, "legs": 1, "odds": -110, "to_win": 0.91}

    load_settlement()
    asyncio.run(bot_module.send_confirmation_message(interaction, payload))

    assert requested_channel_ids == []
    assert confirmation_channel.sent == []
    assert len(followup.sent) == 1
    assert followup.sent[0]["content"] == "Bet recorded: Play #1"
    assert followup.sent[0]["ephemeral"] is True


def test_confirming_recorded_image_does_not_create_another_play(monkeypatch):
    submission = PlaySubmission(
        official=bot_module.official_play_service, get_testing=Mock(),
        is_tracked_operator=Mock(), publish_play=AsyncMock(), official_post_target=Mock(),
        confirm_recorded=AsyncMock(), repost_play=AsyncMock(), announce_play=AsyncMock(), staff_alert=AsyncMock(),
    )
    create_play = Mock()
    monkeypatch.setattr(bot_module.official_play_service, "get_play_for_message", lambda message_id: {"id": 42})
    monkeypatch.setattr(bot_module.official_play_service, "create_play_record", create_play)

    replies = []

    async def defer(**kwargs):
        pass

    async def send(content, **kwargs):
        replies.append((content, kwargs))

    async def confirm():
        view = ConfirmImageView(submission, 42, {"legs": [{"odds": -110}]}, source_message_id=123)
        interaction = SimpleNamespace(response=SimpleNamespace(defer=defer), followup=SimpleNamespace(send=send))
        await view.confirm.callback(interaction)
    asyncio.run(confirm())

    create_play.assert_not_called()
    assert replies == [("This image has already been recorded.", {"ephemeral": True})]


def test_play_card_posts_to_confirmation_channel_not_interaction_channel(monkeypatch, load_presentation):
    target_channel = CapturingChannel()
    interaction_channel = CapturingChannel()
    requested_channel_ids = []

    async def fetch_channel(channel_id):
        requested_channel_ids.append(channel_id)
        return target_channel

    monkeypatch.setattr(bot_module, "OFFICIAL_CHANNEL_ID", 303)
    monkeypatch.setattr(bot_module, "CONFIRMATION_CHANNEL_ID", 101)
    monkeypatch.setattr(bot_module, "TEST_CHANNEL_ID", 202)
    monkeypatch.setattr(bot_module, "testing_enabled", False)
    monkeypatch.setattr(bot_module.bot, "get_channel", lambda channel_id: None)
    monkeypatch.setattr(bot_module.bot, "fetch_channel", fetch_channel)
    interaction = SimpleNamespace(
        channel=interaction_channel,
        user=SimpleNamespace(
            display_name="Test User",
            display_avatar=SimpleNamespace(url="https://example.com/avatar.png"),
        ),
    )
    payload = {
        "play_id": 10,
        "summary": "1u • 1-leg • +100",
        "units": 1,
        "legs": 1,
        "odds": 100,
        "to_win": 1,
        "play_text": "Selection (+100)",
    }

    load_presentation()
    asyncio.run(bot_module.publish_play_message(interaction, payload))

    assert requested_channel_ids == [101]
    assert len(target_channel.sent) == 1
    assert interaction_channel.sent == []


def test_play_card_posts_to_test_channel_while_testing(monkeypatch, load_presentation):
    target_channel = CapturingChannel()
    requested_channel_ids = []

    async def fetch_channel(channel_id):
        requested_channel_ids.append(channel_id)
        return target_channel

    monkeypatch.setattr(bot_module, "OFFICIAL_CHANNEL_ID", 303)
    monkeypatch.setattr(bot_module, "CONFIRMATION_CHANNEL_ID", 101)
    monkeypatch.setattr(bot_module, "TEST_CHANNEL_ID", 202)
    monkeypatch.setattr(bot_module, "testing_enabled", True)
    monkeypatch.setattr(bot_module.bot, "get_channel", lambda channel_id: None)
    monkeypatch.setattr(bot_module.bot, "fetch_channel", fetch_channel)
    interaction = SimpleNamespace(
        user=SimpleNamespace(
            display_name="Test User",
            display_avatar=SimpleNamespace(url="https://example.com/avatar.png"),
        ),
    )
    payload = {
        "play_id": 11,
        "summary": "1u • 1-leg • +100",
        "units": 1,
        "legs": 1,
        "odds": 100,
        "to_win": 1,
        "play_text": "Selection (+100)",
    }

    load_presentation()
    asyncio.run(bot_module.publish_play_message(interaction, payload))

    assert requested_channel_ids == [202]
    assert len(target_channel.sent) == 1


def test_manual_play_embed_posts_to_official_channel(monkeypatch, load_presentation):
    target_channel = CapturingChannel()
    requested_channel_ids = []

    async def fetch_channel(channel_id):
        requested_channel_ids.append(channel_id)
        return target_channel

    monkeypatch.setattr(bot_module, "OFFICIAL_CHANNEL_ID", 303)
    monkeypatch.setattr(bot_module, "CONFIRMATION_CHANNEL_ID", 101)
    monkeypatch.setattr(bot_module, "TEST_CHANNEL_ID", 202)
    monkeypatch.setattr(bot_module, "testing_enabled", False)
    monkeypatch.setattr(bot_module.bot, "get_channel", lambda channel_id: None)
    monkeypatch.setattr(bot_module.bot, "fetch_channel", fetch_channel)
    interaction = SimpleNamespace(
        user=SimpleNamespace(
            display_name="Test User",
            display_avatar=SimpleNamespace(url="https://example.com/avatar.png"),
        ),
    )
    payload = {
        "play_id": 12,
        "summary": "1u • 1-leg • +100",
        "units": 1,
        "legs": 1,
        "odds": 100,
        "to_win": 1,
        "play_text": "Selection (+100)",
    }

    load_presentation()
    asyncio.run(bot_module.publish_play_message(interaction, payload, bot_module.official_post_target()))

    assert requested_channel_ids == [303]
    assert len(target_channel.sent) == 1


def test_play_embed_shows_tracking_details_without_team():
    embed = bot_module.build_play_embed({
        "play_id": 42,
        "summary": "5u • 1-leg • +115",
        "units": 5,
        "odds": 115,
        "to_win": 5.75,
        "team_name": "Incorrect Team",
        "play_text": "Leg 1: Selection (-125)",
    })

    assert embed.title == "Play #42 • Open"
    assert [field.name for field in embed.fields] == ["Units", "Odds", "To win", "Selections"]
    assert [field.value for field in embed.fields[:3]] == ["5u", "+115", "5.75u"]
    assert all(field.name != "Team" for field in embed.fields)


def test_settled_play_embed_shows_result_and_removes_legacy_team_field():
    original = bot_module.discord.Embed(title="Official Play", description="5u • 1-leg • -125")
    original.add_field(name="Team", value="Incorrect Team")
    original.add_field(name="Notes", value="Leg 1: Selection (-125)")
    message = SimpleNamespace(embeds=[original])

    updated = bot_module.build_settled_play_embed(message, 42, "win")

    assert updated.title == "Play #42 • Win"
    assert [field.name for field in updated.fields] == ["Selections"]