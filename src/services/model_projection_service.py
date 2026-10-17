"""Runs the ratings-v2 engine for a league and stores projections/backtests for the site and bot."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import Lock

from src.services.data_model_service import COMPETITIONS, DataModelService, ModelGame
from src.services.model_engine import (
    MODEL_VERSION, SKIPPED_STATUSES, Context, ModelProjection, Params, backtest, run_model,
)
from src.services.model_inputs_service import ModelInputsService
from src.services.supabase_service import supabase_service

logger = logging.getLogger(__name__)
STORE_PAST = timedelta(hours=1)
STORE_AHEAD = timedelta(days=14)
KEEP_HISTORY = timedelta(days=7)


@dataclass
class ModelRun:
    competition: str
    games: list[ModelGame]
    projections: dict[str, ModelProjection]
    params: Params
    backtest: dict | None
    generated_at: datetime
    context: Context = field(default_factory=Context)


def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(float(value), digits)


def projection_row(competition: str, game: ModelGame, projection: ModelProjection, generated_at: datetime) -> dict:
    market = projection.market
    weather = projection.weather
    return {
        "competition": competition, "game_id": game.id, "start_at": game.start.isoformat(),
        "status_code": game.status, "home_id": game.home_id, "home_name": game.home,
        "away_id": game.away_id, "away_name": game.away,
        "home_score": _round(projection.home_score), "away_score": _round(projection.away_score),
        "home_probability": _round(projection.home_probability, 6), "distribution": projection.distribution,
        "margin_sd": _round(projection.margin_sd), "total_sd": _round(projection.total_sd),
        "home_sample": projection.home_sample, "away_sample": projection.away_sample,
        "league_sample": projection.league_sample,
        "market_home_probability": _round(market.home_probability, 6) if market else None,
        "market_spread": market.home_spread if market else None,
        "market_total": market.total if market else None,
        "market_books": market.books if market else None,
        "market_fetched_at": market.fetched_at.isoformat() if market else None,
        "market_edge": _round(projection.market_edge, 6),
        "cover_probability": _round(projection.cover_probability, 6),
        "over_probability": _round(projection.over_probability, 6),
        "details": {
            "margin_adjustments": [[name, _round(value, 3)] for name, value in projection.margin_adjustments],
            "total_adjustments": [[name, _round(value, 3)] for name, value in projection.total_adjustments],
            "rest_days": list(projection.rest_days),
            "travel_km": [_round(value, 0) for value in projection.travel_km],
            "weather": None if weather is None else {
                "temperature_f": _round(weather.temperature_f, 1), "wind_mph": _round(weather.wind_mph, 1),
                "precipitation_mm": _round(weather.precipitation_mm, 1), "source": weather.source,
            },
            "injuries": [{
                "team_id": item.team_id, "player": item.player, "position": item.position,
                "status": item.status, "description": item.description, "points": _round(item.points, 2),
            } for item in projection.injuries if item.points > 0],
            "injury_points": [_round(value, 2) for value in projection.injury_points],
        },
        "model_version": MODEL_VERSION, "data_updated_at": projection.updated.isoformat(),
        "generated_at": generated_at.isoformat(),
    }


class ModelProjectionService:
    def __init__(self, data: DataModelService | None = None, inputs: ModelInputsService | None = None,
                 client=None, clock=None):
        self.data = data or DataModelService()
        self.inputs = inputs or ModelInputsService()
        self.client = client
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._locks = {competition: Lock() for competition in COMPETITIONS}

    def _db(self):
        return self.client or supabase_service._ensure_client()

    def stored_backtest(self, competition: str) -> dict | None:
        try:
            rows = supabase_service._execute(
                lambda: self._db().table("sport_model_backtests").select("model_version,params,report,generated_at")
                .eq("competition", competition).limit(1).execute()).data or []
        except Exception as error:  # noqa: BLE001 - the engine falls back to default parameters
            logger.warning("Model backtest unavailable for %s: %s", competition, error)
            return None
        if not rows or rows[0].get("model_version") != MODEL_VERSION:
            return None
        return rows[0]

    def project(self, competition: str, games: list[ModelGame] | None = None) -> ModelRun:
        """Point-in-time projections using stored history, inputs and tuned parameters."""
        if competition not in COMPETITIONS:
            raise ValueError("Select a supported model league.")
        sport = COMPETITIONS[competition][1]
        games = games if games is not None else self.data.cached(competition)
        now = self.clock()
        context = self.inputs.context(competition, now)
        stored = self.stored_backtest(competition)
        params = Params.from_dict(stored.get("params")) if stored else Params()
        projections, _ = run_model(sport, games, context, params)
        return ModelRun(competition, games, projections, params, stored.get("report") if stored else None, now, context)

    def store(self, run: ModelRun) -> int:
        now = run.generated_at
        rows = [projection_row(run.competition, game, run.projections[game.id], now) for game in run.games
                if game.id in run.projections and now - STORE_PAST < game.start <= now + STORE_AHEAD
                and game.status.upper() not in SKIPPED_STATUSES]
        for start in range(0, len(rows), 500):
            chunk = rows[start:start + 500]
            supabase_service._execute(lambda: self._db().table("sport_model_projections")
                                      .upsert(chunk, on_conflict="competition,game_id").execute())
        # Remove estimates that this run can no longer produce, rather than leaving old predictions visible.
        supabase_service._execute(lambda: self._db().table("sport_model_projections").delete()
                                  .eq("competition", run.competition)
                                  .gt("start_at", (now - STORE_PAST).isoformat())
                                  .lte("start_at", (now + STORE_AHEAD).isoformat())
                                  .lt("generated_at", now.isoformat()).execute())
        supabase_service._execute(lambda: self._db().table("sport_model_projections").delete()
                                  .eq("competition", run.competition)
                                  .lt("start_at", (now - KEEP_HISTORY).isoformat()).execute())
        return len(rows)

    def refresh(self, competition: str, *, odds: bool = False, injuries: bool = False, weather: bool = False) -> dict:
        """Refresh requested inputs (each failure is isolated), then project and store the league."""
        with self._locks[competition]:
            _, sport, league = COMPETITIONS[competition]
            games = self.data.cached(competition)
            summary: dict[str, object] = {}
            steps = (
                ("odds", odds, lambda: self.inputs.refresh_odds(competition, sport, league, games)),
                ("injuries", injuries and competition == "nfl", lambda: self.inputs.refresh_injuries(games)),
                ("weather", weather, lambda: self.inputs.refresh_weather(competition, games)),
            )
            for name, enabled, step in steps:
                if not enabled:
                    continue
                try:
                    summary[name] = step()
                except Exception as error:  # noqa: BLE001 - one input failure must not stop projections
                    logger.warning("model_input_refresh_failed competition=%s input=%s", competition, name, exc_info=True)
                    summary[name] = f"failed: {error}"
            run = self.project(competition, games)
            summary["projections"] = self.store(run)
            return summary

    def process_team_pull(self) -> bool:
        """Recalculate one saved pull using the same engine, without additional provider calls."""
        # A claim is not idempotent: a transport retry could claim a second job.
        job = supabase_service._execute_once(lambda: self._db().rpc("claim_capper_model_refresh").execute()).data
        if job is None:
            return False
        if not isinstance(job, dict) or job.get("competition") not in COMPETITIONS:
            raise ValueError("Invalid capper model refresh job")
        args = {"p_request_id": job["request_id"], "p_claim_token": job["claim_token"]}
        try:
            summary = self.refresh(job["competition"])
        except Exception:
            saved = supabase_service._execute(
                lambda: self._db().rpc("finish_capper_model_refresh", {**args, "p_projections": None}).execute()).data
            if saved is not True:
                logger.error("capper_model_refresh_failure_not_saved request_id=%s", job["request_id"])
            raise
        saved = supabase_service._execute(lambda: self._db().rpc(
            "finish_capper_model_refresh", {**args, "p_projections": summary["projections"]}).execute()).data
        if saved is not True:
            raise RuntimeError("Capper model refresh completion was not saved")
        logger.info("capper_model_refreshed request_id=%s competition=%s projections=%s",
                    job["request_id"], job["competition"], summary["projections"])
        return True

    def run_backtest(self, competition: str) -> dict:
        with self._locks[competition]:
            sport = COMPETITIONS[competition][1]
            games = self.data.cached(competition)
            context = self.inputs.context(competition, self.clock())
            report = backtest(sport, games, context)
            if report.get("status") == "ok":
                row = {"competition": competition, "model_version": MODEL_VERSION, "params": report["params"],
                       "report": report, "generated_at": self.clock().isoformat()}
                supabase_service._execute(lambda: self._db().table("sport_model_backtests")
                                          .upsert(row, on_conflict="competition").execute())
            return report


model_projection_service = ModelProjectionService()
