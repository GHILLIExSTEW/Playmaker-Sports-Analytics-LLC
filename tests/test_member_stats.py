import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import discord

import pytest

from src.member_stats import MemberStats
from src.services.member_stats_service import MemberStatsService, score
from src.services.membership_service import MembershipService

NOW = datetime(2026, 10, 3, tzinfo=timezone.utc)


def event(**changes):
    return {
        "event_id": "1", "start_at": "2026-10-02T12:00:00Z", "home_name": "Home",
        "away_name": "Away", "home_score": {"total": 10}, "away_score": {"total": 5},
        "status_code": "FT", "synced_at": "2026-10-02T12:00:00.42324Z", **changes,
    }


def stats(rows):
    query = Mock()
    for method in ["select", "eq", "ilike", "gte", "lte", "order", "limit"]:
        getattr(query, method).return_value = query
    query.execute.return_value = SimpleNamespace(data=rows)
    database = Mock()
    database._ensure_client.return_value.table.return_value = query
    return MemberStatsService(database=database, clock=lambda: NOW), query


def test_cached_results_scores_and_freshness_without_provider_calls():
    service, query = stats([event()])
    with patch("requests.get", side_effect=AssertionError("Live API forbidden")):
        title, report = service.report("results", "basketball")
    assert "Basketball" in title and "10–5" in report
    assert "Oldest displayed update:" in report
    query.limit.assert_called_with(100)


def test_team_queries_deduplicate_and_show_recent_form():
    service, query = stats([event()])
    _, report = service.report("teamstats", "basketball", "Home")
    assert "1W / 0L / 0T" in report and "Not season standings" in report
    assert "Average scored: 10.0; conceded: 5.0" in report
    assert "Showing 1 cached events" in report
    assert query.ilike.call_count == 2


def test_matchup_filters_other_opponents():
    service, _ = stats([event(status_code="NS"), event(event_id="2", away_name="Different", status_code="NS")])
    _, report = service.report("matchup", "basketball", "Home", "Away")
    assert "Home vs Away" in report and "Different" not in report


def test_unique_short_name_and_wildcard_escaping():
    service, query = stats([event(home_name="Home Extended")])
    _, report = service.report("teamstats", "basketball", "Home")
    assert "Home Extended" in report
    service.events("basketball", True, "100%_Team")
    assert query.ilike.call_args.args == ("away_name", "%100%")


def test_lsu_tigers_resolves_cached_lsu():
    service, _ = stats([event(home_name="LSU", away_name="McNeese", status_code="NS")])
    _, report = service.report("schedule", "ncaa", "LSU Tigers")
    assert "LSU vs McNeese" in report


def test_ambiguous_team_name_does_not_merge_teams():
    service, _ = stats([event(home_name="New York Jets"), event(event_id="2", home_name="New York Giants")])
    with pytest.raises(ValueError, match="ambiguous"):
        service.report("schedule", "basketball", "New York")


def test_expanded_home_name_stats_use_correct_side():
    service, _ = stats([event(home_name="LSU", away_name="McNeese")])
    _, report = service.report("teamstats", "ncaa", "LSU Tigers")
    assert "1W / 0L / 0T" in report


def test_nfl_cache_normalization():
    service, _ = stats([{
        "game_id": 2, "kickoff_at": "2026-10-02T12:00:00Z", "home_team_name": "Home",
        "away_team_name": "Away", "home_score": 7, "away_score": 3, "status_short": "FT",
        "synced_at": "2026-10-02T12:00:00Z",
    }])
    assert "7–3" in service.report("results", "nfl")[1]
    service.db._ensure_client.return_value.table.assert_called_with("api_sports_nfl_games")


def test_f1_session_discovery_uses_name_type_and_id_not_team_scores():
    service, _ = stats([event(
        home_name=None, away_name=None, event_name="Bahrain Grand Prix",
        raw_event={"type": "2nd Practice"}, status_code="COMPLETED",
    )])
    report = service.report("results", "formula-1")[1]
    assert "Bahrain Grand Prix" in report and "2nd Practice" in report and "Session ID" not in report
    assert "None vs None" not in report
    with pytest.raises(ValueError, match="not team-form"):
        service.report("teamstats", "formula-1", "Ferrari")
    with pytest.raises(ValueError, match="cached only"):
        service.refresh("formula-1", 123)


def test_unfinished_and_missing_scores_not_counted_as_wins():
    service, _ = stats([event(home_score=None), event(event_id="2", status_code="NS")])
    _, report = service.report("teamstats", "basketball", "Home")
    assert "0W / 0L / 0T" in report and "Scores unavailable" in report


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), "10", None])
def test_score_rejects_invalid_values(value):
    assert score(value) is None


def test_cache_failure_not_hidden():
    service, query = stats(None)
    with pytest.raises(RuntimeError, match="invalid response"):
        service.events("nfl", True)
    query.execute.side_effect = RuntimeError("offline")
    with pytest.raises(RuntimeError, match="offline"):
        service.events("nfl", True)


def interaction():
    return SimpleNamespace(
        user=SimpleNamespace(id=123), response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


@pytest.mark.parametrize("allowed", [True, False])
def test_paid_gate_before_cache_reads(allowed):
    member = Mock()
    member.has_stats_access.return_value = allowed
    cache = Mock()
    cache.report.return_value = ("Results", "cached")
    target = interaction()
    with patch("src.member_stats.WHOP_MEMBERSHIP_SYNC_ENABLED", True):
        asyncio.run(MemberStats(member, cache).respond(target, "results", "nfl"))
    assert cache.report.call_count == int(allowed)
    if not allowed:
        assert "verified paid membership" in target.followup.send.call_args.args[0]


def test_lookup_failure_does_not_authorize():
    member = Mock()
    member.has_stats_access.side_effect = RuntimeError("migration missing")
    cache = Mock()
    target = interaction()
    with patch("src.member_stats.WHOP_MEMBERSHIP_SYNC_ENABLED", True):
        asyncio.run(MemberStats(member, cache).respond(target, "results", "nfl"))
    cache.report.assert_not_called()
    assert "temporarily unavailable" in target.followup.send.call_args.args[0]


def test_disabled_sync_does_not_read_membership_or_cache():
    member, cache = Mock(), Mock()
    with patch("src.member_stats.WHOP_MEMBERSHIP_SYNC_ENABLED", False):
        asyncio.run(MemberStats(member, cache).respond(interaction(), "results", "nfl"))
    member.has_stats_access.assert_not_called()
    cache.report.assert_not_called()


def test_stats_rpc_includes_new_and_historical_paid_plans():
    database = Mock()
    database._ensure_client.return_value.rpc.return_value.execute.return_value.data = True
    with patch("src.services.membership_service.WHOP_ACCOUNT_ID", "biz_test"), patch(
        "src.services.membership_service.WHOP_PAID_PLAN_IDS", {"plan_high", "plan_all", "plan_new"},
    ):
        assert MembershipService(database).has_stats_access(123)
    database._ensure_client.return_value.rpc.assert_called_once_with(
        "discord_has_paid_plan_access", {
            "p_discord_user_id": "123", "p_account_id": "biz_test",
            "p_plan_ids": ["plan_all", "plan_high", "plan_new"],
        },
    )


@pytest.mark.parametrize("account,plans", [("", {"plan_paid"}), ("biz_test", set())])
def test_missing_stats_configuration_denies(account, plans):
    database = Mock()
    with patch("src.services.membership_service.WHOP_ACCOUNT_ID", account), patch(
        "src.services.membership_service.WHOP_PAID_PLAN_IDS", plans,
    ), pytest.raises(RuntimeError, match="approved"):
        MembershipService(database).has_stats_access(123)
    database._ensure_client.assert_not_called()


@pytest.mark.parametrize("response", [None, [], {}, "true", 1])
def test_stats_lookup_rejects_invalid_responses(response):
    database = Mock()
    database._ensure_client.return_value.rpc.return_value.execute.return_value.data = response
    with patch("src.services.membership_service.WHOP_ACCOUNT_ID", "biz_test"), patch(
        "src.services.membership_service.WHOP_PAID_PLAN_IDS", {"plan_paid"},
    ), pytest.raises(RuntimeError, match="invalid access response"):
        MembershipService(database).has_stats_access(123)


def test_six_guild_only_stats_commands_registered():
    cog = MemberStats()
    commands = cog.get_app_commands()
    assert {command.name for command in commands} == {"matchup", "teamstats", "schedule", "results", "playerstats", "gamestats"}
    assert all(command.guild_only for command in commands)


def test_oversized_report_is_explicit_error_not_silently_truncated():
    member, cache = Mock(), Mock()
    member.has_stats_access.return_value = True
    cache.report.return_value = ("Results", "x" * 4097)
    target = interaction()
    with patch("src.member_stats.WHOP_MEMBERSHIP_SYNC_ENABLED", True):
        asyncio.run(MemberStats(member, cache).respond(target, "results", "nfl"))
    assert "temporarily unavailable" in target.followup.send.call_args.args[0]


def test_paid_member_can_read_cache_while_refresh_remains_disabled():
    member, cache = Mock(), Mock()
    member.has_stats_access.return_value = True
    cache.report.return_value = ("Results", "cached")
    with patch("src.member_stats.WHOP_MEMBERSHIP_SYNC_ENABLED", True), patch(
        "src.member_stats.MEMBER_STATS_REFRESH_ENABLED", False,
    ):
        asyncio.run(MemberStats(member, cache).respond(interaction(), "results", "nfl"))
        target = interaction()
        asyncio.run(MemberStats(member, cache).respond(target, "results", "nfl", refresh=True))
    assert cache.report.call_count == 2
    cache.refresh.assert_not_called()
    assert "On-demand refresh is not enabled yet" in target.followup.send.call_args.args[0]


def test_paid_member_refreshes_and_rereads_cache():
    member, cache = Mock(), Mock()
    member.has_stats_access.return_value = True
    cache.report.return_value = ("Results", "cached")
    cache.refresh.return_value = "Refreshed today only"
    target = interaction()
    with patch("src.member_stats.WHOP_MEMBERSHIP_SYNC_ENABLED", True), patch(
        "src.member_stats.MEMBER_STATS_REFRESH_ENABLED", True,
    ):
        asyncio.run(MemberStats(member, cache).respond(target, "results", "nfl", refresh=True))
    cache.refresh.assert_called_once_with("nfl", 123)
    assert cache.report.call_count == 2
    assert "Refreshed today only" in target.followup.send.call_args.kwargs["embed"].description


@pytest.mark.parametrize("role_id", [1328120848992960543, 1347741218158678097, 1328149760766640190])
def test_configured_guild_moderator_role_grants_stats_only(role_id):
    member, cache = Mock(), Mock()
    cache.report.return_value = ("Results", "cached")
    cache.refresh.return_value = "Refreshed today only"
    target = interaction()
    target.guild_id = 1234
    target.user = Mock(spec=discord.Member)
    target.user.id = 123
    target.user.roles = [SimpleNamespace(id=role_id)]
    with patch("src.member_stats.GUILD_ID", 1234), patch(
        "src.member_stats.WHOP_MEMBERSHIP_SYNC_ENABLED", True,
    ), patch("src.member_stats.MEMBER_STATS_REFRESH_ENABLED", True):
        asyncio.run(MemberStats(member, cache).respond(target, "results", "nfl", refresh=True))
    member.has_stats_access.assert_not_called()
    member.has_paid_access.assert_not_called()
    cache.refresh.assert_called_once_with("nfl", 123)


def test_moderator_can_use_stats_while_whop_sync_disabled():
    member, cache = Mock(), Mock()
    cache.report.return_value = ("Results", "cached")
    target = interaction()
    target.guild_id = 1234
    target.user = Mock(spec=discord.Member)
    target.user.id = 123
    target.user.roles = [SimpleNamespace(id=1328120848992960543)]
    with patch("src.member_stats.GUILD_ID", 1234), patch("src.member_stats.WHOP_MEMBERSHIP_SYNC_ENABLED", False):
        asyncio.run(MemberStats(member, cache).respond(target, "results", "nfl"))
    member.has_stats_access.assert_not_called()
    cache.report.assert_called_once()


def test_moderator_role_from_other_guild_does_not_grant_access():
    member, cache = Mock(), Mock()
    member.has_stats_access.return_value = False
    target = interaction()
    target.guild_id = 9999
    target.user = Mock(spec=discord.Member)
    target.user.id = 123
    target.user.roles = [SimpleNamespace(id=1328120848992960543)]
    with patch("src.member_stats.GUILD_ID", 1234), patch(
        "src.member_stats.WHOP_MEMBERSHIP_SYNC_ENABLED", True,
    ):
        asyncio.run(MemberStats(member, cache).respond(target, "results", "nfl", refresh=True))
    cache.refresh.assert_not_called()
    cache.report.assert_not_called()


@pytest.mark.parametrize("refresh,reads", [(False, 1), (True, 2)])
def test_eligible_trial_uses_same_reports_and_refresh_limits_as_paid(refresh, reads):
    database, cache = Mock(), Mock()
    database._ensure_client.return_value.rpc.return_value.execute.return_value.data = False
    membership = MembershipService(database)
    membership.has_trial_access = Mock(return_value=True)
    cache.report.return_value = ("Results", "cached")
    cache.refresh.return_value = "Refreshed today only"
    target = interaction()
    with patch("src.services.membership_service.WHOP_ACCOUNT_ID", "biz_test"), patch(
        "src.services.membership_service.WHOP_PAID_PLAN_IDS", {"plan_paid"},
    ), patch("src.member_stats.WHOP_MEMBERSHIP_SYNC_ENABLED", True), patch(
        "src.member_stats.MEMBER_STATS_REFRESH_ENABLED", True,
    ):
        asyncio.run(MemberStats(membership, cache).respond(target, "results", "nfl", refresh=refresh))
    membership.has_trial_access.assert_called_once_with(123)
    assert cache.report.call_count == reads
    assert cache.refresh.call_count == int(refresh)
    if refresh:
        cache.refresh.assert_called_once_with("nfl", 123)
