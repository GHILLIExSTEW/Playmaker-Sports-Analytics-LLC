import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from src.member_stats import MemberStats
from src.services.api_budget_service import ApiBudgetDenied, member_request_user
from src.services.player_stats_service import PlayerStatsService, player_records, athlete_records
from tests.test_member_stats import interaction

NOW = datetime(2026, 10, 3, tzinfo=timezone.utc)
PAYLOAD = [{
    "team": {"id": 9, "name": "Cleveland Browns"},
    "groups": [
        {"name": "Passing", "players": [{
            "player": {"id": 609, "name": "Deshaun Watson"},
            "statistics": [{"name": "comp att", "value": "24/33"}, {"name": "yards", "value": "268"}],
        }]},
        {"name": "Rushing", "players": [{
            "player": {"id": 609, "name": "Deshaun Watson"},
            "statistics": [{"name": "yards", "value": "22"}, {"name": "kick return td", "value": None}],
        }]},
    ],
}]


def service(payload=PAYLOAD, updated="2026-10-02T23:00:00Z", cached=True, sport="nfl"):
    game = {
        "game_id": 21561, "kickoff_at": "2026-10-02T00:15:00Z",
        "home_team_name": "Cleveland Browns", "away_team_name": "Pittsburgh Steelers",
        "event_id": 21561, "start_at": "2026-10-02T00:15:00Z",
        "home_name": "Cleveland Browns", "away_name": "Pittsburgh Steelers",
        "home_id": "9", "away_id": "10", "event_name": "Bahrain Grand Prix",
        "raw_event": {"type": "2nd Practice"},
    }
    queries = {}
    for table in ("api_sports_nfl_games", "api_sports_events", "api_sports_player_game_stats"):
        query = Mock()
        for method in ("select", "eq", "limit", "upsert"):
            getattr(query, method).return_value = query
        query.execute.return_value = SimpleNamespace(data=(
            [{"sport_slug": sport, "game_id": 21561, "payload": payload, "synced_at": updated}]
            if table == "api_sports_player_game_stats" and cached else
            [] if table == "api_sports_player_game_stats" else [game]
        ))
        queries[table] = query
    database = Mock()
    database._ensure_client.return_value.table.side_effect = queries.__getitem__
    api = Mock()
    api._request.return_value = deepcopy(PAYLOAD)
    multi_api = Mock()
    multi_api._request.return_value = payload
    return PlayerStatsService(database, clock=lambda: NOW, api=api, multi_api=multi_api), queries, api


@pytest.mark.parametrize("sport", ["nfl", "ncaa"])
def test_cached_player_report_merges_groups_preserves_values_and_makes_no_api_call(sport):
    target, queries, api = service(sport=sport)
    title, report = target.report(sport, 21561, "Deshaun Watson")
    assert "player statistics" in title
    assert "24/33" in report and "yards: 268" in report and "yards: 22" in report
    assert "Unavailable" in report and "not season totals" in report and "Updated <t:" in report
    api._request.assert_not_called()
    if sport == "ncaa":
        assert ("sport_slug", "ncaa") in [call.args for call in queries["api_sports_events"].eq.call_args_list]


@pytest.mark.parametrize("name", ["Watson", "609", "  deshaun   watson  "])
def test_unique_search_and_id(name):
    target, _, _ = service()
    assert "Deshaun Watson" in target.report("nfl", 21561, name)[1]


def test_ambiguous_name_and_exact_match():
    payload = deepcopy(PAYLOAD)
    payload[0]["groups"][0]["players"].append({
        "player": {"id": 610, "name": "Another Watson"}, "statistics": [],
    })
    target, _, _ = service(payload)
    with pytest.raises(ValueError, match="ambiguous"):
        target.report("nfl", 21561, "Watson")
    assert "Deshaun Watson" in target.report("nfl", 21561, "Deshaun Watson")[1]


@pytest.mark.parametrize("payload", [[], PAYLOAD])
def test_missing_player_not_zero(payload):
    target, _, _ = service(payload)
    assert "does not mean the player recorded zero" in target.report("nfl", 21561, "Unknown")[1]


def test_uncached_report_instructs_refresh_without_provider_call():
    target, _, api = service(cached=False)
    assert "not been fetched" in target.report("nfl", 21561, "Watson")[1]
    api._request.assert_not_called()


@pytest.mark.parametrize("sport,game_id", [("baseball", 21561), ("nfl", 0), ("nfl", True)])
def test_invalid_inputs_do_not_spend_requests(sport, game_id):
    target, _, api = service()
    with pytest.raises(ValueError):
        target.refresh(sport, game_id, 123)
    api._request.assert_not_called()


def test_unknown_game_no_request():
    target, queries, api = service()
    queries["api_sports_nfl_games"].execute.return_value.data = []
    with pytest.raises(ValueError, match="not in this sport"):
        target.refresh("nfl", 999, 123)
    api._request.assert_not_called()


def test_refresh_exactly_one_request_caches_complete_game_and_classifies_member():
    target, queries, api = service()
    api._request.side_effect = lambda *args: (
        deepcopy(PAYLOAD) if member_request_user.get() == "123" else pytest.fail("Member quota not applied")
    )
    target.refresh("nfl", 21561, 123)
    api._request.assert_called_once_with("games/statistics/players", {"id": 21561})
    queries["api_sports_player_game_stats"].upsert.assert_called_once_with({
        "sport_slug": "nfl", "game_id": 21561, "payload": PAYLOAD, "synced_at": NOW.isoformat(),
    }, on_conflict="sport_slug,game_id")
    assert member_request_user.get() is None


def test_fresh_snapshot_reused_without_call():
    target, _, api = service(updated="2026-10-02T23:59:00Z")
    assert "no provider call" in target.refresh("nfl", 21561, 123)
    api._request.assert_not_called()


@pytest.mark.parametrize("updated", ["2026-10-02T23:55:00Z", "2026-10-03T00:01:00Z"])
def test_five_minute_boundary_and_future_timestamp_do_not_skip_refresh(updated):
    target, _, api = service(updated=updated)
    target.refresh("nfl", 21561, 123)
    api._request.assert_called_once()


def test_denied_quota_does_not_write_success_snapshot_and_resets_context():
    target, queries, api = service()
    api._request.side_effect = ApiBudgetDenied("exhausted")
    with pytest.raises(ApiBudgetDenied, match="exhausted"):
        target.refresh("nfl", 21561, 123)
    queries["api_sports_player_game_stats"].upsert.assert_not_called()
    assert member_request_user.get() is None


@pytest.mark.parametrize("payload", [None, [{}], [{"team": {}, "groups": []}]])
def test_invalid_response_explicitly_fails_without_cache_write(payload):
    target, queries, api = service()
    api._request.return_value = payload
    with pytest.raises(RuntimeError, match="invalid"):
        target.refresh("nfl", 21561, 123)
    queries["api_sports_player_game_stats"].upsert.assert_not_called()
    assert member_request_user.get() is None


def test_oversized_group_disclosed_and_discord_limit_preserved():
    payload = deepcopy(PAYLOAD)
    payload[0]["groups"][0]["players"][0]["statistics"][0]["value"] = "x" * 5000
    target, _, _ = service(payload)
    report = target.report("nfl", 21561, "609")[1]
    assert "omitted" in report and len(report) <= 4096
    assert "Rushing" in report


@pytest.mark.parametrize("paid,refresh,expected_reads", [
    (False, False, 0), (False, True, 0),
    (True, False, 1), (True, True, 2),
])
def test_player_command_reuses_paid_gate(paid, refresh, expected_reads):
    membership, cache = Mock(), Mock()
    membership.has_stats_access.return_value = paid
    cache.report.return_value = ("Player stats", "cached")
    cache.refresh.return_value = "one game refreshed"
    with patch("src.member_stats.WHOP_MEMBERSHIP_SYNC_ENABLED", True), patch(
        "src.member_stats.MEMBER_STATS_REFRESH_ENABLED", True,
    ):
        asyncio.run(MemberStats(membership, player_stats=cache).respond(
            interaction(), "gamestats", "nfl", refresh=refresh, game_id=21561, player="609",
        ))
    assert cache.report.call_count == expected_reads
    assert cache.refresh.call_count == int(paid and refresh)


def test_empty_provider_response_is_persisted_without_fabricating_records():
    target, queries, api = service(cached=False)
    api._request.return_value = []
    target.refresh("nfl", 21561, 123)
    assert queries["api_sports_player_game_stats"].upsert.call_args.args[0]["payload"] == []
    assert player_records([]) == []


BASKETBALL = [{
    "game": {"id": 21561}, "team": {"id": 9}, "player": {"id": 55397, "name": "C. M. Zinaich"},
    "type": "starters", "minutes": "20:53", "field_goals": {"total": 5, "attempts": 7, "percentage": None},
    "threepoint_goals": {"total": 0, "attempts": 0}, "rebounds": {"total": 8}, "assists": 2, "points": 10,
}]
SOCCER = [{
    "team": {"id": 9, "name": "Seattle Reign FC W"},
    "players": [{
        "player": {"id": 456791, "name": "S. Meza"},
        "statistics": [{
            "games": {"minutes": 90, "position": "M", "captain": False},
            "shots": {"total": 0, "on": 0}, "goals": {"total": 0, "conceded": None},
            "passes": {"total": 46, "key": 1, "accuracy": "38"},
        }],
    }],
}]
F1 = [{
    "race": {"id": 21561}, "driver": {"id": 34, "name": "Charles Leclerc"},
    "team": {"id": 3, "name": "Scuderia Ferrari"}, "position": 1, "time": "1:37.528",
    "laps": 29, "grid": None, "pits": None, "gap": "+0.000",
}]


@pytest.mark.parametrize("sport,payload,name,expected", [
    ("basketball", BASKETBALL, "55397", ["minutes: 20:53", "points: 10", "total: 8", "Unavailable"]),
    ("football", SOCCER, "Meza", ["minutes: 90", "accuracy: 38", "total: 0", "Unavailable"]),
    ("formula-1", F1, "Leclerc", ["2nd Practice", "1:37.528", "laps: 29", "not season totals"]),
])
def test_verified_product_adapters_report_and_route_exactly_one_request(sport, payload, name, expected):
    target, queries, api = service(payload=payload, sport=sport)
    report = target.report(sport, 21561, name)[1]
    assert all(text in report for text in expected)
    target.refresh(sport, 21561, 123)
    endpoints = {
        "basketball": ("https://v1.basketball.api-sports.io", "games/statistics/players", {"id": 21561}),
        "football": ("https://v3.football.api-sports.io", "fixtures/players", {"fixture": 21561}),
        "formula-1": ("https://v1.formula-1.api-sports.io", "rankings/races", {"race": 21561}),
    }
    target.multi_api._request.assert_called_once_with(*endpoints[sport])
    api._request.assert_not_called()
    assert queries["api_sports_player_game_stats"].upsert.call_args.args[0]["payload"] == payload


@pytest.mark.parametrize("sport,payload", [("basketball", BASKETBALL), ("formula-1", F1)])
def test_provider_game_id_mismatch_fails_before_persistence(sport, payload):
    target, queries, _ = service(payload=payload, sport=sport)
    bad = deepcopy(payload)
    bad[0]["game" if sport == "basketball" else "race"]["id"] = 999
    target.multi_api._request.return_value = bad
    with pytest.raises(RuntimeError, match="different event"):
        target.refresh(sport, 21561, 123)
    queries["api_sports_player_game_stats"].upsert.assert_not_called()


@pytest.mark.parametrize("sport", ["baseball", "hockey", "rugby", "handball", "volleyball", "mma", "cricket", "cycling"])
def test_all_tracked_coverage_gaps_are_explicit_and_never_call_provider(sport):
    target, _, api = service()
    with pytest.raises(ValueError, match="endpoint|not enabled|no configured"):
        target.report(sport, 21561, "Player")
    api._request.assert_not_called()
    target.multi_api._request.assert_not_called()


def test_empty_soccer_response_is_not_synthesized_from_team_scores():
    target, _, _ = service(payload=[], sport="football")
    assert "does not mean the player recorded zero" in target.report("football", 21561, "Meza")[1]


def test_unknown_basketball_team_is_explicit_data_error():
    target, _, _ = service()
    game = target.game("basketball", 21561)
    bad = deepcopy(BASKETBALL)
    bad[0]["team"]["id"] = 999
    with pytest.raises(RuntimeError, match="outside"):
        athlete_records("basketball", bad, 21561, game)


def test_omitting_player_lists_available_names_and_ids_without_provider_call():
    target, _, api = service()
    report = target.report("nfl", 21561)[1]
    assert "Deshaun Watson - ID 609" in report and "1 of 1" in report
    api._request.assert_not_called()


def test_large_roster_has_disclosed_bound_and_full_search_still_works():
    payload = deepcopy(SOCCER)
    payload[0]["players"] = [
        {"player": {"id": i + 1, "name": f"Player {i}"}, "statistics": []} for i in range(40)
    ]
    target, _, _ = service(payload=payload, sport="football")
    report = target.report("football", 21561)[1]
    assert "20 of 40" in report and len(report) <= 4096
    assert "**Player 39**" in target.report("football", 21561, "40")[1]


def test_empty_roster_is_explicit_unavailable_data():
    target, _, _ = service(payload=[], sport="football")
    assert "unavailable data, not zero" in target.report("football", 21561)[1]


@pytest.mark.parametrize("sport,payload", [("nfl", PAYLOAD), ("basketball", BASKETBALL)])
def test_nonfinite_stats_are_explicit_data_errors(sport, payload):
    bad = deepcopy(payload)
    if sport == "nfl":
        bad[0]["groups"][0]["players"][0]["statistics"][0]["value"] = float("nan")
    else:
        bad[0]["points"] = float("inf")
    target, _, _ = service(payload=bad, sport=sport)
    with pytest.raises(RuntimeError, match="non-finite"):
        target.report(sport, 21561, "609" if sport == "nfl" else "55397")


def test_actual_soccer_zero_id_retains_named_stats_without_inventing_identifier():
    payload = deepcopy(SOCCER)
    payload[0]["players"][0]["player"] = {"id": 0, "name": "Ana Beatriz Gomes Lopes"}
    target, _, _ = service(payload=payload, sport="football")
    report = target.report("football", 21561, "Gomes")[1]
    assert "minutes: 90" in report and "provider returned ID 0; name-only lookup" in report
    assert "Provider returned ID 0; search by name" in target.report("football", 21561)[1]
    assert "No matching" in target.report("football", 21561, "0")[1]
