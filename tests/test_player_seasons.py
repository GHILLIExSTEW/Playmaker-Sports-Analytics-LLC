import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from src.member_stats import MemberStats
from src.services.api_budget_service import member_request_batch, reserve_request, reserved_batch, member_request_user, ApiBudgetDenied
from src.services.player_season_service import PlayerSeasonService, previous_season, basketball_totals, select_player
from tests.test_member_stats import interaction

NOW = datetime(2026, 10, 3, tzinfo=timezone.utc)


def query(rows):
    result = Mock()
    for name in ("select", "eq", "ilike", "lte", "limit", "order", "upsert"):
        getattr(result, name).return_value = result
    result.execute.return_value = SimpleNamespace(data=rows)
    return result


def service():
    tables = {
        "api_sports_player_leagues": query([{"name": "NFL", "current_season": "2026", "league_id": "1"}]),
        "api_sports_player_directory": query([{"player_id": 609, "name": "Deshaun Watson"}]),
        "api_sports_nfl_games": query([{"season": 2026}]),
        "api_sports_events": query([{"season": "2026"}]),
        "api_sports_player_seasons": query([]),
        "api_sports_player_season_metadata": query([]),
    }
    db = Mock()
    db._ensure_client.return_value.table.side_effect = tables.__getitem__
    return PlayerSeasonService(database=db, clock=lambda: NOW, api=Mock(), multi_api=Mock()), tables


def snapshot(groups=None, stamp="2026-10-02T00:00:00Z"):
    return {
        "groups": groups if groups is not None else [{"name": "Passing", "statistics": [{"name": "yards", "value": "855"}]}],
        "coverage": "Provider season totals.", "synced_at": stamp,
    }


@contextmanager
def batch(*args):
    yield


@pytest.mark.parametrize("value,expected", [("2026", "2025"), ("2026-2027", "2025-2026")])
def test_previous_season_exact_provider_formats(value, expected):
    assert previous_season(value) == expected


@pytest.mark.parametrize("value", ["2026/27", "2026-2028", "bad"])
def test_unknown_season_not_guessed(value):
    with pytest.raises(ValueError):
        previous_season(value)


def test_player_suggestions_filter_sport_league_and_escape_patterns_without_provider():
    target, tables = service()
    target.players("football", "254", "100%_")
    q = tables["api_sports_player_directory"]
    assert ("sport_slug", "football") in [call.args for call in q.eq.call_args_list]
    assert ("league_id", "254") in [call.args for call in q.eq.call_args_list]
    q.ilike.assert_called_once_with("name", "%100\\%\\_%")
    q.limit.assert_called_once_with(25)
    target.api._request.assert_not_called()
    target.multi_api._request.assert_not_called()


def test_league_suggestions_are_sport_scoped():
    target, tables = service()
    target.leagues("basketball", "NBA")
    tables["api_sports_player_leagues"].eq.assert_called_once_with("sport_slug", "basketball")
    assert target.players("nfl", "", "Wat") == []


def test_name_matching_ambiguous_never_merges_players():
    rows = [{"player_id": 1, "name": "John Smith"}, {"player_id": 2, "name": "Jane Smith"}]
    with pytest.raises(ValueError, match="ambiguous"):
        select_player(rows, "Smith")
    assert select_player(rows, "John Smith")["player_id"] == 1


def test_two_season_cached_report_requires_no_game_id_or_provider():
    target, _ = service()
    target.snapshot = Mock(return_value=snapshot())
    title, text = target.report("nfl", "1", "Watson")
    assert "2026 (current)" in text and "2025 (previous)" in text
    assert "yards: 855" in text and "current and previous season" in title
    assert len(text) < 4096
    target.api._request.assert_not_called()


def test_split_season_comes_from_league_not_calendar():
    target, tables = service()
    tables["api_sports_events"].execute.return_value.data = [{"season": "2026-2027"}]
    target.snapshot = Mock(return_value=snapshot())
    text = target.report("basketball", "63", "Watson")[1]
    assert "2026-2027 (current)" in text and "2025-2026 (previous)" in text


def test_missing_previous_is_disclosed_not_zero():
    target, _ = service()
    target.snapshot = Mock(side_effect=[snapshot(), None])
    assert "Not cached yet" in target.report("nfl", "1", "609")[1]


def test_previous_populated_is_frozen_current_and_empty_previous_expire():
    target, _ = service()
    assert target.reusable(snapshot(), True)
    assert not target.reusable(snapshot(), False)
    assert not target.reusable(snapshot(groups=[]), True)
    assert not target.reusable(snapshot(stamp="2026-10-04T00:00:00Z"), True)


def test_known_player_refreshes_only_current_not_frozen_previous():
    target, tables = service()
    target.snapshot = Mock(side_effect=lambda sport, league, player, season, metadata=False:
        {"payload": [{"id": 9, "name": "Browns"}], "synced_at": NOW.isoformat()} if metadata else
        snapshot() if season == "2025" else None)
    target.api._request.return_value = [{
        "player": {"id": 609, "name": "Deshaun Watson"},
        "teams": [{"team": {"id": 9, "name": "Browns"}, "groups": [{
            "name": "Passing", "statistics": [{"name": "yards", "value": "855"}],
        }]}],
    }]
    with patch("src.services.player_season_service.member_request_batch", side_effect=batch) as reserve:
        text = target.refresh("nfl", "1", "Watson", 123)
    reserve.assert_called_once_with("https://v1.american-football.api-sports.io", 123, 1)
    target.api._request.assert_called_once_with("players/statistics", {"id": 609, "season": "2026"})
    assert "reserved 1" in text
    assert tables["api_sports_player_seasons"].upsert.call_args.args[0]["season"] == "2026"


def test_batch_counts_calls_once_and_disallows_extra_or_other_product():
    db = Mock()
    db.rpc.return_value.execute.return_value.data = "reserved"
    with patch("src.services.api_budget_service.API_SPORTS_BUDGET_ENABLED", True), patch(
        "src.services.api_budget_service.supabase_service._ensure_client", return_value=db,
    ):
        with member_request_batch("https://v1.basketball.api-sports.io", 123, 2):
            assert member_request_user.get() == "123"
            with pytest.raises(ApiBudgetDenied):
                reserve_request("https://v3.football.api-sports.io")
            reserve_request("https://v1.basketball.api-sports.io")
            reserve_request("https://v1.basketball.api-sports.io")
            with pytest.raises(ApiBudgetDenied):
                reserve_request("https://v1.basketball.api-sports.io")
        db.rpc.assert_called_once_with("reserve_api_requests", {
            "p_product": "basketball", "p_discord_user_id": "123", "p_requests": 2,
        })
    assert reserved_batch.get() is None and member_request_user.get() is None


def test_batch_denial_and_failures_reset_context():
    with patch("src.services.api_budget_service.API_SPORTS_BUDGET_ENABLED", False), pytest.raises(ApiBudgetDenied):
        with member_request_batch("https://v1.basketball.api-sports.io", 123, 2):
            pytest.fail("Must fail closed")
    db = Mock()
    db.rpc.return_value.execute.return_value.data = "reserved"
    with patch("src.services.api_budget_service.API_SPORTS_BUDGET_ENABLED", True), patch(
        "src.services.api_budget_service.supabase_service._ensure_client", return_value=db,
    ), pytest.raises(RuntimeError):
        with member_request_batch("https://v1.basketball.api-sports.io", 123, 2):
            raise RuntimeError("provider offline")
    assert reserved_batch.get() is None and member_request_user.get() is None


def basketball_row(game=1, points=10):
    return {
        "game": {"id": game}, "team": {"id": 9}, "player": {"id": 4, "name": "Player"},
        "points": points, "assists": 2, "rebounds": {"total": 8},
        "field_goals": {"total": 5, "attempts": 7},
        "threepoint_goals": {"total": 0, "attempts": 0},
        "freethrows_goals": {"total": 0, "attempts": 2},
    }


def test_basketball_filters_league_game_ids_and_uses_reported_denominators():
    groups = basketball_totals([basketball_row(), basketball_row(2, None), basketball_row(3, 99)], [{"id": 1}, {"id": 2}], 4)
    stats = {s["name"]: s["value"] for s in groups[0]["statistics"]}
    assert stats["available game logs"] == 2
    assert stats["points"] == 10 and stats["points reported games"] == 1
    assert stats["points per reported game"] == 10
    assert stats["field goals attempts"] == 14
    with pytest.raises(RuntimeError, match="duplicate"):
        basketball_totals([basketball_row(), basketball_row()], [{"id": 1}], 4)


def test_autocomplete_uses_selected_sport_and_league_and_returns_stable_ids():
    cache = Mock()
    cache.players.return_value = [{"name": "Deshaun Watson", "player_id": 609, "team_name": "Browns"}]
    cache.leagues.return_value = [{"name": "NFL", "league_id": "1", "current_season": "2026"}]
    target = interaction()
    target.guild_id = 1
    target.namespace = SimpleNamespace(sport="nfl", league="1")
    cog = MemberStats(season_stats=cache)
    with patch("src.member_stats.WHOP_MEMBERSHIP_SYNC_ENABLED", True):
        choices = asyncio.run(cog.player_suggestions(target, "Wat"))
        leagues = asyncio.run(cog.league_suggestions(target, "NF"))
    cache.players.assert_called_once_with("nfl", "1", "Wat")
    cache.leagues.assert_called_once_with("nfl", "NF")
    assert choices[0].value == "609" and "Browns" in choices[0].name
    assert leagues[0].value == "1"


@pytest.mark.parametrize("paid,refresh,reads", [
    (False, False, 0), (False, True, 0),
    (True, False, 1), (True, True, 2),
])
def test_season_command_preserves_access_gate(paid, refresh, reads):
    membership, cache = Mock(), Mock()
    membership.has_stats_access.return_value = paid
    cache.report.return_value = ("Season stats", "cached")
    cache.refresh.return_value = "season refresh"
    with patch("src.member_stats.WHOP_MEMBERSHIP_SYNC_ENABLED", True), patch(
        "src.member_stats.MEMBER_STATS_REFRESH_ENABLED", True,
    ):
        asyncio.run(MemberStats(membership, season_stats=cache).respond(
            interaction(), "playerstats", "nfl", league="1", player="Watson", refresh=refresh,
        ))
    assert cache.report.call_count == reads
    assert cache.refresh.call_count == int(paid and refresh)


def test_primary_command_has_no_game_id_and_has_autocomplete():
    cog = MemberStats()
    command = next(c for c in cog.get_app_commands() if c.name == "playerstats")
    assert [p.name for p in command.parameters] == ["sport", "league", "player", "refresh"]
    assert command.get_parameter("player").autocomplete
    assert command.get_parameter("league").autocomplete


def test_cold_nfl_lookup_reserves_five_calls_and_checks_league_teams():
    target, tables = service()
    tables["api_sports_player_directory"].execute.return_value.data = []
    target.snapshot = Mock(return_value=None)

    def request(endpoint, params):
        if endpoint == "players":
            return [{"id": 609, "name": "Deshaun Watson"}]
        if endpoint == "teams":
            assert params["league"] == 1
            return [{"id": 9, "name": "Browns", "logo": "discard"}]
        return [{
            "player": {"id": 609, "name": "Deshaun Watson"},
            "teams": [
                {"team": {"id": 9, "name": "Browns"}, "groups": [{
                    "name": "Passing", "statistics": [{"name": "yards", "value": "855"}],
                }]},
                {"team": {"id": 999, "name": "Different league"}, "groups": []},
            ],
        }]

    target.api._request.side_effect = request
    with patch("src.services.player_season_service.member_request_batch", side_effect=batch) as reserve:
        target.refresh("nfl", "1", "Deshaun Watson", 123)
    reserve.assert_called_once_with("https://v1.american-football.api-sports.io", 123, 5)
    assert target.api._request.call_count == 5
    writes = [call.args[0] for call in tables["api_sports_player_seasons"].upsert.call_args_list]
    assert {w["season"] for w in writes} == {"2026", "2025"}
    assert all("Different league" not in str(w) for w in writes)
    metadata = tables["api_sports_player_season_metadata"].upsert.call_args.args[0]["payload"]
    assert metadata == [{"id": 9, "name": "Browns"}]


def test_basketball_current_refresh_uses_two_calls_and_compact_league_metadata():
    target, tables = service()
    tables["api_sports_events"].execute.return_value.data = [{"season": "2026-2027"}]
    target.snapshot = Mock(side_effect=lambda sport, league, player, season, metadata=False:
        None if metadata or season == "2026-2027" else snapshot())

    def request(base, endpoint, params, **kwargs):
        assert kwargs == {"require_complete": True}
        if endpoint == "games":
            assert params == {"league": 63, "season": "2026-2027"}
            return [{"id": 1, "league": {"id": 63, "season": "2026-2027"}, "teams": {}, "logos": "discard"}]
        row = basketball_row()
        row["player"] = {"id": 609, "name": "Deshaun Watson"}
        return [row]

    target.multi_api._request.side_effect = request
    with patch("src.services.player_season_service.member_request_batch", side_effect=batch) as reserve:
        target.refresh("basketball", "63", "Watson", 123)
    reserve.assert_called_once_with("https://v1.basketball.api-sports.io", 123, 2)
    assert target.multi_api._request.call_count == 2
    assert tables["api_sports_player_season_metadata"].upsert.call_args.args[0]["payload"] == [
        {"id": 1, "league": {"id": 63, "season": "2026-2027"}},
    ]


def test_foreign_soccer_season_not_persisted_as_selected_league():
    target, _ = service()
    payload = [{
        "player": {"id": 4, "name": "Player"}, "statistics": [{
            "team": {"id": 9, "name": "Team"}, "league": {"id": 99, "season": 2026},
        }],
    }]
    with pytest.raises(RuntimeError, match="different league/season"):
        target.normalize("football", "254", "2026", 4, payload, [])


def test_basketball_wrong_metadata_scope_fails():
    target, _ = service()
    with pytest.raises(RuntimeError, match="different league/season"):
        target.normalize("basketball", "63", "2026", 4, [basketball_row()], [
            {"id": 1, "league": {"id": 99, "season": "2026"}},
        ])


def test_unknown_player_without_selected_league_cannot_spend_quota():
    target, tables = service()
    tables["api_sports_player_leagues"].execute.return_value.data = []
    with patch("src.services.player_season_service.member_request_batch") as reserve, pytest.raises(ValueError):
        target.refresh("basketball", "999", "Player", 123)
    reserve.assert_not_called()


def test_autocomplete_database_errors_logged_and_no_api_calls(caplog):
    cache = Mock()
    cache.players.side_effect = RuntimeError("offline")
    target = interaction()
    target.guild_id = 1
    target.namespace = SimpleNamespace(sport="nfl", league="1")
    with patch("src.member_stats.WHOP_MEMBERSHIP_SYNC_ENABLED", True):
        assert asyncio.run(MemberStats(season_stats=cache).player_suggestions(target, "Wat")) == []
    assert "player_name_autocomplete_failed" in caplog.text


def test_season_report_discloses_omitted_groups_and_stays_within_discord_limit():
    target, _ = service()
    target.snapshot = Mock(return_value=snapshot(groups=[
        {"name": "Huge", "statistics": [{"name": "value", "value": "x" * 5000}]},
        {"name": "Available", "statistics": [{"name": "yards", "value": 100}]},
    ]))
    text = target.report("nfl", "1", "Watson")[1]
    assert "omitted" in text and "yards: 100" in text and len(text) <= 3900
