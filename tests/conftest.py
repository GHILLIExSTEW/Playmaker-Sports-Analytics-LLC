import pytest

@pytest.fixture
def mock_play_cards(monkeypatch):
    from io import BytesIO
    from unittest.mock import AsyncMock
    import discord
    from src.services.play_card_service import play_card_service

    mock = AsyncMock(side_effect=lambda *args, **kwargs: discord.File(BytesIO(b"generated card"), filename="playmaker-card-7.png"))
    monkeypatch.setattr(play_card_service, "file", mock)
    return mock

@pytest.fixture
def load_presentation(monkeypatch):
    import src.bot as bot_module
    from src.play_presentation import PlayPresentation

    def load(**overrides):
        kwargs = {
            "official": bot_module.official_play_service, "features": bot_module.play_features_service,
            "get_testing": bot_module.get_testing_enabled, "play_post_target": bot_module.play_post_target,
            "resolve_channel": bot_module.resolve_channel, "staff_alert": bot_module.send_staff_alert,
        }
        kwargs.update(overrides)
        cog = PlayPresentation(bot_module.bot, **kwargs)
        existing = bot_module.bot.get_cog
        monkeypatch.setattr(bot_module.bot, "get_cog", lambda name: cog if name == "PlayPresentation" else existing(name))
        return cog
    return load


@pytest.fixture
def load_bang(monkeypatch):
    import src.bot as bot_module
    from src.bang_notifications import BangNotifications

    def load(**overrides):
        kwargs = {
            "official": bot_module.official_play_service,
            "get_testing": bot_module.get_testing_enabled,
            "resolve_channel": bot_module.resolve_channel,
            "staff_alert": bot_module.send_staff_alert,
        }
        kwargs.update(overrides)
        cog = BangNotifications(bot_module.bot, **kwargs)
        existing = bot_module.bot.get_cog
        monkeypatch.setattr(bot_module.bot, "get_cog", lambda name: cog if name == "BangNotifications" else existing(name))
        return cog
    return load


@pytest.fixture
def load_settlement(monkeypatch, load_bang):
    import src.bot as bot_module
    from src.play_settlement import PlaySettlement

    def load(**overrides):
        load_bang()
        kwargs = {
            "official": bot_module.official_play_service,
            "features": bot_module.play_features_service,
            "is_official": bot_module.is_official,
            "can_manage_plays": bot_module.can_manage_plays,
            "fetch_message": bot_module.fetch_guild_message,
            "update_message": bot_module.update_play_message,
            "refresh_card": bot_module.refresh_play_card,
            "resolve_channel": bot_module.resolve_channel,
            "staff_alert": bot_module.send_staff_alert,
            "reaction_results": bot_module.REACTION_RESULTS,
            "channel_settings": bot_module.settlement_channel_settings,
            "user_can_settle": bot_module.user_can_settle,
            "bang_notifications": bot_module.send_bang_notifications,
        }
        kwargs.update(overrides)
        cog = PlaySettlement(bot_module.bot, **kwargs)
        existing = bot_module.bot.get_cog
        monkeypatch.setattr(bot_module.bot, "get_cog", lambda name: cog if name == "PlaySettlement" else existing(name))
        return cog
    return load
