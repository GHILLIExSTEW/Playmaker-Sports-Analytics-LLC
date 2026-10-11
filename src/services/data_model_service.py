from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone

from src.datetime_utils import parse_iso_datetime
from src.services.api_budget_service import ApiBudgetDenied
from src.config import API_SPORTS_BUDGET_ENABLED
from src.services.api_sports_service import ApiSportsService, nfl_season_for_date, normalize_game
from src.services.api_sports_multi_service import (
    ApiSportsMultiService, DATE_PRODUCTS, MODEL_COMPETITIONS, TRACKER_TIMEZONE,
    model_history_seasons, normalize_event,
)

COMPETITIONS = {
    "nfl": ("NFL", "nfl", 1),
    **{f"{sport}:{league}": (name, sport, league) for sport, league, name in MODEL_COMPETITIONS},
}
FINAL = {"FT", "AOT", "COMPLETED", "FINISHED"}


@dataclass(frozen=True)
class ModelGame:
    id: str
    start: datetime
    home_id: str | None
    home: str
    home_score: float | None
    away_id: str | None
    away: str
    away_score: float | None
    status: str
    synced: datetime


@dataclass(frozen=True)
class Projection:
    home_score: float
    away_score: float
    home_probability: float
    home_sample: int
    away_sample: int
    league_sample: int
    updated: datetime


def model_score(value: object) -> float | None:
    if isinstance(value, dict):
        return model_score(value.get("total"))
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    if not re.fullmatch(r"\d+(?:\.\d+)?", str(value).strip()):
        return None
    result = float(value)
    return result if math.isfinite(result) and result >= 0 else None


def team_key(team_id: str | None, name: str) -> str:
    return f"id:{team_id}" if team_id else f"name:{name.strip().lower()}"


def unique_model_games(games: list[ModelGame]) -> list[ModelGame]:
    unique: dict[tuple[datetime, str, str], ModelGame] = {}

    def rank(game: ModelGame) -> tuple[bool, datetime, str]:
        return (game.status.upper() in FINAL and game.home_score is not None and game.away_score is not None,
                game.synced, game.id)

    for game in games:
        key = (game.start, team_key(game.home_id, game.home), team_key(game.away_id, game.away))
        existing = unique.get(key)
        if existing is None or rank(game) > rank(existing):
            unique[key] = game
    return list(unique.values())


def project_matchup(games: list[ModelGame], matchup: ModelGame) -> Projection | None:
    previous = sorted(
        (game for game in unique_model_games(games) if game.start < matchup.start and game.status.upper() in FINAL
         and game.home_score is not None and game.away_score is not None),
        key=lambda game: game.start, reverse=True,
    )
    home_key = team_key(matchup.home_id, matchup.home)
    away_key = team_key(matchup.away_id, matchup.away)

    def history(key: str) -> list[tuple[float, float]]:
        rows = []
        for game in previous:
            assert game.home_score is not None and game.away_score is not None
            if team_key(game.home_id, game.home) == key:
                rows.append((game.home_score, game.away_score))
            elif team_key(game.away_id, game.away) == key:
                rows.append((game.away_score, game.home_score))
        return rows[:10]

    home_history, away_history = history(home_key), history(away_key)
    margins = []
    for game in previous[:1000]:
        assert game.home_score is not None and game.away_score is not None
        margins.append(game.home_score - game.away_score)
    if len(home_history) < 3 or len(away_history) < 3 or len(margins) < 30:
        return None
    mean_margin = sum(margins) / len(margins)
    deviation = math.sqrt(sum((margin - mean_margin) ** 2 for margin in margins) / len(margins))
    if not math.isfinite(deviation) or deviation == 0:
        return None
    home_score = (sum(row[0] for row in home_history) / len(home_history)
                  + sum(row[1] for row in away_history) / len(away_history)) / 2
    away_score = (sum(row[0] for row in away_history) / len(away_history)
                  + sum(row[1] for row in home_history) / len(home_history)) / 2
    value = (home_score - away_score) / deviation
    x = abs(value) / math.sqrt(2)
    t = 1 / (1 + 0.3275911 * x)
    erf = 1 - (((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t
                  - 0.284496736) * t + 0.254829592) * t) * math.exp(-x * x)
    probability = (1 + (1 if value > 0 else -1 if value < 0 else 0) * erf) / 2
    updated = max(game.synced for game in previous if
                  team_key(game.home_id, game.home) in {home_key, away_key}
                  or team_key(game.away_id, game.away) in {home_key, away_key})
    return Projection(home_score, away_score, probability, len(home_history), len(away_history), len(margins), updated)


def from_row(row: dict, nfl: bool) -> ModelGame:
    def identity(value: object) -> str | None:
        return str(value) if value is not None else None

    home = row.get("home_team_name" if nfl else "home_name")
    away = row.get("away_team_name" if nfl else "away_name")
    if not isinstance(home, str) or not home.strip() or not isinstance(away, str) or not away.strip():
        raise RuntimeError("Provider model game is missing team names.")
    start = parse_iso_datetime(row["kickoff_at" if nfl else "start_at"])
    synced = parse_iso_datetime(row["synced_at"])
    if start.tzinfo is None or synced.tzinfo is None:
        raise RuntimeError("Provider model timestamps must include a timezone.")
    return ModelGame(
        str(row["game_id" if nfl else "event_id"]), start,
        identity(row.get("home_team_id" if nfl else "home_id")), home, model_score(row.get("home_score")),
        identity(row.get("away_team_id" if nfl else "away_id")), away, model_score(row.get("away_score")),
        str(row["status_short" if nfl else "status_code"]), synced,
    )


class DataModelService:
    """Fresh, fully paged league history; same score baseline as the website."""

    def __init__(self, nfl=None, multi=None, clock=None):
        self.nfl = nfl or ApiSportsService()
        self.multi = multi or ApiSportsMultiService()
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def refresh(self, competition: str) -> tuple[list[ModelGame], int, datetime]:
        if competition not in COMPETITIONS:
            raise ValueError("Select a supported model league.")
        if not API_SPORTS_BUDGET_ENABLED:
            raise ApiBudgetDenied("Enable API_SPORTS_BUDGET_ENABLED before using live model refresh.")
        _, sport, league = COMPETITIONS[competition]
        today = self.clock().astimezone(TRACKER_TIMEZONE).date()
        nfl = sport == "nfl"
        seasons = ((str(nfl_season_for_date(today) - 1), str(nfl_season_for_date(today)))
                   if nfl else model_history_seasons(sport, today))
        games: dict[str, ModelGame] = {}
        requests_used = 0
        for season in seasons:
            page, total = 1, 1
            while page <= total:
                params = {"league": league, "season": int(season) if nfl else season}
                if page > 1:
                    params["page"] = page
                if nfl:
                    rows, total = self.nfl._request_page("games", params)
                else:
                    base_url, endpoint = DATE_PRODUCTS[sport]
                    rows, total = self.multi._request_page(base_url, endpoint, params)
                requests_used += 1
                synced = self.clock()
                normalized = [normalize_game(row, synced) if nfl else normalize_event(sport, row, synced) for row in rows]
                for row in normalized:
                    actual_league = row.get("league_id")
                    if str(actual_league) != str(league) or str(row.get("season")) != season:
                        raise RuntimeError("Provider returned games outside the requested league or season.")
                    game = from_row(row, nfl)
                    games[game.id] = game
                if nfl:
                    self.nfl._upsert("api_sports_nfl_games", normalized, "game_id")
                else:
                    self.multi._store_events(normalized)
                page += 1
        return sorted(unique_model_games(list(games.values())), key=lambda game: (game.start, game.id)), requests_used, self.clock()

    @staticmethod
    def choices(games: list[ModelGame], day: date, team: str) -> list[ModelGame]:
        return [game for game in games if game.start.astimezone(TRACKER_TIMEZONE).date() == day
                and (not team or team.casefold() in f"{game.home} {game.away}".casefold())
                and game.status.upper() not in {"CANC", "ABD", "WO"}]
