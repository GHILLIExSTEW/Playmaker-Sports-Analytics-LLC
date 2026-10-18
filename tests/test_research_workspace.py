import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from src import research_workspace as workspace
from src.services.api_budget_service import member_request_user


@pytest.fixture
def service(monkeypatch):
    monkeypatch.setattr(workspace, "API_SPORTS_BUDGET_ENABLED", True)
    db = Mock()
    db._execute_once.side_effect = lambda operation: operation()
    db._execute.side_effect = lambda operation: operation()
    db._ensure_client.return_value.rpc.return_value.execute.return_value.data = True
    db.select.return_value.data = [{"game_id": "1"}]
    return workspace.ResearchRefreshService(database=db, api=Mock(), multi=Mock(), players=Mock())


def request(**changes):
    return {"id": "request", "competition": "nfl", "game_id": "1", "kind": "odds",
            "user_id": "user", "discord_user_id": "123", **changes}


def test_budget_disabled_and_revoked_access_never_call_provider(service, monkeypatch):
    monkeypatch.setattr(workspace, "API_SPORTS_BUDGET_ENABLED", False)
    with pytest.raises(workspace.ApiBudgetDenied):
        service.run(request())
    monkeypatch.setattr(workspace, "API_SPORTS_BUDGET_ENABLED", True)
    service.db._ensure_client.return_value.rpc.return_value.execute.return_value.data = False
    with pytest.raises(PermissionError):
        service.run(request())
    service.multi._request.assert_not_called()


def test_odds_only_store_selected_game_and_attribute_budget(service):
    def response(*args):
        assert member_request_user.get() == "123"
        return [{"game": {"id": 2}, "bookmakers": [{"name": "Wrong"}]},
                {"game": {"id": 1}, "bookmakers": [{"name": "Correct"}]}]
    service.multi._request.side_effect = response
    assert "Refreshed odds" in service.run(request())
    assert service.db.upsert.call_args.args[1]["bookmakers"] == [{"name": "Correct"}]
    assert member_request_user.get() is None


def test_empty_or_wrong_game_does_not_overwrite_cached_odds(service):
    for payload in [[], [{"game": {"id": 2}, "bookmakers": []}]]:
        service.multi._request.return_value = payload
        with pytest.raises(ValueError, match="No sportsbook odds"):
            service.run(request())
    service.db.upsert.assert_not_called()
    assert member_request_user.get() is None


def test_players_reuse_verified_adapter(service):
    service.players.refresh.return_value = "Updated"
    assert service.run(request(kind="players")) == "Updated"
    service.players.refresh.assert_called_once_with("nfl", 1, 123)
    service.multi._request.assert_not_called()


def test_game_refresh_checks_id_and_league(service, monkeypatch):
    service.api._request.return_value = [{}]
    monkeypatch.setattr(workspace, "normalize_game", lambda row, now: {"game_id": 1, "league_id": 2})
    with pytest.raises(ValueError, match="configured league"):
        service.run(request(kind="game"))
    service.db.upsert.assert_not_called()
    monkeypatch.setattr(workspace, "normalize_game", lambda row, now: {"game_id": 1, "league_id": 1})
    service.run(request(kind="game"))
    assert service.db.upsert.call_args.args[0] == "api_sports_nfl_games"


def test_paginated_odds_reserve_a_bounded_batch(service, monkeypatch):
    from contextlib import contextmanager
    reservations = []

    @contextmanager
    def batch(base, user, count):
        reservations.append((base, user, count))
        yield
    monkeypatch.setattr(workspace, "member_request_batch", batch)
    service.multi._request_page.side_effect = [([], 99)] * 5
    with pytest.raises(ValueError, match="No sportsbook odds"):
        service.run(request(competition="basketball:12"))
    assert reservations[0][1:] == (123, 5)
    assert service.multi._request_page.call_count == 5
    assert all("game" not in call.args[2] for call in service.multi._request_page.call_args_list)


def test_queue_reports_failure_and_never_retries_provider(service):
    client = service.db._ensure_client.return_value
    rpc_calls = []

    def rpc(name, args):
        rpc_calls.append((name, args))
        data = [request()] if name == "claim_research_refresh" else True
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=data))
    client.rpc.side_effect = rpc
    service.run = Mock(side_effect=TimeoutError("Provider timeout"))
    assert service.process_next() is True
    assert service.run.call_count == 1
    (_, claim), (finish_name, finish) = rpc_calls
    assert finish_name == "finish_research_refresh"
    assert finish == {"p_id": "request", "p_claim_token": claim["p_claim_token"],
                      "p_state": "failed", "p_result": "Provider timeout"}


def test_dropped_claim_and_finish_are_replayed_with_the_same_token(service):
    client = service.db._ensure_client.return_value
    rpc_calls, drops = [], {"claim_research_refresh": 1, "finish_research_refresh": 1}

    def execute_with_retry(operation):
        try:
            return operation()
        except httpx.TransportError:
            return operation()
    service.db._execute.side_effect = execute_with_retry

    def rpc(name, args):
        rpc_calls.append((name, args))

        def execute():
            if drops[name]:
                drops[name] -= 1
                raise httpx.RemoteProtocolError("Server disconnected")
            return SimpleNamespace(data=[request()] if name == "claim_research_refresh" else True)
        return SimpleNamespace(execute=execute)
    client.rpc.side_effect = rpc
    service.run = Mock(return_value="Refreshed")
    assert service.process_next() is True
    assert service.run.call_count == 1
    tokens = {args["p_claim_token"] for _, args in rpc_calls}
    assert [name for name, _ in rpc_calls] == ["claim_research_refresh"] * 2 + ["finish_research_refresh"] * 2
    assert len(tokens) == 1


def test_unsaved_finish_is_explicit(service):
    client = service.db._ensure_client.return_value
    client.rpc.side_effect = lambda name, args: SimpleNamespace(execute=lambda: SimpleNamespace(
        data=[request()] if name == "claim_research_refresh" else False))
    service.run = Mock(return_value="Refreshed")
    with pytest.raises(RuntimeError, match="tracking changed"):
        service.process_next()


def test_transport_failure_is_reported_then_successful_poll_reports_recovery():
    service = Mock()
    service.process_next.side_effect = [httpx.RemoteProtocolError("HTTP/2 disconnected"), False, False]
    alert = AsyncMock()
    cog = workspace.ResearchWorkspace(Mock(), staff_alert=alert, service=service)
    asyncio.run(cog.refresh_requests.coro(cog))
    assert cog.transport_failed is True
    assert alert.await_args.args[0] == "research_refresh_transport"
    assert "migration" not in alert.await_args.args[1]
    asyncio.run(cog.refresh_requests.coro(cog))
    assert cog.transport_failed is False
    assert alert.await_args.args[0] == "research_refresh_recovered"
    asyncio.run(cog.refresh_requests.coro(cog))
    assert alert.await_count == 2
    assert service.process_next.call_count == 3


def test_nontransport_worker_error_remains_explicit():
    service = Mock()
    service.process_next.side_effect = RuntimeError("missing RPC")
    alert = AsyncMock()
    cog = workspace.ResearchWorkspace(Mock(), staff_alert=alert, service=service)
    asyncio.run(cog.refresh_requests.coro(cog))
    assert alert.await_args.args[0] == "research_refresh_worker"
