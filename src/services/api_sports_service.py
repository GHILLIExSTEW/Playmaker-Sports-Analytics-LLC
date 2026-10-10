from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from src.config import API_SPORTS_BASE_URL, API_SPORTS_KEY
from src.datetime_utils import parse_iso_datetime
from src.services.api_budget_service import reserve_request
from src.services.supabase_service import supabase_service

NFL_LEAGUE_ID = 1
TRACKER_TIMEZONE = ZoneInfo("America/New_York")
FINAL_STATUSES = {"FT", "AOT", "CANC", "ABD", "WO"}


def nfl_season_for_date(day: date) -> int:
    return day.year if day.month >= 8 else day.year - 1


def normalize_game(game: dict, synced_at: datetime) -> dict:
    game_data = game.get("game") or {}
    date_data = game_data.get("date") or {}
    teams = game.get("teams") or {}
    home = teams.get("home") or {}
    away = teams.get("away") or {}
    league = game.get("league") or {}
    status = game_data.get("status") or {}
    scores = game.get("scores") or {}
    timestamp = date_data.get("timestamp")
    kickoff_at = datetime.fromtimestamp(int(timestamp), timezone.utc) if timestamp else datetime.fromisoformat(
        f"{date_data.get('date')}T{date_data.get('time', '00:00')}:00+00:00"
    )
    venue = game_data.get("venue") or {}
    return {
        "game_id": int(game_data["id"]),
        "league_id": int(league.get("id") or NFL_LEAGUE_ID),
        "season": int(league.get("season") or nfl_season_for_date(synced_at.astimezone(TRACKER_TIMEZONE).date())),
        "stage": game_data.get("stage"),
        "week": str(game_data.get("week") or ""),
        "kickoff_at": kickoff_at.isoformat(),
        "venue_name": venue.get("name"),
        "venue_city": venue.get("city"),
        "status_short": str(status.get("short") or "UNK"),
        "status_long": str(status.get("long") or "Unknown"),
        "home_team_id": home.get("id"),
        "home_team_name": str(home.get("name") or "Home team"),
        "home_team_logo": home.get("logo"),
        "away_team_id": away.get("id"),
        "away_team_name": str(away.get("name") or "Away team"),
        "away_team_logo": away.get("logo"),
        "home_score": (scores.get("home") or {}).get("total"),
        "away_score": (scores.get("away") or {}).get("total"),
        "scores": scores,
        "synced_at": synced_at.isoformat(),
    }


def normalize_standing(standing: dict, synced_at: datetime) -> dict:
    league = standing.get("league") or {}
    team = standing.get("team") or {}
    points = standing.get("points") or {}
    return {
        "league_id": int(league.get("id") or NFL_LEAGUE_ID),
        "season": int(league.get("season") or nfl_season_for_date(synced_at.astimezone(TRACKER_TIMEZONE).date())),
        "team_id": int(team["id"]),
        "team_name": str(team.get("name") or "Unknown team"),
        "team_logo": team.get("logo"),
        "conference": standing.get("conference"),
        "division": standing.get("division"),
        "position": int(standing.get("position") or 0),
        "wins": int(standing.get("won") or 0),
        "losses": int(standing.get("lost") or 0),
        "ties": int(standing.get("ties") or 0),
        "points_for": int(points.get("for") or 0),
        "points_against": int(points.get("against") or 0),
        "point_difference": int(points.get("difference") or 0),
        "streak": str(standing.get("streak") or ""),
        "records": standing.get("records") or {},
        "synced_at": synced_at.isoformat(),
    }


def normalize_team(team: dict, synced_at: datetime) -> dict:
    return {
        "team_id": int(team["id"]),
        "name": str(team.get("name") or "Unknown team"),
        "code": team.get("code"),
        "city": team.get("city"),
        "coach": team.get("coach"),
        "stadium": team.get("stadium") or {},
        "established": team.get("established"),
        "logo": team.get("logo"),
        "country": team.get("country") or {},
        "synced_at": synced_at.isoformat(),
    }


class ApiSportsService:
    def __init__(self, api_key: str | None = None, base_url: str | None = None, client=None, get=None, clock=None) -> None:
        self.api_key = api_key if api_key is not None else API_SPORTS_KEY
        self.base_url = (base_url or API_SPORTS_BASE_URL).rstrip("/")
        self.client = client
        self.get = get or requests.get
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.request_count = 0

    def _client(self):
        return self.client or supabase_service._ensure_client()

    def _request(self, endpoint: str, params: dict | None = None) -> list[dict]:
        if not self.api_key:
            raise RuntimeError("API_SPORTS_KEY is not configured.")
        reserve_request(self.base_url)
        response = self.get(
            f"{self.base_url}/{endpoint.lstrip('/')}",
            params=params or {},
            headers={"x-apisports-key": self.api_key},
            timeout=30,
        )
        self.request_count += 1
        response.raise_for_status()
        payload = response.json()
        errors = payload.get("errors") or {}
        if errors:
            raise RuntimeError(f"API-Sports {endpoint} failed: {errors}")
        rows = payload.get("response")
        if not isinstance(rows, list):
            raise RuntimeError(f"API-Sports {endpoint} returned an unexpected response.")
        return rows

    def _upsert(self, table: str, rows: list[dict], conflict: str) -> None:
        if rows:
            supabase_service._execute(lambda: self._client().table(table).upsert(rows, on_conflict=conflict).execute())

    def _record_sync(self, sync_key: str, started_at: datetime, success: bool, error: str | None = None) -> None:
        supabase_service._execute(lambda: self._client().table("api_sports_sync_state").upsert({
            "sync_key": sync_key,
            "last_attempt_at": started_at.isoformat(),
            "last_success_at": self.clock().isoformat() if success else None,
            "request_count": self.request_count,
            "success": success,
            "error_message": error[:500] if error else None,
        }, on_conflict="sync_key").execute())

    def _already_synced_today(self) -> bool:
        rows = (
            supabase_service._execute(lambda: self._client().table("api_sports_sync_state")
            .select("last_success_at,success")
            .eq("sync_key", "daily")
            .limit(1)
            .execute())
            .data
            or []
        )
        if not rows or not rows[0].get("success") or not rows[0].get("last_success_at"):
            return False
        last_success = parse_iso_datetime(rows[0]["last_success_at"])
        return last_success.astimezone(TRACKER_TIMEZONE).date() == self.clock().astimezone(TRACKER_TIMEZONE).date()

    def sync_daily(self, force: bool = False) -> dict:
        if not force and self._already_synced_today():
            return {"skipped": True, "request_count": 0}
        started_at = self.clock()
        self.request_count = 0
        season = nfl_season_for_date(started_at.astimezone(TRACKER_TIMEZONE).date())
        try:
            games = self._request("games", {"league": NFL_LEAGUE_ID, "season": season})
            standings = self._request("standings", {"league": NFL_LEAGUE_ID, "season": season})
            teams = self._request("teams", {"league": NFL_LEAGUE_ID, "season": season})
            synced_at = self.clock()
            self._upsert("api_sports_nfl_games", [normalize_game(row, synced_at) for row in games], "game_id")
            self._upsert("api_sports_nfl_standings", [normalize_standing(row, synced_at) for row in standings], "league_id,season,team_id")
            self._upsert("api_sports_nfl_teams", [normalize_team(row, synced_at) for row in teams], "team_id")
            self._record_sync("daily", started_at, True)
            return {"games": len(games), "standings": len(standings), "teams": len(teams), "request_count": self.request_count}
        except Exception as exc:
            self._record_sync("daily", started_at, False, str(exc))
            raise

    def should_sync_live_scores(self) -> bool:
        now = self.clock()
        rows = (
            supabase_service._execute(lambda: self._client().table("api_sports_nfl_games")
            .select("kickoff_at,status_short")
            .eq("season", nfl_season_for_date(now.astimezone(TRACKER_TIMEZONE).date()))
            .gte("kickoff_at", (now - timedelta(hours=5)).isoformat())
            .lte("kickoff_at", (now + timedelta(minutes=20)).isoformat())
            .execute())
            .data
            or []
        )
        return any(str(row.get("status_short") or "").upper() not in FINAL_STATUSES for row in rows)

    def sync_live_scores(self) -> dict:
        started_at = self.clock()
        self.request_count = 0
        game_date = started_at.astimezone(timezone.utc).date().isoformat()
        try:
            games = self._request("games", {"league": NFL_LEAGUE_ID, "season": nfl_season_for_date(started_at.astimezone(TRACKER_TIMEZONE).date()), "date": game_date})
            synced_at = self.clock()
            self._upsert("api_sports_nfl_games", [normalize_game(row, synced_at) for row in games], "game_id")
            self._record_sync("live_scores", started_at, True)
            return {"games": len(games), "request_count": self.request_count}
        except Exception as exc:
            self._record_sync("live_scores", started_at, False, str(exc))
            raise


api_sports_service = ApiSportsService()