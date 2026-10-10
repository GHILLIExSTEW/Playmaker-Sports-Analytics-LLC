from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import asyncio

import pytest

from src.services import website_capper_service
import src.website_sync as website_module


def test_sync_uses_configured_operator_role_and_exact_roster(monkeypatch):
    client = Mock()
    client.rpc.return_value.execute.return_value = SimpleNamespace(data=True)
    monkeypatch.setattr(website_capper_service.supabase_service, "_ensure_client", lambda: client)
    website_capper_service.sync_website_capper_roster(123, 1328120848992960543, {222, 111})
    client.rpc.assert_called_once_with("sync_website_capper_roster", {
        "p_guild_id": "123",
        "p_role_id": "1328120848992960543",
        "p_discord_user_ids": ["111", "222"],
    })


def test_empty_verified_roster_is_synced(monkeypatch):
    client = Mock()
    client.rpc.return_value.execute.return_value = SimpleNamespace(data=True)
    monkeypatch.setattr(website_capper_service.supabase_service, "_ensure_client", lambda: client)
    website_capper_service.sync_website_capper_roster(123, 1328120848992960543, set())
    assert client.rpc.call_args.args[1]["p_discord_user_ids"] == []


@pytest.mark.parametrize("guild_id,role_id,members", [(0, 1, set()), (1, 0, set()), (1, 1, {0})])
def test_invalid_configuration_is_rejected(guild_id, role_id, members):
    with pytest.raises(ValueError):
        website_capper_service.sync_website_capper_roster(guild_id, role_id, members)


def test_unconfirmed_sync_is_an_error(monkeypatch):
    client = Mock()
    client.rpc.return_value.execute.return_value = SimpleNamespace(data=False)
    monkeypatch.setattr(website_capper_service.supabase_service, "_ensure_client", lambda: client)
    with pytest.raises(RuntimeError, match="not confirmed"):
        website_capper_service.sync_website_capper_roster(123, 1328120848992960543, {111})


def test_owner_sync_uses_exact_confirmed_role_and_empty_roster(monkeypatch):
    client = Mock()
    client.rpc.return_value.execute.return_value = SimpleNamespace(data=True)
    monkeypatch.setattr(website_capper_service.supabase_service, "_ensure_client", lambda: client)
    website_capper_service.sync_website_owner_roster(123, {999, 888})
    client.rpc.assert_called_with("sync_website_owner_roster", {
        "p_guild_id": "123", "p_role_id": "1347741218158678097", "p_discord_user_ids": ["888", "999"],
    })
    website_capper_service.sync_website_owner_roster(123, set())
    assert client.rpc.call_args.args[1]["p_discord_user_ids"] == []
    client.rpc.return_value.execute.return_value = SimpleNamespace(data=False)
    with pytest.raises(RuntimeError, match="not confirmed"):
        website_capper_service.sync_website_owner_roster(123, {999})


@pytest.mark.parametrize("owners", [{999, 888}, set()])
def test_bot_syncs_verified_owner_role_in_existing_loop(monkeypatch, owners):
    role = SimpleNamespace(members=[SimpleNamespace(id=member_id) for member_id in owners])
    guild = SimpleNamespace(id=123, chunked=False, chunk=AsyncMock(),
                            get_role=lambda role_id: role if role_id == 1347741218158678097 else None)
    monkeypatch.setattr(website_module, "GUILD_ID", 123)
    capper_sync = Mock()
    owner_sync = Mock()
    monkeypatch.setattr(website_module, "sync_website_capper_roster", capper_sync)
    monkeypatch.setattr(website_module, "sync_website_owner_roster", owner_sync)
    cog = website_module.WebsiteSync(
        SimpleNamespace(get_guild=lambda guild_id: guild),
        tracked_operators=AsyncMock(return_value={111}), play_post_target=Mock(),
        resolve_channel=AsyncMock(), can_manage_plays=Mock(), staff_alert=AsyncMock(),
    )
    asyncio.run(cog.website_capper_sync.coro(cog))
    guild.chunk.assert_awaited_once_with(cache=True)
    owner_sync.assert_called_once_with(123, owners)
    capper_sync.assert_called_once()


def test_missing_owner_role_alerts_without_syncing_empty_roster(monkeypatch):
    guild = SimpleNamespace(id=123, chunked=True, get_role=lambda role_id: None)
    monkeypatch.setattr(website_module, "GUILD_ID", 123)
    monkeypatch.setattr(website_module, "sync_website_capper_roster", Mock())
    owner_sync = Mock()
    alert = AsyncMock()
    monkeypatch.setattr(website_module, "sync_website_owner_roster", owner_sync)
    cog = website_module.WebsiteSync(
        SimpleNamespace(get_guild=lambda guild_id: guild),
        tracked_operators=AsyncMock(return_value={111}), play_post_target=Mock(),
        resolve_channel=AsyncMock(), can_manage_plays=Mock(), staff_alert=alert,
    )
    asyncio.run(cog.website_capper_sync.coro(cog))
    owner_sync.assert_not_called()
    assert alert.call_args.args[0] == "website_owners"
