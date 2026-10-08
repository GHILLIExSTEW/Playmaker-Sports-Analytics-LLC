from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.services import website_capper_service


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
