from datetime import datetime, timezone
import re
from threading import Lock
from time import monotonic

import requests

from src.config import API_SPORTS_KEY, API_SPORTS_BUDGET_ENABLED
from src.services.api_budget_service import ApiBudgetDenied, reserve_request
from src.services.api_sports_multi_service import DATE_PRODUCTS, normalize_event
from src.services.api_sports_service import normalize_game
from src.services.player_stats_service import PLAYER_ENDPOINTS, athlete_records
from src.services.supabase_service import supabase_service

TEAM_SPORTS = {
    "nfl": "NFL", "ncaa": "NCAA football", "football": "Soccer",
    "basketball": "Basketball", "baseball": "Baseball", "hockey": "Hockey",
    "rugby": "Rugby", "handball": "Handball", "volleyball": "Volleyball",
}
PLAYER_SUPPORTED = {"nfl", "ncaa", "football", "basketball"}
GAME_TEAM_ENDPOINTS = {
    "nfl": ("games/statistics/teams", "id"),
    "ncaa": ("games/statistics/teams", "id"),
    "football": ("fixtures/statistics", "fixture"),
    "basketball": ("games/statistics/teams", "id"),
}
refresh_lock = Lock()


class TeamRefreshFailed(RuntimeError):
    def __init__(self, report: dict):
        self.report = report
        super().__init__(report["error"])


class TeamApiService:
    def __init__(self, database=None, get=None, clock=None, api_key=None, budget_enabled=None):
        self.db = database or supabase_service
        self.get = get or requests.get
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.api_key = API_SPORTS_KEY if api_key is None else api_key
        self.budget_enabled = API_SPORTS_BUDGET_ENABLED if budget_enabled is None else budget_enabled

    def table(self, name):
        return self.db._ensure_client().table(name)

    def rows(self, query):
        data = query.execute().data
        if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
            raise RuntimeError("API cache returned invalid rows.")
        return data

    def picker_directory(self, sport, season, league=None):
        if sport not in TEAM_SPORTS:
            raise ValueError("Select a supported team sport.")
        if not re.fullmatch(r"20\d{2}(?:-20\d{2})?", season):
            raise ValueError("Enter a provider season such as 2026 or 2026-2027.")
        if "-" in season and int(season[5:]) != int(season[:4]) + 1:
            raise ValueError("A split season must use consecutive years.")
        if sport in {"nfl", "ncaa"} and "-" in season:
            raise ValueError("NFL/NCAA use a single-year season.")
        if not self.api_key:
            raise ValueError("The running bot did not load API_SPORTS_KEY. Check the bot's environment and restart it after configuration changes. Do not share the key.")
        if not self.budget_enabled:
            raise ValueError("The running bot sees API_SPORTS_BUDGET_ENABLED as false. It is separate from API_SPORTS_KEY. Check the bot's environment and restart it after configuration changes.")
        base = "https://v1.american-football.api-sports.io" if sport == "nfl" else DATE_PRODUCTS[sport][0]
        report = {"requests": 0, "deadline": monotonic() + 120}
        if league is None and sport in {"nfl", "ncaa"}:
            return [(TEAM_SPORTS[sport], "1" if sport == "nfl" else "2")]
        if league is not None:
            if not re.fullmatch(r"[1-9]\d{0,9}", league):
                raise ValueError("Select a league from the dropdown.")
            payload = self.request(base, "teams", {"league": int(league), "season": season}, report)
        else:
            payload = self.request(base, "leagues", {}, report)
        identities = {}
        for row in payload:
            identity = row.get("team", row) if league is not None else row.get("league", row)
            if not isinstance(identity, dict) or type(identity.get("id")) is not int or identity["id"] <= 0 or not isinstance(identity.get("name"), str) or not identity["name"].strip():
                raise RuntimeError("Provider returned an invalid picker identity.")
            key = str(identity["id"])
            if key in identities and identities[key] != identity["name"]:
                raise RuntimeError("Provider returned conflicting picker identities.")
            identities[key] = identity["name"]
        if league is not None and identities:
            self.table("api_sports_team_directory").upsert([
                {"sport_slug": sport, "league_id": league, "team_id": int(key),
                 "name": name, "synced_at": self.clock().isoformat()}
                for key, name in identities.items()
            ], on_conflict="sport_slug,league_id,team_id").execute()
        return sorted([(name, key) for key, name in identities.items()], key=lambda item: (item[0].casefold(), item[1]))

    def suggestions(self, sport, league="", current="", teams=False):
        if sport not in TEAM_SPORTS:
            return []
        if teams:
            query = self.table("api_sports_team_directory").select("team_id,name").eq(
                "sport_slug", sport,
            ).eq("league_id", league)
            if current.strip():
                value = current.strip()[:100].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                query = query.ilike("name", f"%{value}%")
            rows = self.rows(query.order("name").limit(25))
            return [(row["name"], str(row["team_id"])) for row in rows if current.casefold() in row["name"].casefold()][:25]
        table = "api_sports_nfl_games" if sport == "nfl" else "api_sports_events"
        query = self.table(table).select("league_id" if sport == "nfl" else "league_id,league_name")
        if sport != "nfl":
            query = query.eq("sport_slug", sport)
        rows = self.rows(query.limit(1000))
        names = {str(row["league_id"]): "NFL" if sport == "nfl" else row["league_name"]
                 for row in rows if row.get("league_id") is not None}
        return [(name, key) for key, name in names.items()
                if name and current.casefold() in name.casefold()][:25]

    def request(self, base, endpoint, params, report, allow_object=False):
        pages = []
        page = 1
        total = None
        while True:
            if monotonic() > report["deadline"]:
                raise RuntimeError("Refresh reached the 12-minute interaction limit. Saved components remain cached; the season is incomplete.")
            reserve_request(base)
            report["requests"] += 1
            response = self.get(f"{base}/{endpoint}", params={**params, **({"page": page} if page > 1 else {})},
                                headers={"x-apisports-key": self.api_key}, timeout=30)
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict) or body.get("errors"):
                raise RuntimeError(f"Provider rejected {endpoint}; check subscription, parameters and quota.")
            payload = body.get("response")
            if allow_object and isinstance(payload, dict):
                paging = body.get("paging")
                if paging is not None and paging != {"current": 1, "total": 1}:
                    raise RuntimeError("Unexpected pagination on team summary.")
                return payload
            if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
                raise RuntimeError(f"Provider returned invalid {endpoint} records.")
            paging = body.get("paging")
            if paging is not None:
                if not isinstance(paging, dict) or type(paging.get("total")) is not int or type(paging.get("current")) is not int:
                    raise RuntimeError("Provider returned invalid pagination.")
                if paging == {"current": 1, "total": 0} and not payload and page == 1:
                    return []
                if paging["current"] != page or not 1 <= paging["total"] <= 1000:
                    raise RuntimeError("Provider returned inconsistent pagination.")
                if total is not None and total != paging["total"]:
                    raise RuntimeError("Provider pagination changed during refresh.")
                total = paging["total"]
            elif page > 1:
                raise RuntimeError("Provider omitted pagination mid-refresh.")
            pages.extend(payload)
            if paging is None or page == total:
                return pages
            page += 1

    def snapshot(self, scope, kind, payload, report):
        self.table("api_sports_team_stats_cache").upsert({
            **scope, "stat_kind": kind, "payload": payload, "synced_at": self.clock().isoformat(),
        }, on_conflict="sport_slug,league_id,team_id,season,stat_kind").execute()
        report["snapshots"] += 1

    def validate_stat_scope(self, payload, team_ids, league=None, season=None):
        rows = payload if isinstance(payload, list) else [payload]
        for row in rows:
            if "team" in row:
                identity = row["team"]
                if not isinstance(identity, dict) or str(identity.get("id")) not in team_ids:
                    raise RuntimeError("Provider returned statistics for a different team.")
            if league is not None and "league" in row:
                identity = row["league"]
                if not isinstance(identity, dict) or str(identity.get("id")) != league:
                    raise RuntimeError("Provider returned statistics for a different league.")
                if "season" in identity and str(identity["season"]) != season:
                    raise RuntimeError("Provider returned statistics for a different season.")

    def refresh(self, sport, league, team, season, owner_id):
        if sport not in TEAM_SPORTS:
            raise ValueError("No verified league/team refresh for this sport. F1 uses constructors/drivers; MMA uses fighters. Cricket/cycling feeds are unavailable.")
        if not re.fullmatch(r"[1-9]\d{0,9}", league) or not re.fullmatch(r"[1-9]\d{0,9}", team):
            raise ValueError("Select a provider league/team ID, or enter a positive numeric ID.")
        if not re.fullmatch(r"20\d{2}(?:-20\d{2})?", season):
            raise ValueError("Enter the provider season, for example 2026 or 2026-2027.")
        if "-" in season and int(season[5:]) != int(season[:4]) + 1:
            raise ValueError("A split season must use consecutive years.")
        if sport in {"nfl", "ncaa"} and (league != ("1" if sport == "nfl" else "2") or "-" in season):
            raise ValueError("NFL uses league 1; NCAA uses league 2, with a single-year season.")
        if not self.budget_enabled:
            raise ValueError("Enable the shared API request budget before running a full-team refresh.")
        if not self.api_key:
            raise ValueError("API_SPORTS_KEY is not configured.")
        if not refresh_lock.acquire(blocking=False):
            raise ValueError("Another Owner API refresh is running. Retry after it finishes.")
        report = {"requests": 0, "snapshots": 0, "games": 0, "player_games": 0, "empty_player_games": 0,
                  "owner_id": str(owner_id), "deadline": monotonic() + 720,
                  "empty_team_summary": False,
                  "season_summary_supported": sport not in {"nfl", "ncaa"},
                  "complete": False, "error": None, "player_supported": sport in PLAYER_SUPPORTED}
        scope = {"sport_slug": sport, "league_id": league, "team_id": int(team), "season": season}
        base = "https://v1.american-football.api-sports.io" if sport == "nfl" else DATE_PRODUCTS[sport][0]
        params = {"league": int(league), "season": season, "team": int(team)}
        try:
            # Validate membership before caching any statistics under the requested scope.
            teams = self.request(base, "teams", {"league": int(league), "season": season}, report)
            if any(not isinstance(row.get("team", row), dict) for row in teams):
                raise RuntimeError("Provider returned an invalid team identity.")
            selected = [row for row in teams if (row.get("team", row)).get("id") == int(team)]
            if len(selected) != 1:
                raise ValueError("That team is not in the provider's selected league/season.")
            for row in teams:
                identity = row.get("team", row)
                if type(identity.get("id")) is not int or not isinstance(identity.get("name"), str):
                    raise RuntimeError("Provider returned an invalid team identity.")
            directory = [{"sport_slug": sport, "league_id": league, "team_id": row.get("team", row)["id"],
                          "name": row.get("team", row)["name"], "synced_at": self.clock().isoformat()} for row in teams]
            if directory:
                self.table("api_sports_team_directory").upsert(directory, on_conflict="sport_slug,league_id,team_id").execute()
            if report["season_summary_supported"]:
                summary = self.request(base, "teams/statistics", params, report, allow_object=True)
                self.validate_stat_scope(summary, {team}, league, season)
                report["empty_team_summary"] = not summary
                self.snapshot(scope, "season_team", summary, report)
            endpoint = "fixtures" if sport == "football" else "games"
            raw_games = self.request(base, endpoint, params, report)
            for row in raw_games:
                scope_row = row.get("league")
                if not isinstance(scope_row, dict) or str(scope_row.get("id")) != league or str(scope_row.get("season")) != season:
                    raise RuntimeError("Provider returned a game without the selected league/season.")
                source = row.get("fixture") if sport == "football" else row.get("game") if sport in {"nfl", "ncaa"} else row
                if not isinstance(source, dict) or not source.get("date"):
                    raise RuntimeError("Provider returned a game without a kickoff date; no date was invented.")
                date_value = source["date"]
                if isinstance(date_value, dict) and not (date_value.get("timestamp") or date_value.get("date")):
                    raise RuntimeError("Provider returned a game without a kickoff date; no date was invented.")
            now = self.clock()
            games = [normalize_game(row, now) if sport == "nfl" else normalize_event(sport, row, now) for row in raw_games]
            seen = set()
            for game in games:
                home = game["home_team_id"] if sport == "nfl" else game["home_id"]
                away = game["away_team_id"] if sport == "nfl" else game["away_id"]
                if str(game["league_id"]) != league or str(game["season"]) != season or team not in {str(home), str(away)}:
                    raise RuntimeError("Provider returned a game outside the selected team/league/season.")
                game_id = int(game["game_id"] if sport == "nfl" else game["event_id"])
                if game_id in seen:
                    raise RuntimeError("Provider returned duplicate games.")
                seen.add(game_id)
            if games:
                self.table("api_sports_nfl_games" if sport == "nfl" else "api_sports_events").upsert(
                    games, on_conflict="game_id" if sport == "nfl" else "sport_slug,event_id",
                ).execute()
            report["games"] = len(games)
            self.snapshot(scope, "season_schedule", raw_games, report)
            for game in games:
                start = game["kickoff_at"] if sport == "nfl" else game["start_at"]
                status = game["status_short"] if sport == "nfl" else game["status_code"]
                if status.upper() in {"NS", "TBD", "PST", "CANC", "ABD", "WO"} or datetime.fromisoformat(start) > now:
                    continue
                game_id = int(game["game_id"] if sport == "nfl" else game["event_id"])
                team_ids = {str(game["home_team_id"]), str(game["away_team_id"])} if sport == "nfl" else {game["home_id"], game["away_id"]}
                if sport in GAME_TEAM_ENDPOINTS:
                    endpoint, parameter = GAME_TEAM_ENDPOINTS[sport]
                    payload = self.request(base, endpoint, {parameter: game_id}, report)
                    self.validate_stat_scope(payload, team_ids)
                    self.snapshot(scope, f"game_team:{game_id}", payload, report)
                if sport in PLAYER_SUPPORTED:
                    endpoint, parameter = ("games/statistics/players", "id") if sport in {"nfl", "ncaa"} else PLAYER_ENDPOINTS[sport][1:]
                    payload = self.request(base, endpoint, {parameter: game_id}, report)
                    self.validate_stat_scope(payload, team_ids)
                    records = athlete_records(sport, payload, game_id, game)
                    self.table("api_sports_player_game_stats").upsert({
                        "sport_slug": sport, "game_id": game_id, "payload": payload,
                        "synced_at": self.clock().isoformat(),
                    }, on_conflict="sport_slug,game_id").execute()
                    report["player_games"] += 1
                    if not records:
                        report["empty_player_games"] += 1
            report["complete"] = True
            return report
        except Exception as exc:
            report["error"] = str(exc)
            report["quota_denied"] = isinstance(exc, ApiBudgetDenied)
            raise TeamRefreshFailed(report) from exc
        finally:
            refresh_lock.release()


team_api_service = TeamApiService()
