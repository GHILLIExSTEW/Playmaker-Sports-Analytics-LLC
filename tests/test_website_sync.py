import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import src.bot as bot_module
import src.website_sync as module
from src.website_sync import WebsiteSync


def cog():
    return WebsiteSync(
        Mock(is_ready=Mock(return_value=False), wait_until_ready=AsyncMock()),
        tracked_operators=AsyncMock(return_value={42}),
        can_manage_plays=Mock(return_value=True),
        staff_alert=AsyncMock(),
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


def test_tracker_allowlist_counts_as_tracked_and_is_in_website_roster(monkeypatch):
    member = SimpleNamespace(id=42, bot=False, roles=[])
    role = SimpleNamespace(members=[SimpleNamespace(id=99, bot=False), SimpleNamespace(id=100, bot=True)])
    guild = SimpleNamespace(chunked=True, get_role=lambda role_id: role)
    monkeypatch.setattr(bot_module, "TRACKER_USER_IDS", {42})
    monkeypatch.setattr(bot_module, "TRACKER_ROLE_ID", 456)
    monkeypatch.setattr(bot_module, "GUILD_ID", 123)
    monkeypatch.setattr(bot_module.bot, "get_guild", lambda guild_id: guild)

    assert bot_module.is_tracked_operator(member)
    assert asyncio.run(bot_module.tracked_operator_ids()) == {42, 99}


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
    recipient = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=999)))
    monkeypatch.setattr(website.bot, "get_user", Mock(return_value=recipient))

    async def check():
        assert await asyncio.gather(website.send_insight_request(7), website.send_insight_request(7)) == [True, False]

    asyncio.run(check())
    recipient.send.assert_awaited_once()


def test_request_fetches_user_when_not_cached(monkeypatch):
    stored = {
        "discord_user_id": "42", "selection": "Home", "prompt_message_id": None, "justification": "",
    }
    monkeypatch.setattr(module.capper_insight_service, "insight_context", lambda play_id: [stored])
    monkeypatch.setattr(module.capper_insight_service, "mark_prompt", Mock())
    website = cog()
    monkeypatch.setattr(website.bot, "get_user", Mock(return_value=None))
    recipient = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=999)))
    monkeypatch.setattr(website.bot, "fetch_user", AsyncMock(return_value=recipient))
    asyncio.run(website.send_insight_request(7))
    website.bot.fetch_user.assert_awaited_once_with(42)
    recipient.send.assert_awaited_once()


def test_confirmation_bridge_uses_loaded_cog_and_missing_cog_is_error(monkeypatch):
    website = cog()
    website.send_insight_request = AsyncMock(return_value=True)
    monkeypatch.setattr(bot_module.bot, "get_cog", lambda name: website)
    assert asyncio.run(bot_module.send_insight_request(7))
    website.send_insight_request.assert_awaited_once_with(7)
    monkeypatch.setattr(bot_module.bot, "get_cog", lambda name: None)
    with pytest.raises(RuntimeError, match="not loaded"):
        asyncio.run(bot_module.send_insight_request(7))
