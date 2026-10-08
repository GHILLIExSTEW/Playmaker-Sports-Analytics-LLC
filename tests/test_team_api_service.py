import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import src.bot as bot_module
from src.services import team_api_service as module
from src.services.api_budget_service import ApiBudgetDenied
from src.services.team_api_service import TeamApiService, TeamRefreshFailed


class Query:
    def __init__(self, db, table):
        self.db, self.table = db, table

    def upsert(self, rows, **kwargs):
        self.db.writes.append((self.table, rows, kwargs))
        return self

    def execute(self):
        return SimpleNamespace(data=[])


class DB:
    def __init__(self):
        self.writes = []

    def _ensure_client(self):
        return self

    def table(self, table):
        return Query(self, table)


def game(game_id=1, status="FT", date="2026-09-01"):
    return {
        "game": {"id": game_id, "date": {"date": date, "time": "20:00"}, "status": {"short": status}},
        "league": {"id": 1, "season": 2026},
        "teams": {"home": {"id": 10, "name": "Home"}, "away": {"id": 20, "name": "Away"}},
        "scores": {"home": {"total": 24}, "away": {"total": 14}},
    }


def fixture_service(monkeypatch, responses=None):
    monkeypatch.setattr(module, "reserve_request", Mock())
    calls = []
    payloads = {
        "teams": [{"id": 10, "name": "Home"}, {"id": 20, "name": "Away"}],
        "teams/statistics": {"team": {"id": 10}, "touchdowns": {"passing": 5}, "missing": None},
        "games": [game(), game(2, "NS", "2026-11-01"), game(3, "CANC")],
        "games/statistics/teams": [{"team": {"id": 10}, "statistics": [{"name": "yards", "value": "250"}]}],
        "games/statistics/players": [{"team": {"id": 10, "name": "Home"}, "groups": [{
            "name": "Passing", "players": [{"player": {"id": 99, "name": "Player"},
                                          "statistics": [{"name": "yards", "value": 250}]}],
        }]}],
    }
    payloads.update(responses or {})

    def get(url, **kwargs):
        endpoint = url.split(".io/")[1]
        calls.append((endpoint, kwargs["params"]))
        payload = payloads[endpoint]
        if isinstance(payload, Exception):
            raise payload
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"response": payload, "errors": []})

    db = DB()
    service = TeamApiService(db, get=get, api_key="test", budget_enabled=True,
                             clock=lambda: datetime(2026, 10, 1, tzinfo=timezone.utc))
    return service, db, calls


def test_refresh_preserves_all_fields_and_updates_existing_schedule_player_caches(monkeypatch):
    service, db, calls = fixture_service(monkeypatch)
    report = service.refresh("nfl", "1", "10", "2026", 1)
    assert report["complete"] and report["requests"] == 5
    assert report["games"] == 3 and report["player_games"] == 1
    assert [endpoint for endpoint, _ in calls] == ["teams", "teams/statistics", "games", "games/statistics/teams", "games/statistics/players"]
    snapshots = [row for table, row, _ in db.writes if table == "api_sports_team_stats_cache"]
    assert snapshots[0]["payload"]["touchdowns"] == {"passing": 5}
    assert snapshots[0]["payload"]["missing"] is None
    assert {table for table, _, _ in db.writes} >= {"api_sports_nfl_games", "api_sports_player_game_stats"}


@pytest.mark.parametrize("sport,league,team,season", [
    ("mma", "1", "10", "2026"), ("formula-1", "1", "10", "2026"), ("nfl", "2", "10", "2026"),
    ("nfl", "1", "zero", "2026"), ("basketball", "1", "10", "2026-2028"),
])
def test_invalid_scope_makes_no_paid_requests(monkeypatch, sport, league, team, season):
    service, db, calls = fixture_service(monkeypatch)
    with pytest.raises(ValueError):
        service.refresh(sport, league, team, season, 1)
    assert calls == [] and db.writes == []


def test_wrong_team_and_wrong_game_scope_never_store_mislabeled_stats(monkeypatch):
    service, db, _ = fixture_service(monkeypatch, {"teams": [{"id": 88, "name": "Other"}]})
    with pytest.raises(TeamRefreshFailed):
        service.refresh("nfl", "1", "10", "2026", 1)
    assert db.writes == []
    service, db, _ = fixture_service(monkeypatch, {"games": [{**game(), "league": {"id": 2, "season": 2026}}]})
    with pytest.raises(TeamRefreshFailed):
        service.refresh("nfl", "1", "10", "2026", 1)
    assert not any(table == "api_sports_nfl_games" for table, _, _ in db.writes)


def test_quota_failure_reports_partial_and_keeps_previous_saved_components(monkeypatch):
    service, db, _ = fixture_service(monkeypatch)
    count = 0

    def reserve(_base):
        nonlocal count
        count += 1
        if count == 5:
            raise ApiBudgetDenied("System daily allowance exhausted")

    monkeypatch.setattr(module, "reserve_request", reserve)
    with pytest.raises(TeamRefreshFailed) as result:
        service.refresh("nfl", "1", "10", "2026", 1)
    report = result.value.report
    assert not report["complete"] and report["quota_denied"]
    assert report["requests"] == 4 and report["player_games"] == 0
    assert any(table == "api_sports_nfl_games" for table, _, _ in db.writes)
    assert not any(table == "api_sports_player_game_stats" for table, _, _ in db.writes)
    assert not module.refresh_lock.locked()


def test_provider_pagination_is_complete_or_fails_without_partial_result(monkeypatch):
    service, _, _ = fixture_service(monkeypatch)
    calls = []

    def get(_url, **kwargs):
        page = kwargs["params"].get("page", 1)
        calls.append(page)
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {
            "response": [{"id": page}], "paging": {"current": page, "total": 2},
        })

    service.get = get
    report = {"requests": 0, "deadline": float("inf")}
    assert service.request("base", "endpoint", {}, report) == [{"id": 1}, {"id": 2}]
    assert calls == [1, 2]
    service.get = lambda *_args, **_kwargs: SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"response": [{"id": 1}], "paging": {"current": 1, "total": 2}},
    )
    with pytest.raises(RuntimeError, match="inconsistent"):
        service.request("base", "endpoint", {}, report)


@pytest.mark.parametrize("sport", ["ncaa", "football", "basketball", "baseball", "hockey", "rugby", "handball", "volleyball"])
def test_other_team_sports_route_to_correct_cache_and_report_player_coverage(monkeypatch, sport):
    if sport == "ncaa":
        row = {**game(), "league": {"id": 2, "season": 2026}}
    elif sport == "football":
        row = {
            "fixture": {"id": 1, "date": "2026-09-01T20:00:00+00:00", "status": {"short": "FT"}},
            "league": {"id": 2, "season": 2026},
            "teams": game()["teams"], "goals": {"home": 1, "away": 0},
        }
    else:
        row = {
            "id": 1, "date": "2026-09-01T20:00:00+00:00", "status": {"short": "FT"},
            "league": {"id": 2, "season": 2026}, "teams": game()["teams"], "scores": {},
        }
    extras = {"games": [row], "fixtures": [row]}
    if sport == "football":
        extras["fixtures/statistics"] = [{"team": {"id": 10}, "statistics": []}]
        extras["fixtures/players"] = [{"team": {"id": 10, "name": "Home"}, "players": [{
            "player": {"id": 99, "name": "Player"}, "statistics": [{"passes": {"total": 5}}],
        }]}]
    if sport == "basketball":
        extras["games/statistics/players"] = [{
            "game": {"id": 1}, "team": {"id": 10}, "player": {"id": 99, "name": "Player"}, "points": 5,
        }]
    service, db, calls = fixture_service(monkeypatch, extras)
    result = service.refresh(sport, "2", "10", "2026", 1)
    assert result["complete"]
    assert any(table == "api_sports_events" for table, _, _ in db.writes)
    events = next(rows for table, rows, _ in db.writes if table == "api_sports_events")
    assert events[0]["start_at"] == "2026-09-01T20:00:00+00:00"
    assert result["player_supported"] == (sport in module.PLAYER_SUPPORTED)
    player_calls = [endpoint for endpoint, _ in calls if "players" in endpoint]
    assert len(player_calls) == (1 if sport in module.PLAYER_SUPPORTED else 0)


def test_wrong_player_team_does_not_overwrite_existing_player_cache(monkeypatch):
    service, db, _ = fixture_service(monkeypatch, {
        "games/statistics/players": [{"team": {"id": 888, "name": "Wrong"}, "groups": []}],
    })
    with pytest.raises(TeamRefreshFailed, match="different team"):
        service.refresh("nfl", "1", "10", "2026", 1)
    assert not any(table == "api_sports_player_game_stats" for table, _, _ in db.writes)


def test_missing_schedule_date_is_not_replaced_with_sync_time(monkeypatch):
    row = {**game(), "game": {"id": 1, "status": {"short": "FT"}}}
    service, db, _ = fixture_service(monkeypatch, {"games": [row]})
    with pytest.raises(TeamRefreshFailed, match="no date was invented"):
        service.refresh("nfl", "1", "10", "2026", 1)
    assert not any(table == "api_sports_nfl_games" for table, _, _ in db.writes)


def test_empty_player_data_is_counted_as_unavailable_not_zero(monkeypatch):
    service, _, _ = fixture_service(monkeypatch, {"games/statistics/players": [], "teams/statistics": []})
    report = service.refresh("nfl", "1", "10", "2026", 1)
    assert report["empty_player_games"] == 1 and report["empty_team_summary"]


def test_budget_disabled_and_concurrent_refresh_are_explicit(monkeypatch):
    service, _, calls = fixture_service(monkeypatch)
    service.budget_enabled = False
    with pytest.raises(ValueError, match="budget"):
        service.refresh("nfl", "1", "10", "2026", 1)
    service.budget_enabled = True
    with module.refresh_lock:
        with pytest.raises(ValueError, match="running"):
            service.refresh("nfl", "1", "10", "2026", 1)
    assert not calls


def test_owner_only_command_permissions_and_failure_message(monkeypatch):
    monkeypatch.setattr(bot_module, "GUILD_ID", 123)
    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=123), user=SimpleNamespace(id=1, roles=[], bot=False),
        response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    refresh = Mock()
    monkeypatch.setattr(bot_module.team_api_service, "refresh", refresh)
    asyncio.run(bot_module.api_command.callback(interaction, "nfl", "2026"))
    assert not refresh.called
    interaction.user.roles = [SimpleNamespace(id=bot_module.WEBSITE_OWNER_ROLE_ID)]
    report = {"complete": False, "requests": 1, "snapshots": 0, "games": 0, "player_games": 0,
              "empty_player_games": 0, "player_supported": True, "quota_denied": True, "error": "quota"}
    refresh.side_effect = TeamRefreshFailed(report)
    asyncio.run(bot_module.run_api_refresh(interaction, "nfl", "1", "10", "2026"))
    message = interaction.followup.send.call_args.args[0]
    assert "PARTIAL / FAILED" in message and "budget denied" in message
    assert interaction.followup.send.call_args.kwargs["ephemeral"]
    interaction.guild.id = 456
    assert not bot_module.can_refresh_api(interaction)
    interaction.guild.id = 123
    interaction.user.bot = True
    assert not bot_module.can_refresh_api(interaction)


def test_picker_loads_team_names_without_requiring_ids(monkeypatch):
    service, db, calls = fixture_service(monkeypatch)
    assert service.picker_directory("nfl", "2026") == [("NFL", "1")]
    assert not calls
    assert service.picker_directory("nfl", "2026", "1") == [("Away", "20"), ("Home", "10")]
    assert calls == [("teams", {"league": 1, "season": "2026"})]
    assert db.writes[0][0] == "api_sports_team_directory"
    with pytest.raises(ValueError):
        service.picker_directory("nfl", "2026-2027")


def test_provider_league_picker_and_nested_soccer_team_names(monkeypatch):
    service, _, _ = fixture_service(monkeypatch, {
        "leagues": [{"league": {"id": 39, "name": "Premier League"}}],
        "teams": [{"team": {"id": 50, "name": "Manchester City"}}],
    })
    assert service.picker_directory("football", "2026") == [("Premier League", "39")]
    assert service.picker_directory("football", "2026", "39") == [("Manchester City", "50")]


def test_picker_paginated_buttons_and_owner_rechecks(monkeypatch):
    monkeypatch.setattr(bot_module, "GUILD_ID", 123)

    async def run():
        rows = [(f"Team {i:02}", str(i + 1)) for i in range(32)]
        picker = bot_module.ApiPickerView(7, "nfl", "2026", rows, "1")
        assert len(picker.children[0].options) == 25
        assert picker.previous.disabled and not picker.next.disabled
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=123),
            user=SimpleNamespace(id=7, roles=[SimpleNamespace(id=bot_module.WEBSITE_OWNER_ROLE_ID)], bot=False),
            response=SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock()),
        )
        assert await picker.interaction_check(interaction)
        await picker.next.callback(interaction)
        assert picker.page == 1 and len(picker.children[0].options) == 7
        assert picker.next.disabled
        interaction.user.roles = []
        assert not await picker.interaction_check(interaction)
        interaction.user.roles = [SimpleNamespace(id=bot_module.WEBSITE_OWNER_ROLE_ID)]
        interaction.user.id = 8
        assert not await picker.interaction_check(interaction)

    asyncio.run(run())


def test_command_opens_private_league_picker_and_team_selection_refreshes(monkeypatch):
    monkeypatch.setattr(bot_module, "GUILD_ID", 123)
    directory = Mock(side_effect=[[("NFL", "1")], [("Home", "10")]])
    monkeypatch.setattr(bot_module.team_api_service, "picker_directory", directory)
    refresh = AsyncMock()
    monkeypatch.setattr(bot_module, "run_api_refresh", refresh)

    async def run():
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=123),
            user=SimpleNamespace(id=7, roles=[SimpleNamespace(id=bot_module.WEBSITE_OWNER_ROLE_ID)], bot=False),
            response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock(), edit_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()), edit_original_response=AsyncMock(),
        )
        await bot_module.api_command.callback(interaction, "nfl", "2026")
        assert interaction.followup.send.call_args.kwargs["ephemeral"]
        picker = interaction.followup.send.call_args.kwargs["view"]
        select = picker.children[0]
        select._values = ["1"]
        await select.callback(interaction)
        directory.assert_called_with("nfl", "2026", "1")
        team_picker = interaction.edit_original_response.call_args.kwargs["view"]
        assert team_picker.children[0].options[0].label == "Home"
        team_picker.children[0]._values = ["10"]
        await team_picker.children[0].callback(interaction)
        refresh.assert_awaited_once_with(interaction, "nfl", "1", "10", "2026")

    asyncio.run(run())
