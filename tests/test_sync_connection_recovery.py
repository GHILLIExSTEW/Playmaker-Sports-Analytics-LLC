from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from src.services import api_sports_service as nfl_module
from src.services import supabase_service as db_module
from src.services.api_sports_service import ApiSportsService
from src.services.member_bet_service import MemberBetService


class Query:
    def __init__(self, client, table):
        self.client, self.table = client, table

    def __getattr__(self, name):
        def chain(*args, **kwargs):
            self.client.calls.append((self.table, name, args, kwargs))
            return self
        return chain

    def execute(self):
        self.client.executions.append(self.table)
        if self.client.disconnected:
            raise httpx.RemoteProtocolError("Server disconnected")
        return SimpleNamespace(data=self.client.rows)


class Client:
    def __init__(self, disconnected, rows=None):
        self.disconnected = disconnected
        self.rows = rows or []
        self.calls, self.executions = [], []

    def table(self, table):
        return Query(self, table)


def connection(monkeypatch, rows=None, permanent=False):
    old, new = Client(True), Client(permanent, rows)
    monkeypatch.setattr(db_module.supabase_service, "client", old)
    factory = Mock(return_value=new)
    monkeypatch.setattr(db_module, "create_client", factory)
    return old, new, factory


@pytest.mark.parametrize("operation", ["daily_gate", "live_gate", "upsert", "sync_record"])
def test_nfl_database_operations_rebuild_query_on_reconnected_client(monkeypatch, operation):
    now = datetime(2026, 10, 10, 3, tzinfo=timezone.utc)
    rows = [{"success": True, "last_success_at": now.isoformat(), "status_short": "Q1"}]
    old, new, factory = connection(monkeypatch, rows)
    service = ApiSportsService(clock=lambda: now)
    if operation == "daily_gate":
        assert service._already_synced_today()
    elif operation == "live_gate":
        assert service.should_sync_live_scores()
    elif operation == "upsert":
        service._upsert("api_sports_nfl_games", [{"game_id": 1}], "game_id")
    else:
        service._record_sync("daily", now, True)
    assert len(old.executions) == len(new.executions) == 1
    factory.assert_called_once()
    assert old.executions == new.executions
    assert old.calls[0][1] == new.calls[0][1]


@pytest.mark.parametrize("method", ["pending", "dirty"])
def test_vault_reconciliation_reads_recover_without_becoming_empty(monkeypatch, method):
    rows = [{"id": 7, "status": "open"}]
    old, new, factory = connection(monkeypatch, rows)
    service = MemberBetService(vision=Mock())
    assert getattr(service, method)(123) == rows
    assert old.executions == new.executions == ["member_bets"]
    assert ("member_bets", "eq", ("channel_id", "123"), {}) in new.calls
    factory.assert_called_once()


@pytest.mark.parametrize("method", ["pending", "dirty"])
def test_vault_persistent_disconnect_is_raised_after_bounded_attempts(monkeypatch, method):
    old, new, factory = connection(monkeypatch, permanent=True)
    with pytest.raises(httpx.RemoteProtocolError, match="Server disconnected"):
        getattr(MemberBetService(vision=Mock()), method)(123)
    assert len(old.executions) + len(new.executions) == 3
    assert factory.call_count == 2


def test_nfl_live_storage_retry_does_not_repeat_provider_or_budget_reservation(monkeypatch):
    old, new, factory = connection(monkeypatch)
    budget = Mock()
    monkeypatch.setattr(nfl_module, "reserve_request", budget)
    response = Mock()
    response.json.return_value = {
        "response": [{
            "game": {"id": 1, "date": {"date": "2026-10-09", "time": "20:00"}, "status": {}},
            "teams": {"home": {}, "away": {}},
            "scores": {"home": {}, "away": {}}, "league": {},
        }],
    }
    get = Mock(return_value=response)
    service = ApiSportsService(api_key="test", get=get)
    outcome = service.sync_live_scores()
    assert outcome["games"] == 1 and outcome["request_count"] == 1
    get.assert_called_once()
    budget.assert_called_once()
    assert old.executions == ["api_sports_nfl_games"]
    assert new.executions == ["api_sports_nfl_games", "api_sports_sync_state"]
    factory.assert_called_once()


def test_nfl_persistent_read_disconnect_does_not_spend_provider_requests(monkeypatch):
    connection(monkeypatch, permanent=True)
    get = Mock()
    service = ApiSportsService(api_key="test", get=get)
    with pytest.raises(httpx.RemoteProtocolError):
        service.sync_daily()
    get.assert_not_called()
