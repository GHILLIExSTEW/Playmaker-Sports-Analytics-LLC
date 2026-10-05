from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests

from src.config import API_SPORTS_KEY
from src.services.supabase_service import supabase_service


class ApiSportsPlayerService:
    BASE_URLS = {
        "baseball": "https://v1.baseball.api-sports.io",
        "american-football": "https://v1.american-football.api-sports.io",
        "nfl": "https://v1.american-football.api-sports.io",
        "ncaa-football": "https://v1.american-football.api-sports.io",
        "basketball": "https://v1.basketball.api-sports.io",
        "football": "https://v3.football.api-sports.io",
        "soccer": "https://v3.football.api-sports.io",
        "formula-1": "https://v1.formula-1.api-sports.io",
        "f1": "https://v1.formula-1.api-sports.io",
    }

    @classmethod
    def normalize_sport_slug(cls, sport_slug: str) -> str:
        slug = sport_slug.strip().lower()
        if slug not in cls.BASE_URLS:
            raise ValueError(f"Unsupported API-Sports sport: {sport_slug}")
        return slug

    @staticmethod
    def parse_league_value(value: str) -> tuple[str, str]:
        try:
            league_id, season = value.split("|", 1)
        except ValueError as exc:
            raise ValueError("Choose a league from the suggestions.") from exc
        if not league_id or not season:
            raise ValueError("Choose a league from the suggestions.")
        return league_id, season

    @classmethod
    def _request(cls, sport_slug: str, endpoint: str, params: dict[str, Any]) -> dict:
        if not API_SPORTS_KEY:
            raise RuntimeError("API_SPORTS_KEY is not configured.")
        slug = cls.normalize_sport_slug(sport_slug)
        response = requests.get(
            f"{cls.BASE_URLS[slug]}/{endpoint.lstrip('/')}",
            headers={"x-apisports-key": API_SPORTS_KEY},
            params=params,
            timeout=25,
        )
        response.raise_for_status()
        payload = response.json()
        errors = payload.get("errors")
        if errors:
            raise RuntimeError(f"API-Sports request failed: {errors}")
        return payload

    @staticmethod
    def _provider_player(record: dict) -> dict | None:
        player = record.get("player") or record.get("driver") or record
        player_id = player.get("id")
        name = player.get("name") or " ".join(
            part for part in (player.get("firstname"), player.get("lastname")) if part
        )
        if player_id is None or not name:
            return None
        statistics = record.get("statistics") or []
        first_statistics = statistics[0] if statistics and isinstance(statistics[0], dict) else {}
        team = first_statistics.get("team") or record.get("team") or {}
        return {
            "player_id": int(player_id),
            "name": str(name).strip(),
            "team_name": team.get("name") if isinstance(team, dict) else None,
        }

    def list_sports(self, current: str = "") -> list[dict]:
        rows = supabase_service._ensure_client().table("api_sports_player_leagues").select(
            "sport_slug"
        ).limit(1000).execute().data or []
        slugs = sorted({row["sport_slug"] for row in rows if row.get("sport_slug")})
        query = current.casefold()
        return [
            {"slug": slug, "name": self._sport_name(slug)}
            for slug in slugs
            if slug in self.BASE_URLS and query in self._sport_name(slug).casefold()
        ][:25]

    @staticmethod
    def _sport_name(slug: str) -> str:
        return {
            "american-football": "American Football",
            "ncaa-football": "NCAA Football",
            "football": "Soccer",
            "f1": "Formula 1",
            "formula-1": "Formula 1",
        }.get(slug, slug.replace("-", " ").title())

    def list_leagues(self, sport_slug: str, current: str = "") -> list[dict]:
        rows = supabase_service._ensure_client().table("api_sports_player_leagues").select(
            "league_id,name,current_season"
        ).eq("sport_slug", sport_slug).limit(1000).execute().data or []
        query = current.casefold()
        leagues = {
            (str(row["league_id"]), str(row["current_season"])): row
            for row in rows
            if row.get("league_id") is not None and row.get("current_season")
            and query in str(row.get("name") or "").casefold()
        }
        return sorted(leagues.values(), key=lambda row: str(row.get("name") or "").casefold())[:25]

    def search_cached_players(self, sport_slug: str, league_id: str, current: str) -> list[dict]:
        if not current.strip():
            return []
        response = supabase_service._ensure_client().table("api_sports_player_directory").select(
            "player_id,name,team_name"
        ).eq("sport_slug", sport_slug).eq("league_id", str(league_id)).ilike(
            "name", f"%{current.strip()}%"
        ).limit(25).execute()
        return response.data or []

    def sync_player_directory(self, sport_slug: str, league_value: str) -> int:
        league_id, season = self.parse_league_value(league_value)
        slug = self.normalize_sport_slug(sport_slug)
        is_f1 = slug in {"f1", "formula-1"}
        endpoint = "drivers" if is_f1 else "players"
        params: dict[str, Any] = {"season": season}
        if not is_f1:
            params["league"] = league_id

        total = 0
        page = 1
        while True:
            response = self._request(slug, endpoint, {**params, "page": page})
            records = response.get("response") or []
            rows = []
            for record in records:
                player = self._provider_player(record)
                if not player:
                    continue
                rows.append({
                    "sport_slug": slug,
                    "league_id": str(league_id),
                    **player,
                    "synced_at": datetime.now(timezone.utc).isoformat(),
                })
            if rows:
                supabase_service.upsert(
                    "api_sports_player_directory",
                    rows,
                    ["sport_slug", "league_id", "player_id"],
                )
                total += len(rows)
            pages = int((response.get("paging") or {}).get("total") or 1)
            if page >= pages:
                return total
            page += 1

    def search_players(self, sport_slug: str, league_value: str, current: str) -> list[dict]:
        if not current.strip():
            return []
        league_id, _season = self.parse_league_value(league_value)
        return self.search_cached_players(sport_slug, league_id, current)

    def search_cached_games(self, sport_slug: str, league_id: str, current: str) -> list[dict]:
        client = supabase_service._ensure_client()
        query = client.table("api_sports_events").select(
            "event_id,event_name,start_at"
        ).eq("sport_slug", sport_slug).eq("league_id", str(league_id))
        query = query.gte("start_at", datetime.now(timezone.utc).isoformat())
        if current.strip():
            query = query.ilike("event_name", f"%{current.strip()}%")
        return query.order("start_at", desc=False).limit(25).execute().data or []

    def get_game_label(self, sport_slug: str, league_id: str, game_id: str) -> str:
        rows = supabase_service._ensure_client().table("api_sports_events").select(
            "event_name,start_at"
        ).eq("sport_slug", sport_slug).eq("league_id", str(league_id)).eq(
            "event_id", str(game_id)
        ).limit(1).execute().data or []
        if not rows:
            return "Selected event"
        return f"{rows[0]['event_name']} · {str(rows[0]['start_at'])[:10]}"

    def _cached_player(self, sport_slug: str, league_id: str, player_id: str) -> dict | None:
        rows = supabase_service._ensure_client().table("api_sports_player_directory").select(
            "player_id,name,team_name"
        ).eq("sport_slug", sport_slug).eq("league_id", str(league_id)).eq(
            "player_id", int(player_id)
        ).limit(1).execute().data or []
        return rows[0] if rows else None

    @classmethod
    def _record_contains_player(cls, value: Any, player_id: int) -> bool:
        if isinstance(value, list):
            return any(cls._record_contains_player(item, player_id) for item in value)
        if not isinstance(value, dict):
            return False
        for key in ("player", "driver"):
            person = value.get(key)
            if isinstance(person, dict) and str(person.get("id")) == str(player_id):
                return True
        if str(value.get("player_id")) == str(player_id) or str(value.get("driver_id")) == str(player_id):
            return True
        return any(cls._record_contains_player(item, player_id) for item in value.values())

    def fetch_player_stats(
        self,
        sport_slug: str,
        league_value: str,
        player_id: str,
        game_id: str | None = None,
    ) -> tuple[dict, list[dict]]:
        league_id, season = self.parse_league_value(league_value)
        player = self._cached_player(sport_slug, league_id, player_id)
        if not player:
            raise ValueError("Player is not in the cached directory. Run /syncplayers for this league first.")

        client = supabase_service._ensure_client()
        if game_id:
            try:
                game_id_value = int(game_id)
            except ValueError as exc:
                raise ValueError("The selected event has an invalid provider ID.") from exc
            cached = client.table("api_sports_player_game_stats").select(
                "payload"
            ).eq("sport_slug", sport_slug).eq("game_id", game_id_value).limit(1).execute().data or []
            if sport_slug in {"f1", "formula-1"}:
                endpoint, params = "rankings/races", {"race": game_id_value}
            elif sport_slug in {"football", "soccer"}:
                endpoint, params = "fixtures/players", {"fixture": game_id_value}
            else:
                endpoint, params = "players/statistics", {"game": game_id_value}
        else:
            cached = client.table("api_sports_player_seasons").select(
                "groups"
            ).eq("sport_slug", sport_slug).eq("league_id", str(league_id)).eq(
                "player_id", int(player_id)
            ).eq("season", season).limit(1).execute().data or []
            endpoint = "rankings/drivers" if sport_slug in {"f1", "formula-1"} else "players/statistics"
            params = {"season": season} if endpoint == "rankings/drivers" else {
                "player": int(player_id), "league": league_id, "season": season,
            }

        if cached:
            records = cached[0].get("payload" if game_id else "groups") or []
            if not isinstance(records, list):
                records = [records]
        else:
            response = self._request(sport_slug, endpoint, params)
            records = response.get("response") or []
            if not isinstance(records, list):
                records = [records]
            if sport_slug in {"f1", "formula-1"} and not game_id:
                records = [
                    row for row in records
                    if self._record_contains_player(row, int(player_id))
                ]
        if game_id:
            if not cached:
                supabase_service.upsert("api_sports_player_game_stats", {
                    "sport_slug": sport_slug,
                    "game_id": game_id_value,
                    "payload": records,
                    "synced_at": datetime.now(timezone.utc).isoformat(),
                }, ["sport_slug", "game_id"])
            records = [
                record for record in records
                if self._record_contains_player(record, int(player_id))
            ]
        else:
            if not cached:
                supabase_service.upsert("api_sports_player_seasons", {
                    "sport_slug": sport_slug,
                    "league_id": str(league_id),
                    "player_id": int(player_id),
                    "season": season,
                    "groups": records,
                    "coverage": "season",
                    "synced_at": datetime.now(timezone.utc).isoformat(),
                }, ["sport_slug", "league_id", "player_id", "season"])
        return player, records


api_sports_player_service = ApiSportsPlayerService()