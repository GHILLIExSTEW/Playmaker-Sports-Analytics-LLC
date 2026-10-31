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


def test_website_capper_access_uses_approved_research_rpc():
    db = Mock()
    db._ensure_client.return_value.rpc.return_value.execute.return_value.data = True
    data = workspace.ResearchWorkspaceData(database=db)

    assert data.website_capper_tools_access(123, 456, 42) is True
    db._ensure_client.return_value.rpc.assert_called_once_with(
        "discord_capper_tools_access",
        {"p_discord_user_id": "123", "p_guild_id": "456", "p_role_id": "42"},
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
    cog = workspace.ResearchWorkspace(Mock(), staff_alert=AsyncMock(), membership=Mock(), data=Mock())
    cog.membership.has_stats_access.return_value = False
    cog.data.website_capper_tools_access.return_value = False
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
    cog = workspace.ResearchWorkspace(Mock(), staff_alert=AsyncMock(), membership=Mock(), data=Mock())
    cog.data.website_capper_tools_access.return_value = False
    cog._is_operator = AsyncMock(return_value=False)
    target = SimpleNamespace(
        user=SimpleNamespace(id=123), guild_id=456,
        response=SimpleNamespace(send_message=AsyncMock()),
    )
    assert asyncio.run(cog._gate(target, operator_only=True)) is False
    assert "OPERATOR/Owner" in target.response.send_message.call_args.args[0]
    cog.membership.has_stats_access.assert_not_called()


def test_owner_can_pass_live_refresh_gate_without_capper_roster(monkeypatch):
    cog = workspace.ResearchWorkspace(Mock(), staff_alert=AsyncMock(), data=Mock())
    cog.data.website_capper_tools_access.return_value = False
    cog._is_operator = AsyncMock(return_value=True)
    monkeypatch.setattr(cog, "_is_capper", lambda interaction: False)
    target = SimpleNamespace(
        user=SimpleNamespace(id=123),
        response=SimpleNamespace(send_message=AsyncMock()),
    )

    assert asyncio.run(cog._capper_refresh_gate(target)) is True
    target.response.send_message.assert_not_awaited()


def test_approved_website_capper_can_pass_refresh_gate():
    cog = workspace.ResearchWorkspace(Mock(), staff_alert=AsyncMock(), data=Mock())
    cog.data.website_capper_tools_access.return_value = True
    target = SimpleNamespace(
        user=SimpleNamespace(id=123),
        guild_id=456,
        response=SimpleNamespace(send_message=AsyncMock()),
    )

    assert asyncio.run(cog._capper_refresh_gate(target)) is True
    cog.data.website_capper_tools_access.assert_called_once_with(123, 456, workspace.TRACKER_ROLE_ID)


def test_approved_website_capper_can_use_cached_research_without_paid_membership(monkeypatch):
    cog = workspace.ResearchWorkspace(Mock(), staff_alert=AsyncMock(), membership=Mock(), data=Mock())
    cog.data.website_capper_tools_access.return_value = True
    cog._is_operator = AsyncMock(return_value=False)
    target = SimpleNamespace(user=SimpleNamespace(id=123), guild_id=456)
    monkeypatch.setattr(workspace, "WHOP_MEMBERSHIP_SYNC_ENABLED", False)

    assert asyncio.run(cog._member_allowed(target)) is True
    cog.membership.has_stats_access.assert_not_called()


def test_stale_research_auto_refresh_is_capper_only(monkeypatch):
    monkeypatch.setattr(workspace, "GUILD_ID", None)
    cog = workspace.ResearchWorkspace(Mock(), staff_alert=AsyncMock(), data=Mock())
    cog.data.request_refresh.return_value = "request-123"
    cog.data.website_capper_tools_access.return_value = True
    interaction = SimpleNamespace(user=SimpleNamespace(id=123), guild_id=456)
    game = {"competition": "nfl", "game_id": "1"}

    note = asyncio.run(cog._refresh_if_stale(
        interaction, game, "game",
        (workspace.datetime.now(workspace.timezone.utc) - workspace.timedelta(minutes=61)).isoformat(),
    ))

    assert "Refresh queued" in note
    cog.data.request_refresh.assert_called_once_with(
        123, "nfl", "1", "game", 456, workspace.TRACKER_ROLE_ID,
    )

    cog.data.website_capper_tools_access.return_value = False
    cog._is_operator = AsyncMock(return_value=False)
    non_capper = SimpleNamespace(user=SimpleNamespace(id=789), guild_id=456)
    assert asyncio.run(cog._refresh_if_stale(non_capper, game, "game", None)) is None
    cog.data.request_refresh.assert_called_once()


def test_fresh_capper_cache_does_not_queue_refresh(monkeypatch):
    monkeypatch.setattr(workspace, "GUILD_ID", None)
    cog = workspace.ResearchWorkspace(Mock(), staff_alert=AsyncMock(), data=Mock())
    cog.data.website_capper_tools_access.return_value = True
    interaction = SimpleNamespace(user=SimpleNamespace(id=123), guild_id=456)
    updated_at = workspace.datetime.now(workspace.timezone.utc).isoformat()

    assert asyncio.run(cog._refresh_if_stale(
        interaction, {"competition": "nfl", "game_id": "1"}, "odds", updated_at,
    )) is None
    cog.data.request_refresh.assert_not_called()


def test_auto_refresh_reports_unsupported_player_stat_leagues(monkeypatch):
    monkeypatch.setattr(workspace, "GUILD_ID", None)
    cog = workspace.ResearchWorkspace(Mock(), staff_alert=AsyncMock(), data=Mock())
    cog.data.website_capper_tools_access.return_value = True
    interaction = SimpleNamespace(user=SimpleNamespace(id=123), guild_id=456)

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


def test_player_embed_splits_player_research_into_compact_sections():
    game = {"away_name": "Minnesota Vikings", "home_name": "Detroit Lions"}
    report = "\n".join([
        "**Jared Goff — Detroit Lions**",
        "Sample: 1 prior game logs, up to ten; only pre-match data is included.",
        "**Passing yards:** average 250.00 across 1 prior observations. At 225: 1 over · 0 under · 0 push.",
        "Selected game snapshot: 300 (not included in the prior-game sample).",
        "<t:1791501600:f>: 250",
        "Cached samples may be incomplete; this is descriptive research, not a prediction or betting advice.",
    ])

    embed = workspace.ResearchWorkspace._player_embed(game, report)

    assert embed.title == "Minnesota Vikings at Detroit Lions · Player research"
    assert "Sample:" in embed.description
    assert len(embed.fields) == 1
    assert embed.fields[0].name == "Stat results"
    assert "**Passing yards:**" in embed.fields[0].value
    assert "Selected game snapshot: 300" in embed.fields[0].value
    assert "Cached samples may be incomplete" in embed.footer.text


def test_player_image_card_parser_extracts_selected_stats_and_observations():
    report = "\n".join([
        "**Jared Goff — Detroit Lions**",
        "Sample: 1 prior game logs, up to ten; only pre-match data is included.",
        "**Passing yards:** average 250.00 across 1 prior observations. At 225: 1 over · 0 under · 0 push.",
        "Selected game snapshot: 300 (not included in the prior-game sample).",
        "<t:1791501600:f>: 250",
    ])

    player, description, sections = workspace.player_report_sections(report)

    assert player == "Jared Goff — Detroit Lions"
    assert description.startswith("Sample:")
    assert sections[0][0] == "Passing yards"
    assert "1 over" in sections[0][1]
    assert sections[1][0] == "Recent observations"


def test_research_image_card_renders_team_marks_and_report_sections():
    from io import BytesIO

    from PIL import Image

    logo = BytesIO()
    Image.new("RGBA", (32, 32), (255, 0, 0, 255)).save(logo, format="PNG")

    result = workspace.render_research_card(
        away_name="Minnesota Vikings",
        home_name="Detroit Lions",
        heading="Player research · Jared Goff",
        description="Sample: 1 prior game · pre-match only",
        sections=[("Passing yards", "Average 250 · 1 over · 0 under · 0 push")],
        away_logo=logo.getvalue(),
        home_logo=logo.getvalue(),
    )

    with Image.open(result) as card:
        assert card.format == "PNG"
        assert card.width == 1200
        assert card.height >= 760


def test_image_cards_render_discord_timestamps_as_readable_dates():
    from src.services.research_card_image_service import _image_text

    text = _image_text("Kickoff <t:1793438400:f> · Last result <t:1793340000:R>")

    assert "<t:" not in text
    assert "2026" in text
    assert "Kickoff" in text


def test_research_response_attaches_rendered_card_image():
    cog = workspace.ResearchWorkspace(Mock(), staff_alert=AsyncMock(), data=Mock())
    cog.data.team_logo_urls.return_value = (None, None)
    embed = workspace.discord.Embed(title="Lions at Vikings", description="Cached matchup")
    embed.add_field(name="Recent form", value="Lions 4-1")

    image = asyncio.run(cog._attach_research_image(
        embed,
        {"competition": "nfl", "game_id": "1", "sport": "nfl",
         "away_name": "Detroit Lions", "home_name": "Minnesota Vikings"},
    ))

    assert image.filename == "research-card.png"
    assert embed.image.url == "attachment://research-card.png"
    assert embed.description is None
    assert not embed.fields


def test_player_embed_lists_available_players_in_separate_field():
    game = {"away_name": "Minnesota Vikings", "home_name": "Detroit Lions"}
    report = "\n".join([
        "**Cached player records before <t:1791501600:f>**",
        "Jared Goff — Detroit Lions",
        "Justin Jefferson — Minnesota Vikings",
        "Run `/research players` again with a player name.",
    ])

    embed = workspace.ResearchWorkspace._player_embed(game, report)

    assert embed.description == "**Cached player records before <t:1791501600:f>**"
    assert embed.fields[0].name == "Players in cached sample"
    assert "Jared Goff" in embed.fields[0].value
    assert embed.fields[1].name == "Next step"


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
        "status": "NS", "synced_at": "2026-10-09T18:00:00Z", "season": "2026",
    }
    history = [
        {
            "game_id": "10", "season": "2026", "start_at": "2026-10-01T18:00:00Z", "home_id": "1",
            "home_name": "Home", "away_id": "2", "away_name": "Away", "home_score": 24,
            "away_score": 17, "synced_at": "2026-10-02T18:00:00Z",
        },
        {
            "game_id": "11", "season": "2026", "start_at": "2026-10-02T18:00:00Z", "home_id": "1",
            "home_name": "Home", "away_id": "3", "away_name": "Other", "home_score": 7,
            "away_score": 10, "synced_at": "2026-10-03T18:00:00Z",
        },
        {
            "game_id": "9", "season": "2025", "start_at": "2025-12-01T18:00:00Z", "home_id": "1",
            "home_name": "Home", "away_id": "4", "away_name": "Old season", "home_score": 99,
            "away_score": 0, "synced_at": "2025-12-02T18:00:00Z",
        },
    ]
    embed = workspace.ResearchWorkspace._matchup_embed(
        {"game": game, "history": history}, operator_fields=[],
    )
    assert "Season 2026" in embed.description
    assert "**Season record (2 scored):** 1-1-0" in embed.fields[0].value
    assert "99" not in embed.fields[0].value
    assert "Recent head-to-head" in embed.fields[2].name
    assert "Other" not in embed.fields[2].value
    assert "2026 season only" in embed.footer.text


def test_matchup_history_queries_and_filters_exact_selected_season():
    game = {
        "competition": "nfl", "game_id": "30", "start_at": "2026-10-10T18:00:00Z",
        "home_id": "1", "home_name": "Home", "away_id": None, "away_name": "Away",
        "status": "NS", "synced_at": "2026-10-09T18:00:00Z", "season": "2026",
    }
    rows = [
        {"game_id": "old", "season": "2025", "start_at": "2025-12-01T18:00:00Z"},
        {"game_id": "current", "season": "2026", "start_at": "2026-10-01T18:00:00Z"},
    ]

    class Query:
        def __init__(self):
            self.filters = []

        def select(self, *_args):
            return self

        def eq(self, *args):
            self.filters.append(args)
            return self

        def lt(self, *_args):
            return self

        def in_(self, *_args):
            return self

        def order(self, *_args, **_kwargs):
            return self

        def limit(self, *_args):
            return self

        def execute(self):
            return SimpleNamespace(data=rows)

    query = Query()
    db = Mock()
    db.select.return_value.data = [game]
    db._ensure_client.return_value.table.return_value = query
    db._execute.side_effect = lambda operation: operation()

    result = workspace.ResearchWorkspaceData(database=db).matchup("nfl", "30")

    assert ("season", "2026") in query.filters
    assert [row["game_id"] for row in result["history"]] == ["current"]


def test_score_parser_treats_missing_score_as_unavailable():
    assert workspace._numeric_score(None) is None
