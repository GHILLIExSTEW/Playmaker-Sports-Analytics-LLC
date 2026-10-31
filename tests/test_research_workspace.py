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
            "user_id": "user", "discord_user_id": "123", "guild_id": "456",
            "role_id": "42", **changes}


def test_budget_disabled_and_revoked_access_never_call_provider(service, monkeypatch):
    monkeypatch.setattr(workspace, "API_SPORTS_BUDGET_ENABLED", False)
    with pytest.raises(workspace.ApiBudgetDenied):
        service.run(request())
    monkeypatch.setattr(workspace, "API_SPORTS_BUDGET_ENABLED", True)
    service.db._ensure_client.return_value.rpc.return_value.execute.return_value.data = False
    with pytest.raises(PermissionError):
        service.run(request())
    service.db._ensure_client.return_value.rpc.assert_called_once_with(
        "research_refresh_authorized",
        {
            "p_user_id": "user", "p_discord_user_id": "123",
            "p_guild_id": "456", "p_role_id": "42",
        },
    )
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


def test_refresh_request_binds_current_guild_and_tracker_role():
    db = Mock()
    db._ensure_client.return_value.rpc.return_value.execute.return_value.data = "request-id"
    data = workspace.ResearchWorkspaceData(database=db)

    assert data.request_refresh(123, "nfl", "1", "game", 456, 42) == "request-id"
    db._ensure_client.return_value.rpc.assert_called_once_with(
        "request_discord_research_refresh",
        {
            "p_discord_user_id": "123", "p_competition": "nfl", "p_game_id": "1",
            "p_kind": "game", "p_guild_id": "456", "p_role_id": "42",
        },
    )


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


def test_research_group_registers_cached_and_budgeted_tools():
    commands = {command.name for command in workspace.ResearchWorkspace.__cog_app_commands__}
    assert workspace.ResearchWorkspace.__cog_is_app_commands_group__ is True
    assert workspace.ResearchWorkspace.__cog_group_name__ == "research"
    assert commands == {
        "matchup", "players", "odds", "model", "performance", "refresh", "refresh_status",
    }


def test_member_research_gate_requires_paid_access_or_operator_override(monkeypatch):
    cog = workspace.ResearchWorkspace(Mock(), staff_alert=AsyncMock(), membership=Mock())
    cog.membership.has_stats_access.return_value = False
    cog._is_operator = AsyncMock(return_value=False)
    target = SimpleNamespace(
        user=SimpleNamespace(id=123), guild_id=456,
        response=SimpleNamespace(send_message=AsyncMock()),
    )
    monkeypatch.setattr(workspace, "WHOP_MEMBERSHIP_SYNC_ENABLED", True)
    monkeypatch.setattr(workspace, "is_stats_moderator", lambda interaction: False)
    assert asyncio.run(cog._gate(target)) is False
    assert "verified paid membership" in target.response.send_message.call_args.args[0]

    cog.membership.has_stats_access.return_value = True
    target.response.send_message.reset_mock()
    assert asyncio.run(cog._gate(target)) is True
    target.response.send_message.assert_not_awaited()


def test_operator_research_gate_does_not_accept_paid_membership():
    cog = workspace.ResearchWorkspace(Mock(), staff_alert=AsyncMock(), membership=Mock())
    cog._is_operator = AsyncMock(return_value=False)
    target = SimpleNamespace(
        user=SimpleNamespace(id=123), guild_id=456,
        response=SimpleNamespace(send_message=AsyncMock()),
    )
    assert asyncio.run(cog._gate(target, operator_only=True)) is False
    assert "OPERATOR/Owner" in target.response.send_message.call_args.args[0]
    cog.membership.has_stats_access.assert_not_called()


def test_stale_research_auto_refresh_is_capper_only(monkeypatch):
    monkeypatch.setattr(workspace, "TRACKER_ROLE_ID", 42)
    monkeypatch.setattr(workspace, "GUILD_ID", None)
    cog = workspace.ResearchWorkspace(Mock(), staff_alert=AsyncMock(), data=Mock())
    cog.data.request_refresh.return_value = "request-123"
    capper = Mock(spec=workspace.discord.Member)
    capper.id = 123
    capper.roles = [SimpleNamespace(id=42)]
    interaction = SimpleNamespace(user=capper, guild_id=456)
    game = {"competition": "nfl", "game_id": "1"}

    note = asyncio.run(cog._refresh_if_stale(
        interaction, game, "game",
        (workspace.datetime.now(workspace.timezone.utc) - workspace.timedelta(minutes=61)).isoformat(),
    ))

    assert "Refresh queued" in note
    cog.data.request_refresh.assert_called_once_with(123, "nfl", "1", "game", 456, 42)

    member = Mock(spec=workspace.discord.Member)
    member.id = 456
    member.roles = []
    non_capper = SimpleNamespace(user=member, guild_id=456)
    assert asyncio.run(cog._refresh_if_stale(non_capper, game, "game", None)) is None
    cog.data.request_refresh.assert_called_once()


def test_fresh_capper_cache_does_not_queue_refresh(monkeypatch):
    monkeypatch.setattr(workspace, "TRACKER_ROLE_ID", 42)
    monkeypatch.setattr(workspace, "GUILD_ID", None)
    cog = workspace.ResearchWorkspace(Mock(), staff_alert=AsyncMock(), data=Mock())
    capper = Mock(spec=workspace.discord.Member)
    capper.id = 123
    capper.roles = [SimpleNamespace(id=42)]
    interaction = SimpleNamespace(user=capper, guild_id=456)
    updated_at = workspace.datetime.now(workspace.timezone.utc).isoformat()

    assert asyncio.run(cog._refresh_if_stale(
        interaction, {"competition": "nfl", "game_id": "1"}, "odds", updated_at,
    )) is None
    cog.data.request_refresh.assert_not_called()


def test_auto_refresh_reports_unsupported_player_stat_leagues(monkeypatch):
    monkeypatch.setattr(workspace, "TRACKER_ROLE_ID", 42)
    monkeypatch.setattr(workspace, "GUILD_ID", None)
    cog = workspace.ResearchWorkspace(Mock(), staff_alert=AsyncMock(), data=Mock())
    capper = Mock(spec=workspace.discord.Member)
    capper.id = 123
    capper.roles = [SimpleNamespace(id=42)]
    interaction = SimpleNamespace(user=capper, guild_id=456)

    note = asyncio.run(cog._refresh_if_stale(
        interaction, {"competition": "baseball:1", "game_id": "1", "sport": "baseball"},
        "players", None,
    ))

    assert "unsupported" in note
    cog.data.request_refresh.assert_not_called()


def test_player_report_aggregates_cached_threshold_observations():
    game = {"start_at": "2026-10-10T18:00:00Z", "sport": "basketball"}
    records = [{
        "id": "12", "name": "Player One", "team": "Home",
        "logs": [
            {"date": "2026-10-08T18:00:00Z", "stats": {"Points": 18}},
            {"date": "2026-10-06T18:00:00Z", "stats": {"Points": 22}},
            {"date": "2026-10-04T18:00:00Z", "stats": {"Points": 20}},
        ],
    }]
    report = workspace.ResearchWorkspace._player_report(game, records, "Player One", "Points", 20)
    assert "average 20.00 across 3 prior observations" in report
    assert "1 over · 1 under · 1 push" in report


def test_selected_game_stats_do_not_leak_into_pre_match_sample():
    game = {"start_at": "2026-10-10T18:00:00Z", "sport": "basketball"}
    records = [{
        "id": "12", "name": "Player One", "team": "Home",
        "logs": [
            {"date": "2026-10-08T18:00:00Z", "stats": {"Points": 20}},
            {"date": "2026-10-10T18:00:00Z", "stats": {"Points": 50}, "current": True},
        ],
    }]
    report = workspace.ResearchWorkspace._player_report(game, records, "Player One", "Points", 25)
    assert "average 20.00 across 1 prior observations" in report
    assert "Selected game snapshot: 50" in report
    assert "0 over · 1 under · 0 push" in report


def test_player_report_filters_home_away_and_head_to_head_samples():
    game = {"start_at": "2026-10-10T18:00:00Z", "sport": "basketball", "home_id": "1", "away_id": "2"}
    history = [
        {"game_id": "8", "home_id": "1", "away_id": "3"},
        {"game_id": "9", "home_id": "4", "away_id": "2"},
        {"game_id": "10", "home_id": "1", "away_id": "2"},
    ]
    records = [{
        "id": "12", "name": "Player One", "team": "Home",
        "logs": [
            {"date": "2026-10-08T18:00:00Z", "stats": {"Points": 10}, "home_name": "Home", "away_name": "Other", "home_id": "1", "away_id": "3"},
            {"date": "2026-10-06T18:00:00Z", "stats": {"Points": 20}, "home_name": "Other", "away_name": "Home", "home_id": "4", "away_id": "1"},
            {"date": "2026-10-04T18:00:00Z", "stats": {"Points": 30}, "home_name": "Home", "away_name": "Away", "home_id": "1", "away_id": "2"},
        ],
    }]
    home = workspace.ResearchWorkspace._player_report(game, records, "Player One", "Points", None, "home")
    head_to_head = workspace.ResearchWorkspace._player_report(
        game, records, "Player One", "Points", None, "head-to-head",
    )
    assert "average 20.00 across 2 prior observations" in home
    assert "average 30.00 across 1 prior observations" in head_to_head


def test_player_report_explains_unavailable_league_adapter():
    game = {"start_at": "2026-10-10T18:00:00Z", "sport": "baseball"}
    assert "unavailable" in workspace.ResearchWorkspace._player_report(game, [], None, None, None)


def test_matchup_report_uses_prior_scores_and_h2h_only():
    game = {
        "competition": "nfl", "game_id": "30", "start_at": "2026-10-10T18:00:00Z",
        "home_id": "1", "home_name": "Home", "away_id": "2", "away_name": "Away",
        "status": "NS", "synced_at": "2026-10-09T18:00:00Z",
    }
    history = [
        {
            "game_id": "10", "start_at": "2026-10-01T18:00:00Z", "home_id": "1",
            "home_name": "Home", "away_id": "2", "away_name": "Away", "home_score": 24,
            "away_score": 17, "synced_at": "2026-10-02T18:00:00Z",
        },
        {
            "game_id": "11", "start_at": "2026-10-02T18:00:00Z", "home_id": "1",
            "home_name": "Home", "away_id": "3", "away_name": "Other", "home_score": 7,
            "away_score": 10, "synced_at": "2026-10-03T18:00:00Z",
        },
    ]
    embed = workspace.ResearchWorkspace._matchup_embed(
        {"game": game, "history": history}, operator_fields=[],
    )
    assert "**Last 2 scored games:** 1-1-0" in embed.fields[0].value
    assert "Recent head-to-head" in embed.fields[2].name
    assert "Other" not in embed.fields[2].value


def test_score_parser_treats_missing_score_as_unavailable():
    assert workspace._numeric_score(None) is None
