import asyncio
import math
import random
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src import model_projections as cog_module
from src.model_projections import ALERT_INTERVAL, BACKTEST_RETRY, ModelProjections
from src.services import model_engine as engine
from src.services.data_model_service import ModelGame
from src.services.model_engine import (
    Context, Injury, Market, ModelProjection, Params, Weather, backtest, consensus_market, parameter_grid,
    poisson_outcomes, poisson_pmf, run_model,
)
from src.services.model_inputs_service import ModelInputsService, weather_window
from src.services.model_projection_service import ModelProjectionService, ModelRun, projection_row
from src.services.team_locations import VENUES

NOW = datetime(2026, 10, 14, 16, tzinfo=timezone.utc)


def season(count=240, teams=8, seed=7, start=datetime(2026, 1, 1, 0, tzinfo=timezone.utc)):
    rng = random.Random(seed)
    strength = [rng.gauss(0, 6) for _ in range(teams)]
    games = []
    for index in range(count):
        home, away = rng.sample(range(teams), 2)
        margin = strength[home] - strength[away] + 3 + rng.gauss(0, 10)
        total = 210 + rng.gauss(0, 12)
        games.append(ModelGame(
            str(index + 1), start + timedelta(hours=12 * index), str(home), f"Team {home}",
            round((total + margin) / 2), str(away), f"Team {away}", round((total - margin) / 2), "FT", NOW))
    return games


def upcoming(games, start=NOW + timedelta(hours=3), home="0", away="1"):
    return ModelGame("upcoming", start, home, f"Team {home}", None, away, f"Team {away}", None, "NS", NOW)


class Recorder:
    """Supabase-style fluent client fake that records every chain and serves canned rows per table."""

    def __init__(self, rows=None, fail=()):
        self.rows = rows or {}
        self.fail = set(fail)
        self.calls = []

    def table(self, name):
        if name in self.fail:
            raise RuntimeError(f"{name} missing")
        chain = [name]
        self.calls.append(chain)
        client = self

        class Query:
            def __getattr__(self, method):
                def call(*args, **kwargs):
                    chain.append((method, args, kwargs))
                    return self
                return call

            def execute(self):
                return SimpleNamespace(data=client.rows.get(name, []))
        return Query()

    def ops(self, table, method):
        return [step for chain in self.calls if chain[0] == table for step in chain[1:] if step[0] == method]


# ----- engine math -----

def test_poisson_outcomes_match_direct_sums():
    home_rate, away_rate = 3.1, 2.7
    home, away = poisson_pmf(home_rate), poisson_pmf(away_rate)
    win = sum(h * a for i, h in enumerate(home) for j, a in enumerate(away) if i > j)
    tie = sum(home[i] * away[i] for i in range(min(len(home), len(away))))
    probability, cover, over = poisson_outcomes(home_rate, away_rate, home_line=-1.5, total_line=5.5)
    assert probability == pytest.approx(win + tie / 2, abs=1e-9)
    assert cover == pytest.approx(sum(h * a for i, h in enumerate(home) for j, a in enumerate(away) if i - j > 1.5), abs=1e-9)
    total = poisson_pmf(home_rate + away_rate)
    assert over == pytest.approx(1 - sum(total[:6]), abs=1e-9)
    assert poisson_outcomes(2.5, 2.5)[0] == pytest.approx(0.5, abs=1e-9)


def test_poisson_whole_number_lines_exclude_pushes():
    home, away = poisson_pmf(3.0), poisson_pmf(2.0)
    _, cover, over = poisson_outcomes(3.0, 2.0, home_line=-1.0, total_line=5.0)
    wins = sum(h * a for i, h in enumerate(home) for j, a in enumerate(away) if i - j > 1)
    pushes = sum(h * a for i, h in enumerate(home) for j, a in enumerate(away) if i - j == 1)
    assert cover == pytest.approx(wins / (1 - pushes), abs=1e-9)
    total = poisson_pmf(5.0)
    assert over == pytest.approx((1 - sum(total[:6])) / (1 - total[5]), abs=1e-9)


def test_consensus_market_uses_no_vig_median_and_balanced_lines():
    def book(home, away, spreads, totals):
        return {"bets": [
            {"name": "Home/Away", "values": [{"value": "Home", "odd": home}, {"value": "Away", "odd": away}]},
            {"name": "Asian Handicap", "values": [{"value": f"{side} {line}", "odd": odd}
                                                  for line, (h, a) in spreads.items()
                                                  for side, odd in (("Home", h), ("Away", a))]},
            {"name": "Over/Under", "values": [{"value": f"{side} {line}", "odd": odd}
                                              for line, (o, u) in totals.items() for side, odd in (("Over", o), ("Under", u))]},
        ]}
    books = [
        book("1.50", "2.60", {-3.5: ("1.91", "1.91"), -7.5: ("2.80", "1.40")}, {44.5: ("1.90", "1.92"), 41.5: ("1.5", "2.6")}),
        book("1.55", "2.45", {-3.0: ("1.95", "1.87")}, {45.0: ("1.91", "1.91")}),
        book("bad", "2.00", {}, {}),
    ]
    market = consensus_market(books, NOW)
    expected = sorted([(1 / 1.5) / (1 / 1.5 + 1 / 2.6), (1 / 1.55) / (1 / 1.55 + 1 / 2.45)])
    assert market.home_probability == pytest.approx(sum(expected) / 2)
    assert market.home_spread == pytest.approx(-3.25)
    assert market.total == pytest.approx(44.75)
    assert market.books == 2
    assert consensus_market([{"bets": []}], NOW) is None


def test_injury_points_weight_status_and_position():
    assert Injury("1", "QB", "QB", "Out", "").points == 3.0
    assert Injury("1", "Kicker", "PK", "Doubtful", "").points == pytest.approx(0.75 * 0.3)
    assert Injury("1", "Unknown", None, "Out", "").points == 0.25
    assert Injury("1", "Q", "QB", "Questionable", "").points == 0.0


def test_params_from_dict_rejects_invalid_values():
    assert Params.from_dict({"learning_rate": 0.03, "season_regression": 0.15, "use_efficiency": False}) == \
        Params(learning_rate=0.03, season_regression=0.15, use_efficiency=False)
    for value in (None, {}, {"learning_rate": 2, "season_regression": 0.1}, {"learning_rate": "x", "season_regression": 0}):
        assert Params.from_dict(value) == Params()


def test_nfl_grid_offers_epa_free_parameters_only_for_nfl():
    assert len(parameter_grid("nfl")) == 2 * len(parameter_grid("basketball"))
    assert {params.use_efficiency for params in parameter_grid("nfl")} == {True, False}
    assert all(params.use_efficiency for params in parameter_grid("hockey"))


# ----- engine behaviour -----

def test_projections_use_only_information_available_before_each_game():
    games = season()
    projections, _ = run_model("basketball", games)
    later = [replace(game, home_score=game.away_score, away_score=game.home_score) if index >= 150 else game
             for index, game in enumerate(games)]
    changed, _ = run_model("basketball", later)
    for game in games[:151]:
        assert changed.get(game.id) == projections.get(game.id)
    assert any(changed[game.id] != projections[game.id] for game in games[160:] if game.id in projections)


def test_projection_requires_minimum_team_and_league_history():
    games = season(count=60)
    projections, _ = run_model("basketball", games)
    assert not any(game.id in projections for game in games[:engine.MIN_LEAGUE_GAMES])
    assert any(game.id in projections for game in games[40:])
    projection = projections[games[-1].id]
    assert projection.home_sample >= engine.MIN_TEAM_GAMES and projection.away_sample >= engine.MIN_TEAM_GAMES


def test_skipped_games_are_not_projected_or_learned():
    games = season()
    cancelled = games + [replace(upcoming(games), id="cancelled", status="CANC")]
    projections, _ = run_model("basketball", cancelled)
    assert "cancelled" not in projections
    assert run_model("basketball", games)[0] == {key: value for key, value in projections.items()}


def test_market_is_comparison_only_and_sets_edge_cover_and_over():
    games = season()
    game = upcoming(games)
    plain, _ = run_model("basketball", games + [game])
    market = Market(0.55, -4.5, 211.5, 6, NOW)
    compared, _ = run_model("basketball", games + [game], Context(markets={game.id: market}))
    base, result = plain[game.id], compared[game.id]
    assert (result.home_score, result.away_score, result.home_probability) == \
        (base.home_score, base.away_score, base.home_probability)
    assert result.market == market
    assert result.market_edge == pytest.approx(result.home_probability - 0.55)
    assert 0 < result.cover_probability < 1 and 0 < result.over_probability < 1
    assert base.market is None and base.market_edge is None and base.cover_probability is None


def test_poisson_sports_report_poisson_distribution():
    games = [replace(game, home_score=game.home_score % 7, away_score=game.away_score % 6) for game in season()]
    projection = run_model("hockey", games + [upcoming(games)])[0]["upcoming"]
    assert projection.distribution == "poisson"
    assert run_model("basketball", season() + [upcoming(season())])[0]["upcoming"].distribution == "normal"


def test_nfl_injuries_lower_the_injured_team_projection():
    games = season()
    game = upcoming(games)
    healthy = run_model("nfl", games + [game])[0][game.id]
    hurt = run_model("nfl", games + [game], Context(injuries={"0": [Injury("0", "Q. Back", "QB", "Out", "")]}))[0][game.id]
    assert hurt.injury_points == (3.0, 0.0)
    assert hurt.projected_margin < healthy.projected_margin
    assert hurt.home_probability < healthy.home_probability


def test_backtest_reports_holdout_metrics_against_baseline():
    games = season()
    report = backtest("basketball", games, grid=(Params(), Params(learning_rate=0.03)))
    assert report["status"] == "ok"
    assert report["games"] + report["tuning_games"] >= 40
    assert set(report["model"]) == {"brier", "log_loss", "accuracy", "margin_mae", "total_mae"}
    assert set(report["baseline"]) == set(report["model"])
    assert report["params"]["learning_rate"] in {0.06, 0.03}
    assert sum(bucket["games"] for bucket in report["calibration"]) == report["games"]
    assert backtest("basketball", games[:60], grid=(Params(),))["status"] == "insufficient"


def test_backtest_ignores_injuries_and_markets():
    games = season()
    context = Context(injuries={"0": [Injury("0", "Q", "QB", "Out", "")]},
                      markets={games[-1].id: Market(0.9, -20, 100, 1, NOW)})
    grid = (Params(),)
    assert backtest("nfl", games, context, grid=grid)["model"] == backtest("nfl", games, grid=grid)["model"]


# ----- inputs -----

def hourly(start, temperatures, winds, rain):
    return {"time": [(start + timedelta(hours=index)).strftime("%Y-%m-%dT%H:%M") for index in range(len(temperatures))],
            "temperature_2m": temperatures, "wind_speed_10m": winds, "precipitation": rain}


def test_weather_window_averages_the_first_three_game_hours():
    start = datetime(2026, 10, 18, 0, tzinfo=timezone.utc)
    data = hourly(start, [50, 40, 30, 20, 10], [5, 10, 15, 20, 25], [0, 1, 2, 3, 4])
    assert weather_window(data, start + timedelta(hours=1)) == Weather(30, 15, 6)
    assert weather_window(data, start + timedelta(hours=3)) is None
    assert weather_window(data, start - timedelta(hours=1)) is None
    assert weather_window({**data, "wind_speed_10m": [5, None, 15, 20, 25]}, start) is None
    assert weather_window({}, start) is None


def inputs(rows=None, multi=None, get=None, key="key", fail=()):
    client = Recorder(rows, fail)
    return ModelInputsService(multi=multi or Mock(), client=client, get=get or Mock(), clock=lambda: NOW,
                              weather_key=key), client


def test_context_falls_back_when_input_tables_are_missing():
    service, _ = inputs(fail={"sport_model_weather", "sport_model_odds", "sport_model_injuries",
                              "sport_model_players", "api_sports_nfl_teams", "nflverse_team_game_efficiency"})
    context = service.context("nfl", NOW)
    assert (context.weather, context.markets, context.injuries, context.efficiency) == ({}, {}, {}, {})
    assert context.venues == VENUES["nfl"]


def test_context_keeps_recent_injuries_with_roster_positions():
    service, _ = inputs({
        "sport_model_players": [{"team_id": "1", "player_id": "7", "position": "QB"}],
        "sport_model_injuries": [
            {"team_id": "1", "player_id": "7", "player_name": "Q. Back", "status": "Out", "description": "Knee",
             "reported_on": "2026-10-12"},
            {"team_id": "1", "player_id": "8", "player_name": "Old", "status": "Out", "description": "",
             "reported_on": "2026-08-01"},
        ],
    })
    assert service.context("nfl", NOW).injuries == {"1": [Injury("1", "Q. Back", "QB", "Out", "Knee")]}


def test_refresh_odds_pages_league_odds_and_keeps_known_games():
    multi = Mock()
    line = {"bookmakers": [{"bets": [{"name": "Home/Away", "values": [
        {"value": "Home", "odd": "1.8"}, {"value": "Away", "odd": "2.0"}]}]}]}
    multi._request_page.side_effect = [([{"game": {"id": 5}, **line}], 2), ([{"game": {"id": 999}, **line}], 2)]
    service, client = inputs(multi=multi)
    game = ModelGame("5", NOW + timedelta(hours=4), "1", "A", None, "2", "B", None, "NS", NOW)
    assert service.refresh_odds("hockey:57", "hockey", 57, [game]) == 1
    assert multi._request_page.call_args_list[1].args[2]["page"] == 2
    rows = client.ops("sport_model_odds", "upsert")[0][1][0]
    assert [row["game_id"] for row in rows] == ["5"]
    assert rows[0]["home_probability"] == pytest.approx((1 / 1.8) / (1 / 1.8 + 1 / 2.0))


def test_refresh_football_odds_requests_only_pending_games_in_window():
    multi = Mock()
    multi._request.return_value = []
    service, _ = inputs(multi=multi)
    games = [
        ModelGame("1", NOW + timedelta(days=2), "1", "A", None, "2", "B", None, "NS", NOW),
        ModelGame("2", NOW + timedelta(days=9), "1", "A", None, "2", "B", None, "NS", NOW),
        ModelGame("3", NOW - timedelta(hours=2), "1", "A", 3, "2", "B", 7, "FT", NOW),
    ]
    assert service.refresh_odds("nfl", "nfl", 1, games) == 0
    assert [call.args[2] for call in multi._request.call_args_list] == [{"game": "1"}]


def test_refresh_injuries_refreshes_stale_rosters_and_replaces_reports():
    multi = Mock()
    multi._request.side_effect = lambda base, endpoint, params: {
        "players": [{"id": 7, "name": "Q. Back", "position": "QB", "group": "Offense"}],
        "injuries": [{"player": {"id": 7, "name": "Q. Back"}, "status": "Out", "description": "Knee", "date": "2026-10-12"},
                     {"player": {}, "status": "Out"}],
    }[endpoint]
    service, client = inputs({"sport_model_players": [
        {"team_id": "2", "fetched_at": (NOW - timedelta(days=1)).isoformat()}]}, multi=multi)
    game = ModelGame("1", NOW + timedelta(days=3), "1", "A", None, "2", "B", None, "NS", NOW)
    assert service.refresh_injuries([game]) == 2
    endpoints = [(call.args[1], call.args[2].get("team")) for call in multi._request.call_args_list]
    assert ("players", "1") in endpoints and ("players", "2") not in endpoints
    assert len(client.ops("sport_model_injuries", "delete")) == 2
    rows = client.ops("sport_model_injuries", "upsert")[0][1][0]
    assert rows == [{"competition": "nfl", "team_id": "1", "player_id": "7", "player_name": "Q. Back", "status": "Out",
                     "description": "Knee", "reported_on": "2026-10-12", "fetched_at": NOW.isoformat()}]


def test_refresh_weather_marks_observed_and_forecast_rows():
    outdoor = next(name for name, venue in VENUES["nfl"].items() if venue.outdoor)
    dome = next(name for name, venue in VENUES["nfl"].items() if not venue.outdoor)
    past = ModelGame("past", NOW - timedelta(days=2), "1", outdoor, 20, "2", "B", 17, "FT", NOW)
    soon = ModelGame("soon", NOW + timedelta(days=2), "1", outdoor, None, "2", "B", None, "NS", NOW)
    indoor = ModelGame("dome", NOW + timedelta(days=2), "3", dome, None, "4", "C", None, "NS", NOW)
    first = NOW.replace(hour=0) - timedelta(days=3)
    size = 24 * 20
    response = Mock()
    response.json.return_value = {"hourly": hourly(first, [40.0] * size, [10.0] * size, [0.5] * size)}
    get = Mock(return_value=response)
    service, client = inputs(get=get)
    assert service.refresh_weather("nfl", [past, soon, indoor]) == 2
    assert get.call_args.kwargs["params"]["apikey"] == "key"
    assert get.call_args.kwargs["params"]["past_days"] == 4
    rows = {row["game_id"]: row for row in client.ops("sport_model_weather", "upsert")[0][1][0]}
    assert rows["past"]["source"] == "observed" and rows["soon"]["source"] == "forecast"
    assert rows["soon"]["precipitation_mm"] == 1.5


def test_refresh_weather_skips_without_key_and_fresh_rows():
    get = Mock()
    service, _ = inputs(get=get, key="")
    assert service.refresh_weather("nfl", []) == 0
    outdoor = next(name for name, venue in VENUES["nfl"].items() if venue.outdoor)
    soon = ModelGame("soon", NOW + timedelta(days=2), "1", outdoor, None, "2", "B", None, "NS", NOW)
    service, _ = inputs({"sport_model_weather": [
        {"game_id": "soon", "source": "forecast", "fetched_at": (NOW - timedelta(hours=1)).isoformat()}]}, get=get)
    assert service.refresh_weather("nfl", [soon]) == 0
    get.assert_not_called()


# ----- projection service -----

def sample_projection(**changes):
    values = dict(
        home_score=24.444, away_score=20.06, home_probability=0.6421234, home_sample=8, away_sample=7,
        league_sample=158, updated=NOW, margin_adjustments=(("Rest days", 0.41234),), rest_days=(7, 6),
        travel_km=(0.0, 1499.6), weather=Weather(31.24, 18.0, 2.5, "forecast"),
        injuries=(Injury("1", "Q. Back", "QB", "Out", "Knee"), Injury("1", "Maybe", "WR", "Questionable", "")),
        injury_points=(3.0, 0.0), market=Market(0.6, -3.5, 44.5, 7, NOW), market_edge=0.0421234,
        cover_probability=0.53, over_probability=0.41,
    )
    values.update(changes)
    return ModelProjection(**values)


def test_projection_row_serialises_market_inputs_and_positive_injuries():
    game = ModelGame("99", NOW + timedelta(hours=3), "1", "Home", None, "2", "Away", None, "NS", NOW)
    row = projection_row("nfl", game, sample_projection(), NOW)
    assert row["home_score"] == 24.444 and row["home_probability"] == 0.642123
    assert (row["market_spread"], row["market_total"], row["market_books"]) == (-3.5, 44.5, 7)
    assert row["details"]["travel_km"] == [0.0, 1500.0]
    assert row["details"]["margin_adjustments"] == [["Rest days", 0.412]]
    assert row["details"]["weather"]["temperature_f"] == 31.2
    assert [item["player"] for item in row["details"]["injuries"]] == ["Q. Back"]
    assert row["model_version"] == engine.MODEL_VERSION
    empty = projection_row("nfl", game, sample_projection(market=None, weather=None, market_edge=None), NOW)
    assert empty["market_home_probability"] is None and empty["details"]["weather"] is None


def projection_service(games, client=None, inputs_service=None):
    data = Mock()
    data.cached.return_value = games
    if inputs_service is None:
        inputs_service = Mock()
        inputs_service.context.side_effect = lambda competition, now: Context(now=now)
    return ModelProjectionService(data=data, inputs=inputs_service, client=client or Recorder(), clock=lambda: NOW)


def test_store_keeps_only_games_in_the_site_window():
    games = [
        ModelGame("old", NOW - timedelta(hours=2), "1", "A", None, "2", "B", None, "NS", NOW),
        ModelGame("live", NOW - timedelta(minutes=30), "1", "A", None, "2", "B", None, "NS", NOW),
        ModelGame("far", NOW + timedelta(days=20), "1", "A", None, "2", "B", None, "NS", NOW),
        ModelGame("off", NOW + timedelta(days=1), "1", "A", None, "2", "B", None, "PST", NOW),
    ]
    client = Recorder()
    service = projection_service(games, client)
    run = ModelRun("nfl", games, {game.id: sample_projection() for game in games}, Params(), None, NOW)
    assert service.store(run) == 1
    assert [row["game_id"] for row in client.ops("sport_model_projections", "upsert")[0][1][0]] == ["live"]
    assert client.ops("sport_model_projections", "lt")[0][1] == ("start_at", (NOW - timedelta(days=7)).isoformat())


def test_project_uses_stored_tuned_parameters_and_backtest_report():
    stored = {"model_version": engine.MODEL_VERSION, "params": {"learning_rate": 0.03, "season_regression": 0.15},
              "report": {"status": "ok", "games": 10}}
    service = projection_service(season(), Recorder({"sport_model_backtests": [stored]}))
    run = service.project("basketball:12")
    assert run.params == Params(learning_rate=0.03, season_regression=0.15)
    assert run.backtest == {"status": "ok", "games": 10}
    stale = projection_service(season(), Recorder({"sport_model_backtests": [{**stored, "model_version": "old"}]}))
    assert stale.project("basketball:12").params == Params()
    with pytest.raises(ValueError):
        service.project("unknown")


def test_refresh_isolates_input_failures_and_still_stores_projections():
    games = season() + [upcoming(season(), start=NOW + timedelta(hours=2))]
    inputs_service = Mock()
    inputs_service.context.side_effect = lambda competition, now: Context(now=now)
    inputs_service.refresh_odds.side_effect = RuntimeError("provider down")
    inputs_service.refresh_weather.return_value = 3
    service = projection_service(games, inputs_service=inputs_service)
    summary = service.refresh("basketball:12", odds=True, injuries=True, weather=True)
    assert summary == {"odds": "failed: provider down", "weather": 3, "projections": 1}
    inputs_service.refresh_injuries.assert_not_called()


def test_run_backtest_stores_only_successful_reports():
    client = Recorder()
    report = projection_service(season(), client).run_backtest("basketball:12")
    assert report["status"] == "ok"
    stored = client.ops("sport_model_backtests", "upsert")[0][1][0]
    assert stored["params"] == report["params"] and stored["model_version"] == engine.MODEL_VERSION
    client = Recorder()
    assert projection_service(season(count=50), client).run_backtest("basketball:12")["status"] == "insufficient"
    assert client.ops("sport_model_backtests", "upsert") == []


# ----- cog -----

def cog(service=None, clock=None):
    service = service or Mock()
    alert = AsyncMock()
    times = clock or [NOW]
    instance = ModelProjections(Mock(), service=service, enabled=True, staff_alert=alert, clock=lambda: times[0])
    return instance, service, alert, times


def test_due_uses_slower_football_odds_interval():
    instance, *_ = cog()
    instance.last[("nfl", "odds")] = NOW - timedelta(hours=2)
    instance.last[("hockey:57", "odds")] = NOW - timedelta(hours=2)
    instance.last[("nfl", "weather")] = NOW - timedelta(hours=1)
    assert not instance._due("nfl", "odds", NOW)
    assert instance._due("hockey:57", "odds", NOW)
    assert not instance._due("nfl", "weather", NOW)
    assert instance._due("nfl", "injuries", NOW)


def test_refresh_competition_retries_missing_backtest_at_most_every_twelve_hours():
    service = Mock()
    service.stored_backtest.return_value = None
    service.run_backtest.side_effect = RuntimeError("boom")
    service.refresh.return_value = {"odds": 2, "injuries": "failed: x", "weather": 0, "projections": 4}
    instance, _, alert, times = cog(service)
    assert asyncio.run(instance.refresh_competition("nfl"))["projections"] == 4
    alert.assert_awaited_once()
    assert alert.await_args.args[0] == "model_backtest:nfl"
    assert ("nfl", "injuries") not in instance.last and instance.last[("nfl", "odds")] == NOW
    times[0] = NOW + timedelta(hours=1)
    asyncio.run(instance.refresh_competition("nfl"))
    assert service.run_backtest.call_count == 1
    assert service.refresh.call_args.kwargs == {"odds": False, "injuries": True, "weather": False}
    times[0] = NOW + BACKTEST_RETRY
    asyncio.run(instance.refresh_competition("nfl"))
    assert service.run_backtest.call_count == 2
    assert alert.await_count == 2


def test_staff_alerts_are_throttled_per_job():
    instance, _, alert, times = cog()
    asyncio.run(instance.report_failure("model_projections:nfl"))
    asyncio.run(instance.report_failure("model_projections:nfl"))
    asyncio.run(instance.report_failure("model_projections:nba"))
    assert alert.await_count == 2
    times[0] = NOW + ALERT_INTERVAL
    asyncio.run(instance.report_failure("model_projections:nfl"))
    assert alert.await_count == 3


def test_refresh_loop_isolates_league_failures(monkeypatch):
    monkeypatch.setattr(cog_module, "COMPETITIONS", {"nfl": None, "hockey:57": None})
    instance, _, alert, _ = cog()
    calls = []

    async def refresh(competition):
        calls.append(competition)
        if competition == "nfl":
            raise RuntimeError("down")
        return {}
    instance.refresh_competition = refresh
    asyncio.run(instance.refresh_projections.coro(instance))
    assert calls == ["nfl", "hockey:57"]
    assert alert.await_args.args[0] == "model_projections:nfl"


def test_disabled_cog_does_not_start_jobs():
    instance = ModelProjections(Mock(), service=Mock(), enabled=False, staff_alert=AsyncMock())
    instance.start_jobs()
    assert not instance.refresh_projections.is_running()
    assert math.isclose(instance.refresh_projections.minutes, 30)
