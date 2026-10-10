import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

import src.bot as bot_module
import src.website_sync as module
from src.website_sync import WebsiteSync


def cog():
    return WebsiteSync(
        Mock(is_ready=Mock(return_value=False), wait_until_ready=AsyncMock()),
        tracked_operators=AsyncMock(return_value={42}),
        play_post_target=Mock(return_value=("CONFIRMATION_CHANNEL_ID", 777)),
        resolve_channel=AsyncMock(), can_manage_plays=Mock(return_value=True),
        staff_alert=AsyncMock(),
    )


def click(custom_id="pm:insight:7"):
    return SimpleNamespace(
        type=discord.InteractionType.component, data={"custom_id": custom_id},
        user=SimpleNamespace(id=42),
        response=SimpleNamespace(is_done=Mock(return_value=False), send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


def test_ready_reconnect_and_unload_manage_single_roster_loop():
    website = cog()
    loop = Mock(is_running=Mock(return_value=False))
    loop.start.side_effect = lambda: setattr(loop.is_running, "return_value", True)
    website.website_capper_sync = loop

    async def check():
        await website.cog_load()
        loop.start.assert_not_called()
        await website.on_ready()
        await website.on_ready()
        website.cog_unload()

    asyncio.run(check())
    loop.start.assert_called_once()
    loop.cancel.assert_called_once()


def test_hot_load_starts_and_before_loop_waits_for_ready():
    website = cog()
    website.bot.is_ready.return_value = True
    website.start_jobs = Mock()
    asyncio.run(website.cog_load())
    website.start_jobs.assert_called_once()
    asyncio.run(website.before_website_capper_sync())
    website.bot.wait_until_ready.assert_awaited_once()
    assert website.website_capper_sync.minutes == 5


@pytest.mark.parametrize("operators", [None, set()])
def test_unverified_roster_is_not_empty_roster_and_owner_sync_is_independent(monkeypatch, operators):
    monkeypatch.setattr(module, "GUILD_ID", 123)
    monkeypatch.setattr(module, "TRACKER_ROLE_ID", 456)
    capper, owner = Mock(), Mock()
    monkeypatch.setattr(module, "sync_website_capper_roster", capper)
    monkeypatch.setattr(module, "sync_website_owner_roster", owner)
    website = cog()
    website.tracked_operators.return_value = operators
    website.bot.get_guild.return_value = SimpleNamespace(
        id=123, chunked=True, get_role=lambda role_id: SimpleNamespace(members=[]),
    )
    asyncio.run(website.website_capper_sync.coro(website))
    owner.assert_called_once_with(123, set())
    if operators is None:
        capper.assert_not_called()
        assert website.staff_alert.call_args.args[0] == "website_cappers"
    else:
        capper.assert_called_once_with(123, 456, set())
        website.staff_alert.assert_not_awaited()


def test_roster_database_failure_alerts_without_skipping_owner(monkeypatch):
    monkeypatch.setattr(module, "GUILD_ID", 123)
    monkeypatch.setattr(module, "TRACKER_ROLE_ID", 456)
    monkeypatch.setattr(module, "sync_website_capper_roster", Mock(side_effect=RuntimeError("database unavailable")))
    owner = Mock()
    monkeypatch.setattr(module, "sync_website_owner_roster", owner)
    website = cog()
    website.bot.get_guild.return_value = SimpleNamespace(
        id=123, chunked=True, get_role=lambda role_id: SimpleNamespace(members=[SimpleNamespace(id=99)]),
    )
    asyncio.run(website.website_capper_sync.coro(website))
    owner.assert_called_once_with(123, {99})
    assert website.staff_alert.call_args.args[0] == "website_cappers"


@pytest.mark.parametrize("custom_id", ["pm:tail:7", "pm:as:7:win", "unrelated", "pm:insightful:7"])
def test_insight_listener_ignores_other_feature_buttons(custom_id):
    website = cog()
    website.handle_insight_click = AsyncMock()
    request = click(custom_id)
    asyncio.run(website.on_insight_interaction(request))
    website.handle_insight_click.assert_not_awaited()
    request.response.send_message.assert_not_awaited()


def test_insight_has_one_dispatch_owner_after_extraction(load_presentation):
    website = cog()
    website.handle_insight_click = AsyncMock()
    request = click()
    presentation = load_presentation()

    async def check():
        await presentation.on_play_feature_interaction(request)
        await website.on_insight_interaction(request)

    asyncio.run(check())
    website.handle_insight_click.assert_awaited_once_with(request, 7)
    request.response.send_message.assert_not_awaited()


@pytest.mark.parametrize("custom_id", ["pm:insight:", "pm:insight:bad", "pm:insight:0", "pm:insight:7:extra"])
def test_bad_insight_buttons_fail_privately(custom_id, caplog):
    website = cog()
    website.handle_insight_click = AsyncMock()
    request = click(custom_id)
    asyncio.run(website.on_insight_interaction(request))
    website.handle_insight_click.assert_not_awaited()
    assert request.response.send_message.call_args.kwargs["ephemeral"]
    assert "capper_insight_interaction_failed" in caplog.text


def test_insight_lookup_failure_uses_followup_after_response():
    website = cog()
    website.handle_insight_click = AsyncMock(side_effect=RuntimeError("database unavailable"))
    request = click()
    request.response.is_done.return_value = True
    asyncio.run(website.on_insight_interaction(request))
    request.response.send_message.assert_not_awaited()
    assert request.followup.send.call_args.kwargs["ephemeral"]


def test_concurrent_requests_share_one_cog_lock_and_deduplicate(monkeypatch):
    stored = {
        "play_id": 7, "discord_user_id": "42", "selection": "Home ML",
        "prompt_message_id": None, "justification": "",
    }
    monkeypatch.setattr(module.capper_insight_service, "insight_context", lambda play_id: [stored])
    monkeypatch.setattr(
        module.capper_insight_service, "mark_prompt",
        lambda play_id, message_id: stored.update(prompt_message_id=str(message_id)),
    )
    website = cog()
    channel = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=999)))
    website.resolve_channel.return_value = channel

    async def check():
        assert await asyncio.gather(website.send_insight_request(7), website.send_insight_request(7)) == [True, False]

    asyncio.run(check())
    channel.send.assert_awaited_once()


def test_request_uses_dynamic_test_mode_target(monkeypatch):
    stored = {
        "discord_user_id": "42", "selection": "Home", "prompt_message_id": None, "justification": "",
    }
    monkeypatch.setattr(module.capper_insight_service, "insight_context", lambda play_id: [stored])
    monkeypatch.setattr(module.capper_insight_service, "mark_prompt", Mock())
    monkeypatch.setattr(bot_module, "testing_enabled", True)
    monkeypatch.setattr(bot_module, "TEST_CHANNEL_ID", 888)
    website = cog()
    website.play_post_target = bot_module.play_post_target
    website.resolve_channel.return_value = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=999)))
    asyncio.run(website.send_insight_request(7))
    website.resolve_channel.assert_awaited_once_with(888, "TEST_CHANNEL_ID", required=True)


def test_confirmation_bridge_uses_loaded_cog_and_missing_cog_is_error(monkeypatch):
    website = cog()
    website.send_insight_request = AsyncMock(return_value=True)
    monkeypatch.setattr(bot_module.bot, "get_cog", lambda name: website)
    assert asyncio.run(bot_module.send_insight_request(7))
    website.send_insight_request.assert_awaited_once_with(7)
    monkeypatch.setattr(bot_module.bot, "get_cog", lambda name: None)
    with pytest.raises(RuntimeError, match="not loaded"):
        asyncio.run(bot_module.send_insight_request(7))
