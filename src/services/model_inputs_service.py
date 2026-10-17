"""External model inputs: sportsbook consensus, NFL injuries, venue weather and NFL EPA."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

import requests

from src.config import OPEN_METEO_API_KEY
from src.datetime_utils import parse_iso_datetime
from src.services.api_sports_multi_service import (
    ApiSportsMultiService, DATE_PRODUCTS, TRACKER_TIMEZONE, model_history_seasons,
)
from src.services.api_sports_service import nfl_season_for_date
from src.services.model_engine import (
    FINAL_STATUSES, SKIPPED_STATUSES, Context, Efficiency, Injury, Market, Weather, consensus_market,
)
from src.services.supabase_service import supabase_service
from src.services.team_locations import VENUES, Venue

FOOTBALL_BASE = "https://v1.american-football.api-sports.io"
FORECAST_URL = "https://customer-api.open-meteo.com/v1/forecast"
FORECAST_DAYS = 16
WEATHER_PAST_DAYS = 92
GAME_HOURS = 3
ODDS_DAYS = {"nfl": 7, "ncaa:2": 4}
INJURY_DAYS = 8
INJURY_RECENT_DAYS = 21
ROSTER_MAX_AGE = timedelta(days=7)
FORECAST_MAX_AGE = timedelta(hours=3)
logger = logging.getLogger(__name__)


def _pending(game) -> bool:
    return game.status.upper() not in FINAL_STATUSES | SKIPPED_STATUSES


def _float(value: object) -> float | None:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def weather_window(hourly: dict, start: datetime) -> Weather | None:
    """Mean temperature/wind and summed precipitation across the first three game hours."""
    times = hourly.get("time") or []
    if not times:
        return None
    first = datetime.fromisoformat(times[0]).replace(tzinfo=timezone.utc)
    index = int((start.astimezone(timezone.utc) - first).total_seconds() // 3600)
    if index < 0 or index + GAME_HOURS > len(times):
        return None
    window = slice(index, index + GAME_HOURS)
    temperatures = [_float(value) for value in (hourly.get("temperature_2m") or [])[window]]
    winds = [_float(value) for value in (hourly.get("wind_speed_10m") or [])[window]]
    rain = [_float(value) for value in (hourly.get("precipitation") or [])[window]]
    if any(value is None for value in temperatures + winds + rain) or len(temperatures) != GAME_HOURS:
        return None
    return Weather(sum(temperatures) / GAME_HOURS, sum(winds) / GAME_HOURS, sum(rain))  # type: ignore[arg-type]


class ModelInputsService:
    def __init__(self, multi=None, client=None, get=None, clock=None, weather_key: str | None = None):
        self.multi = multi or ApiSportsMultiService()
        self.client = client
        self.get = get or requests.get
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.weather_key = OPEN_METEO_API_KEY if weather_key is None else weather_key

    def _db(self):
        return self.client or supabase_service._ensure_client()

    def _select(self, table: str, columns: str, **filters) -> list[dict]:
        rows: list[dict] = []
        offset = 0
        while True:
            def page(start=offset):
                query = self._db().table(table).select(columns)
                for column, value in filters.items():
                    query = query.in_(column, value) if isinstance(value, (list, tuple, set)) else query.eq(column, value)
                return query.range(start, start + 999).execute()

            batch = supabase_service._execute(page).data or []
            rows.extend(batch)
            if len(batch) < 1000:
                return rows
            offset += 1000

    def _upsert(self, table: str, rows: list[dict], conflict: str) -> None:
        for start in range(0, len(rows), 500):
            chunk = rows[start:start + 500]
            supabase_service._execute(lambda: self._db().table(table).upsert(chunk, on_conflict=conflict).execute())

    def _optional(self, table: str, columns: str, **filters) -> list[dict]:
        """Model inputs are optional; a missing or failing input table must not block projections."""
        try:
            return self._select(table, columns, **filters)
        except Exception as error:  # noqa: BLE001 - logged and skipped by design
            logger.warning("Model input %s unavailable: %s", table, error)
            return []

    # ----- loaders -----

    def efficiency(self) -> dict[str, list[Efficiency]]:
        teams = self._optional("api_sports_nfl_teams", "team_id,code")
        by_code = {str(row["code"]).strip().upper(): str(row["team_id"]) for row in teams if row.get("code")}
        result: dict[str, list[Efficiency]] = {}
        for row in self._optional("nflverse_team_game_efficiency",
                                  "game_date,team_code,offense_epa_sum,offense_plays,defense_epa_sum,defense_plays"):
            team_id = by_code.get(str(row.get("team_code") or "").strip().upper())
            values = [_float(row.get(key)) for key in ("offense_epa_sum", "offense_plays", "defense_epa_sum", "defense_plays")]
            if team_id is None or any(value is None for value in values) or not row.get("game_date"):
                continue
            result.setdefault(team_id, []).append(Efficiency(
                date.fromisoformat(str(row["game_date"])[:10]), values[0], int(values[1]), values[2], int(values[3])))
        for rows in result.values():
            rows.sort(key=lambda item: item.day)
        return result

    def context(self, competition: str, now: datetime | None = None) -> Context:
        now = now or self.clock()
        weather = {
            str(row["game_id"]): Weather(float(row["temperature_f"]), float(row["wind_mph"]),
                                         float(row["precipitation_mm"]), row.get("source") or "forecast")
            for row in self._optional("sport_model_weather", "game_id,temperature_f,wind_mph,precipitation_mm,source",
                                      competition=competition)
        }
        markets = {
            str(row["game_id"]): Market(_float(row.get("home_probability")), _float(row.get("home_spread")),
                                        _float(row.get("total")), int(row.get("books") or 0),
                                        parse_iso_datetime(row["fetched_at"]))
            for row in self._optional("sport_model_odds", "game_id,home_probability,home_spread,total,books,fetched_at",
                                      competition=competition)
        }
        injuries: dict[str, list[Injury]] = {}
        efficiency: dict[str, list[Efficiency]] = {}
        if competition == "nfl":
            efficiency = self.efficiency()
            positions = {(str(row["team_id"]), str(row["player_id"])): row.get("position")
                         for row in self._optional("sport_model_players", "team_id,player_id,position", competition="nfl")}
            cutoff = (now - timedelta(days=INJURY_RECENT_DAYS)).date()
            for row in self._optional("sport_model_injuries",
                                      "team_id,player_id,player_name,status,description,reported_on", competition="nfl"):
                reported = row.get("reported_on")
                if reported and date.fromisoformat(str(reported)[:10]) < cutoff:
                    continue
                team_id = str(row["team_id"])
                injuries.setdefault(team_id, []).append(Injury(
                    team_id, row.get("player_name") or "Unknown player",
                    positions.get((team_id, str(row.get("player_id")))), row.get("status") or "",
                    row.get("description") or ""))
        return Context(venues=VENUES.get(competition, {}), weather=weather, efficiency=efficiency,
                       injuries=injuries, markets=markets, now=now)

    # ----- refreshers -----

    def refresh_odds(self, competition: str, sport: str, league: int, games: list) -> int:
        now = self.clock()
        fetched: dict[str, Market] = {}
        bookmaker_snapshots: dict[str, list[dict]] = {}
        known = {game.id for game in games}
        if sport in {"nfl", "ncaa"}:
            horizon = now + timedelta(days=ODDS_DAYS.get(competition, 4))
            for game in games:
                if _pending(game) and now - timedelta(hours=1) <= game.start <= horizon:
                    for row in self.multi._request(FOOTBALL_BASE, "odds", {"game": game.id}):
                        bookmaker_snapshots.setdefault(game.id, []).extend(row.get("bookmakers") or [])
                        market = consensus_market(row.get("bookmakers") or [], now)
                        if market is not None:
                            fetched[game.id] = market
        else:
            base_url, _ = DATE_PRODUCTS[sport]
            season = model_history_seasons(sport, now.astimezone(TRACKER_TIMEZONE).date())[1]
            page, total = 1, 1
            while page <= total:
                params = {"league": league, "season": season, **({"page": page} if page > 1 else {})}
                rows, total = self.multi._request_page(base_url, "odds", params)
                for row in rows:
                    game_id = str((row.get("game") or {}).get("id") or "")
                    if game_id in known:
                        bookmaker_snapshots.setdefault(game_id, []).extend(row.get("bookmakers") or [])
                    market = consensus_market(row.get("bookmakers") or [], now)
                    if game_id in known and market is not None:
                        fetched[game_id] = market
                page += 1
        self._upsert("research_odds", [{
            "competition": competition, "game_id": game_id, "bookmakers": bookmakers,
            "fetched_at": now.isoformat(),
        } for game_id, bookmakers in bookmaker_snapshots.items()], "competition,game_id")
        self._upsert("sport_model_odds", [{
            "competition": competition, "game_id": game_id, "home_probability": market.home_probability,
            "home_spread": market.home_spread, "total": market.total, "books": market.books,
            "fetched_at": market.fetched_at.isoformat(),
        } for game_id, market in fetched.items()], "competition,game_id")
        return len(fetched)

    def refresh_injuries(self, games: list) -> int:
        """NFL injury reports plus roster positions for teams playing within eight days."""
        now = self.clock()
        teams = sorted({team for game in games if _pending(game) and now <= game.start <= now + timedelta(days=INJURY_DAYS)
                        for team in (game.home_id, game.away_id) if team})
        if not teams:
            return 0
        season = nfl_season_for_date(now.astimezone(TRACKER_TIMEZONE).date())
        rosters = {}
        for row in self._select("sport_model_players", "team_id,fetched_at", competition="nfl", team_id=teams):
            fetched = parse_iso_datetime(row["fetched_at"])
            rosters[str(row["team_id"])] = max(rosters.get(str(row["team_id"]), fetched), fetched)
        count = 0
        for team in teams:
            if team not in rosters or now - rosters[team] > ROSTER_MAX_AGE:
                players = self.multi._request(FOOTBALL_BASE, "players", {"team": team, "season": season})
                self._upsert("sport_model_players", [{
                    "competition": "nfl", "team_id": team, "player_id": str(player["id"]),
                    "player_name": player.get("name"), "position": player.get("position"),
                    "position_group": player.get("group"), "fetched_at": now.isoformat(),
                } for player in players if player.get("id") is not None], "competition,team_id,player_id")
            reports = self.multi._request(FOOTBALL_BASE, "injuries", {"team": team})
            rows = {}
            for report in reports:
                player = report.get("player") or {}
                if player.get("id") is None:
                    continue
                rows[str(player["id"])] = {
                    "competition": "nfl", "team_id": team, "player_id": str(player["id"]),
                    "player_name": player.get("name"), "status": report.get("status"),
                    "description": report.get("description"),
                    "reported_on": str(report.get("date"))[:10] if report.get("date") else None,
                    "fetched_at": now.isoformat(),
                }
            supabase_service._execute(lambda: self._db().table("sport_model_injuries").delete()
                                      .eq("competition", "nfl").eq("team_id", team).execute())
            self._upsert("sport_model_injuries", list(rows.values()), "competition,team_id,player_id")
            count += len(rows)
        return count

    def refresh_weather(self, competition: str, games: list) -> int:
        """Open-Meteo weather for open-air venues: recent analysis for past games, forecasts upcoming."""
        if not self.weather_key:
            return 0
        venues: dict[str, Venue] = VENUES.get(competition, {})
        now = self.clock()
        stored = {str(row["game_id"]): row for row in self._select(
            "sport_model_weather", "game_id,source,fetched_at", competition=competition)}
        earliest = now - timedelta(days=WEATHER_PAST_DAYS - 1)
        latest = now + timedelta(days=FORECAST_DAYS - 1)
        needed = []
        for game in games:
            venue = venues.get(game.home)
            if venue is None or not venue.outdoor or game.status.upper() in SKIPPED_STATUSES:
                continue
            if not earliest <= game.start <= latest:
                continue
            row = stored.get(game.id)
            finished = game.start + timedelta(hours=GAME_HOURS) <= now
            if row is None or (row.get("source") != "observed" and (
                    finished or now - parse_iso_datetime(row["fetched_at"]) > FORECAST_MAX_AGE)):
                needed.append((game, venue, finished))
        if not needed:
            return 0
        locations = sorted({(venue.latitude, venue.longitude) for _, venue, _ in needed})
        past_days = min(WEATHER_PAST_DAYS, max(1, (now - min(game.start for game, _, _ in needed)).days + 2))
        response = self.get(FORECAST_URL, params={
            "latitude": ",".join(str(lat) for lat, _ in locations),
            "longitude": ",".join(str(lon) for _, lon in locations),
            "hourly": "temperature_2m,wind_speed_10m,precipitation",
            "temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "precipitation_unit": "mm",
            "timezone": "UTC", "past_days": past_days, "forecast_days": FORECAST_DAYS, "apikey": self.weather_key,
        }, timeout=60)
        response.raise_for_status()
        payload = response.json()
        payload = payload if isinstance(payload, list) else [payload]
        if len(payload) != len(locations):
            raise RuntimeError("Open-Meteo returned an unexpected number of locations.")
        hourly = {location: item.get("hourly") or {} for location, item in zip(locations, payload)}
        rows = []
        for game, venue, finished in needed:
            weather = weather_window(hourly[(venue.latitude, venue.longitude)], game.start)
            if weather is not None:
                rows.append({
                    "competition": competition, "game_id": game.id, "temperature_f": round(weather.temperature_f, 2),
                    "wind_mph": round(weather.wind_mph, 2), "precipitation_mm": round(weather.precipitation_mm, 2),
                    "source": "observed" if finished else "forecast", "fetched_at": now.isoformat(),
                })
        self._upsert("sport_model_weather", rows, "competition,game_id")
        return len(rows)
