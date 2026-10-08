from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import requests

from src.config import API_SPORTS_KEY
from src.datetime_utils import parse_iso_datetime
from src.services.api_budget_service import reserve_request
from src.services.supabase_service import supabase_service

TRACKER_TIMEZONE = ZoneInfo("America/New_York")
SEASON_FINAL_STATUSES = {"FT", "AOT", "CANC", "ABD", "WO", "COMPLETED", "FINISHED"}
DATE_PRODUCTS = {
    "football": ("https://v3.football.api-sports.io", "fixtures"),
    "basketball": ("https://v1.basketball.api-sports.io", "games"),
    "baseball": ("https://v1.baseball.api-sports.io", "games"),
    "hockey": ("https://v1.hockey.api-sports.io", "games"),
    "rugby": ("https://v1.rugby.api-sports.io", "games"),
    "handball": ("https://v1.handball.api-sports.io", "games"),
    "volleyball": ("https://v1.volleyball.api-sports.io", "games"),
    "mma": ("https://v1.mma.api-sports.io", "fights"),
    "ncaa": ("https://v1.american-football.api-sports.io", "games"),
}
DATE_PRODUCT_PARAMS = {
    "ncaa": {"league": 2},
}
SEASON_PRODUCTS = {
    "formula-1": ("https://v1.formula-1.api-sports.io", "races"),
}


def _status(value: Any) -> tuple[str, str]:
    if isinstance(value, dict):
        return str(value.get("short") or value.get("status") or "UNK"), str(value.get("long") or value.get("short") or "Unknown")
    text = str(value or "Unknown")
    return text.upper(), text


def _timestamp(value: Any, fallback: datetime) -> str:
    if isinstance(value, dict):
        if value.get("timestamp"):
            return datetime.fromtimestamp(int(value["timestamp"]), timezone.utc).isoformat()
        value = value.get("date")
    if value:
        parsed = parse_iso_datetime(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    return fallback.isoformat()


def normalize_event(sport_slug: str, row: dict, synced_at: datetime) -> dict:
    if sport_slug == "football":
        event = row.get("fixture") or {}
        league = row.get("league") or {}
        teams = row.get("teams") or {}
        home = teams.get("home") or {}
        away = teams.get("away") or {}
        venue = event.get("venue") or {}
        scores = row.get("goals") or {}
        status_value = event.get("status")
        name = f"{home.get('name') or 'Home'} vs {away.get('name') or 'Away'}"
        round_name = league.get("round")
        start_value = event.get("date")
        event_id = event.get("id")
    elif sport_slug == "formula-1":
        event = row
        league = row.get("competition") or {}
        circuit = row.get("circuit") or {}
        location = league.get("location") or {}
        venue = {"name": circuit.get("name"), "city": location.get("city"), "country": location.get("country")}
        home = away = {}
        scores = {}
        status_value = row.get("status")
        name = str(league.get("name") or "Formula 1 event")
        round_name = row.get("type")
        start_value = row.get("date")
        event_id = row.get("id")
    elif sport_slug == "ncaa":
        event = row.get("game") or {}
        league = row.get("league") or {}
        teams = row.get("teams") or {}
        home = teams.get("home") or {}
        away = teams.get("away") or {}
        venue = event.get("venue") or {}
        scores = row.get("scores") or {}
        status_value = event.get("status")
        name = f"{home.get('name') or 'Home'} vs {away.get('name') or 'Away'}"
        round_name = event.get("week") or event.get("stage")
        start_value = event.get("date")
        event_id = event.get("id")
    else:
        event = row
        league = row.get("league") or {}
        teams = row.get("teams") or {}
        home = teams.get("home") or {}
        away = teams.get("away") or {}
        venue = row.get("venue") or {}
        scores = row.get("scores") or {}
        status_value = row.get("status")
        name = f"{home.get('name') or 'Home'} vs {away.get('name') or 'Away'}"
        round_name = row.get("week") or row.get("stage")
        start_value = row.get("date")
        event_id = row.get("id")

    if event_id is None:
        raise ValueError(f"{sport_slug} event is missing its API-Sports ID.")
    status_code, status_text = _status(status_value)
    season = league.get("season") or row.get("season")
    return {
        "sport_slug": sport_slug,
        "event_id": str(event_id),
        "league_id": str(league.get("id")) if league.get("id") is not None else None,
        "league_name": str(league.get("name") or name),
        "season": str(season) if season is not None else None,
        "round_name": str(round_name) if round_name is not None else None,
        "event_name": name,
        "start_at": _timestamp(start_value, synced_at),
        "venue": venue,
        "home_id": str(home.get("id")) if home.get("id") is not None else None,
        "home_name": home.get("name"),
        "home_logo": home.get("logo"),
        "away_id": str(away.get("id")) if away.get("id") is not None else None,
        "away_name": away.get("name"),
        "away_logo": away.get("logo"),
        "home_score": scores.get("home"),
        "away_score": scores.get("away"),
        "status_code": status_code,
        "status": status_text,
        "raw_event": row,
        "synced_at": synced_at.isoformat(),
    }


class ApiSportsMultiService:
    def __init__(self, api_key: str | None = None, client=None, get=None, clock=None) -> None:
        self.api_key = api_key if api_key is not None else API_SPORTS_KEY
        self.client = client
        self.get = get or requests.get
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.request_count = 0

    def _client(self):
        return self.client or supabase_service._ensure_client()

    def _request(self, base_url: str, endpoint: str, params: dict, *, require_complete: bool = False) -> list[dict]:
        if not self.api_key:
            raise RuntimeError("API_SPORTS_KEY is not configured.")
        reserve_request(base_url)
        response = self.get(f"{base_url}/{endpoint}", params=params, headers={"x-apisports-key": self.api_key}, timeout=30)
        self.request_count += 1
        response.raise_for_status()
        payload = response.json()
        if payload.get("errors"):
            raise RuntimeError(f"API-Sports {endpoint} failed: {payload['errors']}")
        if require_complete:
            paging = payload.get("paging")
            if paging is not None:
                if not isinstance(paging, dict) or type(paging.get("total")) is not int or paging["total"] not in {0, 1}:
                    raise ValueError("This player search requires additional provider pages. Use a full name or a cached player suggestion; no partial season was saved.")
        rows = payload.get("response")
        if not isinstance(rows, list):
            raise RuntimeError(f"API-Sports {endpoint} returned an unexpected response.")
        return rows

    def _daily_complete(self, sport_slug: str, today: date) -> bool:
        rows = supabase_service._execute(
            lambda: self._client().table("api_sports_sync_state").select("success,last_success_at")
            .eq("sync_key", f"events:{sport_slug}").limit(1).execute()
        ).data or []
        if not rows or not rows[0].get("success") or not rows[0].get("last_success_at"):
            return False
        last = parse_iso_datetime(rows[0]["last_success_at"])
        return last.astimezone(TRACKER_TIMEZONE).date() == today

    def _record(self, sync_key: str, started: datetime, success: bool, requests_used: int, error: str | None = None) -> None:
        supabase_service._execute(lambda: self._client().table("api_sports_sync_state").upsert({
            "sync_key": sync_key,
            "last_attempt_at": started.isoformat(),
            "last_success_at": self.clock().isoformat() if success else None,
            "request_count": requests_used,
            "success": success,
            "error_message": error[:500] if error else None,
        }, on_conflict="sync_key").execute())

    def _store_events(self, rows: list[dict]) -> None:
        if rows:
            supabase_service._execute(
                lambda: self._client().table("api_sports_events")
                .upsert(rows, on_conflict="sport_slug,event_id").execute()
            )

    def sync_daily(self, force: bool = False) -> dict:
        self.request_count = 0
        today = self.clock().astimezone(TRACKER_TIMEZONE).date()
        summary: dict[str, dict] = {}
        for sport_slug, (base_url, endpoint) in DATE_PRODUCTS.items():
            if not force and self._daily_complete(sport_slug, today):
                summary[sport_slug] = {"skipped": True, "requests": 0, "events": 0}
                continue
            started = self.clock()
            request_start = self.request_count
            try:
                events: dict[str, dict] = {}
                for day_offset in range(7):
                    target_date = today + timedelta(days=day_offset)
                    params = {"date": target_date.isoformat(), **DATE_PRODUCT_PARAMS.get(sport_slug, {})}
                    rows = self._request(base_url, endpoint, params)
                    fetched_at = self.clock()
                    for row in rows:
                        normalized = normalize_event(sport_slug, row, fetched_at)
                        events[normalized["event_id"]] = normalized
                self._store_events(list(events.values()))
                count = self.request_count - request_start
                self._record(f"events:{sport_slug}", started, True, count)
                summary[sport_slug] = {"requests": count, "events": len(events)}
            except Exception as exc:
                count = self.request_count - request_start
                self._record(f"events:{sport_slug}", started, False, count, str(exc))
                summary[sport_slug] = {"requests": count, "error": str(exc)}

        f1_slug = "formula-1"
        if force or not self._daily_complete(f1_slug, today):
            started = self.clock()
            request_start = self.request_count
            try:
                season = today.year
                rows = self._request(*SEASON_PRODUCTS[f1_slug], params={"season": season})
                fetched_at = self.clock()
                events = [normalize_event(f1_slug, row, fetched_at) for row in rows]
                self._store_events(events)
                count = self.request_count - request_start
                self._record(f"events:{f1_slug}", started, True, count)
                summary[f1_slug] = {"requests": count, "events": len(events)}
            except Exception as exc:
                count = self.request_count - request_start
                self._record(f"events:{f1_slug}", started, False, count, str(exc))
                summary[f1_slug] = {"requests": count, "error": str(exc)}
        else:
            summary[f1_slug] = {"skipped": True, "requests": 0, "events": 0}
        return {"total_requests": self.request_count, "sports": summary}

    def active_sports(self) -> list[str]:
        now = self.clock()
        rows = supabase_service._execute(
            lambda: self._client().table("api_sports_events").select("sport_slug,start_at,status_code")
            .gte("start_at", (now - timedelta(hours=5)).isoformat())
            .lte("start_at", (now + timedelta(minutes=20)).isoformat()).execute()
        ).data or []
        active = set()
        for row in rows:
            if row.get("sport_slug") in DATE_PRODUCTS and str(row.get("status_code") or "").upper() not in SEASON_FINAL_STATUSES:
                active.add(row["sport_slug"])
        return sorted(active)

    def sync_live_scores(self, sport_slugs: list[str] | None = None) -> dict:
        self.request_count = 0
        targets = sport_slugs if sport_slugs is not None else self.active_sports()
        summary = {}
        now = self.clock()
        current_date = now.astimezone(timezone.utc).date().isoformat()
        for sport_slug in targets:
            config = DATE_PRODUCTS.get(sport_slug)
            if not config:
                continue
            started = self.clock()
            request_start = self.request_count
            try:
                params = {"date": current_date, **DATE_PRODUCT_PARAMS.get(sport_slug, {})}
                rows = self._request(*config, params=params)
                fetched_at = self.clock()
                events = [normalize_event(sport_slug, row, fetched_at) for row in rows]
                self._store_events(events)
                count = self.request_count - request_start
                self._record(f"events_live:{sport_slug}", started, True, count)
                summary[sport_slug] = {"requests": count, "events": len(events)}
            except Exception as exc:
                count = self.request_count - request_start
                self._record(f"events_live:{sport_slug}", started, False, count, str(exc))
                summary[sport_slug] = {"requests": count, "error": str(exc)}
        return {"total_requests": self.request_count, "sports": summary}


api_sports_multi_service = ApiSportsMultiService()