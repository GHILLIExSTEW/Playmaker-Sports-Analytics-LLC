from datetime import datetime, timedelta, timezone
import math
import re

import discord

from src.datetime_utils import parse_iso_datetime
from src.services.api_budget_service import member_request_batch
from src.services.api_sports_multi_service import ApiSportsMultiService
from src.services.api_sports_service import ApiSportsService
from src.services.player_stats_service import PLAYER_SPORTS, SUPPORTED_PLAYER_SPORTS, identity, stat_groups
from src.services.supabase_service import supabase_service

BASES = {
    "nfl": "https://v1.american-football.api-sports.io",
    "ncaa": "https://v1.american-football.api-sports.io",
    "basketball": "https://v1.basketball.api-sports.io",
    "football": "https://v3.football.api-sports.io",
    "formula-1": "https://v1.formula-1.api-sports.io",
}


def previous_season(season: str) -> str:
    if re.fullmatch(r"\d{4}", season):
        return str(int(season) - 1)
    match = re.fullmatch(r"(\d{4})-(\d{4})", season)
    if match and int(match[2]) == int(match[1]) + 1:
        return f"{int(match[1]) - 1}-{int(match[2]) - 1}"
    raise ValueError("This league's season format is not supported yet.")


def search_pattern(value: str) -> str:
    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def select_player(rows: list[dict], value: str) -> dict | None:
    if value.isdecimal():
        matches = [row for row in rows if row["player_id"] == int(value)]
    else:
        matches = [row for row in rows if row["name"].casefold() == value.casefold()]
        if not matches:
            matches = [row for row in rows if value.casefold() in row["name"].casefold()]
    unique = {row["player_id"]: row for row in matches}
    if len(unique) > 1:
        raise ValueError("Player name is ambiguous. Select a suggestion or enter a full name: " + ", ".join(
            f"{discord.utils.escape_markdown(row['name'][:60])} ({row['player_id']})"
            for row in list(unique.values())[:5]
        ))
    return next(iter(unique.values()), None)


def basketball_totals(payload: list, metadata: list, player_id: int) -> list[dict]:
    game_ids = set()
    for row in metadata:
        if not isinstance(row, dict) or type(row.get("id")) is not int:
            raise RuntimeError("Basketball season metadata returned invalid games.")
        game_ids.add(row["id"])
    stats = {}
    counts = {}
    seen = set()
    for row in payload:
        if not isinstance(row, dict) or identity(row.get("player"))["id"] != player_id:
            raise RuntimeError("Basketball season response returned the wrong player.")
        game = row.get("game")
        team = row.get("team")
        if not isinstance(game, dict) or type(game.get("id")) is not int or not isinstance(team, dict) or type(team.get("id")) is not int:
            raise RuntimeError("Basketball season response returned invalid game/team IDs.")
        if game["id"] not in game_ids:
            continue
        key = (game["id"], team["id"])
        if key in seen:
            raise RuntimeError("Basketball season response contains duplicate game logs.")
        seen.add(key)
        fields = {"points": row.get("points"), "assists": row.get("assists")}
        for field in ("field_goals", "threepoint_goals", "freethrows_goals", "rebounds"):
            values = row.get(field)
            if not isinstance(values, dict):
                raise RuntimeError("Basketball season response returned invalid stat fields.")
            for part in (("total",) if field == "rebounds" else ("total", "attempts")):
                fields[f"{field} {part}"] = values.get(part)
        for name, value in fields.items():
            if value is None:
                continue
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise RuntimeError("Basketball season response returned an invalid numeric stat.")
            stats[name] = stats.get(name, 0) + value
            counts[name] = counts.get(name, 0) + 1
    if not seen:
        return []
    values = {"available game logs": len(seen)}
    for name, total in stats.items():
        values[name] = total
        values[name + " reported games"] = counts[name]
    if counts.get("points"):
        values["points per reported game"] = round(stats["points"] / counts["points"], 2)
    return stat_groups({"Season totals from available league game logs": values})


class PlayerSeasonService:
    def __init__(self, database=None, clock=None, api=None, multi_api=None):
        self.db = database or supabase_service
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.api = api or ApiSportsService()
        self.multi_api = multi_api or ApiSportsMultiService()

    def rows(self, query) -> list[dict]:
        rows = query.execute().data
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise RuntimeError("Player season cache returned an invalid response.")
        return rows

    def table(self, name: str):
        return self.db._ensure_client().table(name)

    def leagues(self, sport: str, current: str = "") -> list[dict]:
        if sport not in SUPPORTED_PLAYER_SPORTS:
            return []
        query = self.table("api_sports_player_leagues").select("*").eq("sport_slug", sport)
        if current.strip():
            query = query.ilike("name", search_pattern(current.strip()[:100]))
        return self.rows(query.order("name").limit(25))

    def league(self, sport: str, league_id: str) -> dict:
        if sport not in SUPPORTED_PLAYER_SPORTS:
            raise ValueError(f"{PLAYER_SPORTS.get(sport, sport)} has no verified season-stat adapter.")
        rows = self.rows(self.table("api_sports_player_leagues").select("*").eq(
            "sport_slug", sport,
        ).eq("league_id", league_id).limit(1))
        if not rows:
            raise ValueError("Select a league suggested for this sport.")
        league = rows[0]
        # Cached event metadata, not the calendar year, determines split seasons.
        if sport in {"basketball", "football", "ncaa"}:
            events = self.rows(self.table("api_sports_events").select("season").eq(
                "sport_slug", sport,
            ).eq("league_id", league_id).lte("start_at", self.clock().isoformat()).order("start_at", desc=True).limit(1))
            if events and events[0].get("season"):
                league = {**league, "current_season": str(events[0]["season"])}
        elif sport == "nfl":
            events = self.rows(self.table("api_sports_nfl_games").select("season").lte(
                "kickoff_at", self.clock().isoformat(),
            ).order("kickoff_at", desc=True).limit(1))
            if events:
                league = {**league, "current_season": str(events[0]["season"])}
        elif sport == "formula-1":
            events = self.rows(self.table("api_sports_events").select("season").eq(
                "sport_slug", sport,
            ).lte("start_at", self.clock().isoformat()).order("start_at", desc=True).limit(1))
            if events:
                league = {**league, "current_season": str(events[0]["season"])}
        previous_season(league["current_season"])
        return league

    def players(self, sport: str, league_id: str, current: str = "", limit: int = 25) -> list[dict]:
        if sport not in SUPPORTED_PLAYER_SPORTS or not league_id:
            return []
        query = self.table("api_sports_player_directory").select("*").eq(
            "sport_slug", sport,
        ).eq("league_id", league_id)
        value = current.strip()[:100]
        if value.isdecimal():
            query = query.eq("player_id", int(value))
        elif value:
            query = query.ilike("name", search_pattern(value))
        return self.rows(query.order("name").limit(limit))

    def validate(self, sport: str, league_id: str, player: str) -> tuple[dict, str, dict | None]:
        value = " ".join(player.split())
        if not value or len(value) > 100:
            raise ValueError("Enter a player name (1-100 characters) or select a suggestion.")
        league = self.league(sport, league_id)
        candidates = self.players(sport, league_id, value, limit=100)
        exact = next((row for row in candidates if row["name"].casefold() == value.casefold()), None)
        if len(candidates) == 100 and exact is None:
            raise ValueError("Player search is too broad. Enter more of the name.")
        return league, value, select_player(candidates, value)

    def snapshot(self, sport: str, league_id: str, player_id: int, season: str, metadata=False) -> dict | None:
        table = "api_sports_player_season_metadata" if metadata else "api_sports_player_seasons"
        query = self.table(table).select("*").eq("sport_slug", sport).eq("league_id", league_id).eq("season", season)
        if not metadata:
            query = query.eq("player_id", player_id)
        rows = self.rows(query.limit(1))
        return rows[0] if rows else None

    def reusable(self, snapshot: dict | None, previous: bool) -> bool:
        if not snapshot:
            return False
        age = self.clock() - parse_iso_datetime(snapshot["synced_at"])
        if age < timedelta(0):
            return False
        populated = bool(snapshot.get("groups", snapshot.get("payload")))
        return (previous and populated) or age < (timedelta(days=1) if previous else timedelta(minutes=5))

    def report(self, sport: str, league_id: str, player: str) -> tuple[str, str]:
        league, value, person = self.validate(sport, league_id, player)
        title = f"{PLAYER_SPORTS[sport]} - current and previous season"
        if person is None:
            return title, "No matching player in this sport/league directory yet. Verified paid members, eligible trial members, or authorized owner/moderator grants can refresh by full name when enabled to discover the player; suggestions use cached names only."
        escape = discord.utils.escape_markdown
        current = league["current_season"]
        lines = [f"**{escape(person['name'])}** - {escape(league['name'])}"]
        for season in (current, previous_season(current)):
            snapshot = self.snapshot(sport, league_id, person["player_id"], season)
            label = "current" if season == current else "previous"
            lines.append(f"\n**{season} ({label})**")
            if not snapshot:
                lines.append("Not cached yet. Use an authorized refresh to load this season.")
                continue
            groups = snapshot["groups"]
            if not groups:
                lines.append("No season statistics supplied for this league/player. Unavailable, not zero.")
            shown = []
            omitted = False
            for group in groups:
                block = "**" + escape(group["name"]) + "**\n" + "\n".join(
                    f"{escape(stat['name'])}: {escape(str(stat['value'])) if stat['value'] is not None else 'Unavailable'}"
                    for stat in group["statistics"]
                )
                if len("\n".join(shown) + block) > 1350:
                    omitted = True
                    continue
                shown.append(block)
            lines.extend(shown)
            if omitted:
                lines.append("Additional stat groups omitted to fit the two-season report.")
            lines.append(escape(snapshot["coverage"][:250]))
            lines.append(f"Updated <t:{int(parse_iso_datetime(snapshot['synced_at']).timestamp())}:R>.")
        lines.append("\nPrevious-season records are reused once populated; current data may lag. Missing stats are not zero.")
        return title, "\n".join(lines)

    def request(self, sport: str, endpoint: str, params: dict) -> list[dict]:
        return self.api._request(endpoint, params) if sport in {"nfl", "ncaa"} else self.multi_api._request(
            BASES[sport], endpoint, params, require_complete=True,
        )

    def store_person(self, sport: str, league_id: str, person: dict):
        self.table("api_sports_player_directory").upsert({
            "sport_slug": sport, "league_id": league_id, "player_id": person["id"],
            "name": person["name"], "synced_at": self.clock().isoformat(),
        }, on_conflict="sport_slug,league_id,player_id").execute()

    def refresh(self, sport: str, league_id: str, player: str, user_id: int) -> str:
        league, value, person = self.validate(sport, league_id, player)
        current = league["current_season"]
        seasons = (current, previous_season(current))
        pending = [
            season for season in seasons if person is None or not self.reusable(
                self.snapshot(sport, league_id, person["player_id"], season), season != current,
            )
        ]
        if not pending:
            return "Reused current data updated within five minutes and the cached previous season; no API request."
        metadata = {}
        for season in pending:
            if sport in {"nfl", "ncaa", "basketball"}:
                cached = self.snapshot(sport, league_id, 0, season, metadata=True)
                if self.reusable(cached, season != current):
                    metadata[season] = cached["payload"]
        needed = len(pending) + (len(pending) - len(metadata) if sport in {"nfl", "ncaa", "basketball"} else 0)
        if person is None and sport in {"nfl", "ncaa", "basketball"}:
            needed += 1
            if not value.isdecimal() and len(value) < 3:
                raise ValueError("Use at least three characters for an uncached player search.")
        if person is None and sport == "football" and not value.isdecimal() and len(value) < 3:
            raise ValueError("Use at least three characters for an uncached player search.")
        found_scoped_stats = person is not None
        with member_request_batch(BASES[sport], user_id, needed):
            if person is None and sport in {"nfl", "ncaa", "basketball"}:
                if value.isdecimal():
                    params = {"id": int(value)}
                else:
                    params = {"search": value}
                profiles = self.request(sport, "players", params)
                normalized = [identity(row) for row in profiles]
                person = select_player([{"player_id": row["id"], "name": row["name"]} for row in normalized], value)
                if person is None:
                    raise ValueError("No provider player matched that name. Try the full name.")
            for season in pending:
                if sport in {"nfl", "ncaa", "basketball"} and season not in metadata:
                    endpoint = "games" if sport == "basketball" else "teams"
                    rows = self.request(sport, endpoint, {"league": int(league_id), "season": season})
                    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                        raise RuntimeError("Season metadata returned an invalid response.")
                    if sport == "basketball":
                        compact = []
                        for row in rows:
                            scope = row.get("league")
                            if type(row.get("id")) is not int or not isinstance(scope, dict) or str(scope.get("id")) != league_id or str(scope.get("season")) != season:
                                raise RuntimeError("Basketball season metadata returned a different league/season.")
                            compact.append({"id": row["id"], "league": {"id": scope["id"], "season": scope["season"]}})
                        rows = compact
                    else:
                        rows = [{"id": identity(row)["id"], "name": row["name"]} for row in rows]
                    metadata[season] = rows
                    self.table("api_sports_player_season_metadata").upsert({
                        "sport_slug": sport, "league_id": league_id, "season": season,
                        "payload": rows, "synced_at": self.clock().isoformat(),
                    }, on_conflict="sport_slug,league_id,season").execute()
                if sport == "football":
                    params = {"league": int(league_id), "season": int(season)}
                    if person or value.isdecimal():
                        params["id"] = person["player_id"] if person else int(value)
                    else:
                        params["search"] = value
                    payload = self.request(sport, "players", params)
                    profiles = [identity(row.get("player")) for row in payload]
                elif sport == "formula-1":
                    params = {"season": int(season)}
                    if person:
                        params["driver"] = person["player_id"]
                    payload = self.request(sport, "rankings/drivers", params)
                    profiles = [identity(row.get("driver")) for row in payload]
                else:
                    payload = self.request(sport, "games/statistics/players" if sport == "basketball" else "players/statistics", {
                        "player" if sport == "basketball" else "id": person["player_id"], "season": season,
                    })
                    profiles = [identity(row.get("player")) for row in payload]
                if person is None:
                    if sport in {"football", "formula-1"}:
                        for candidate in profiles:
                            candidate_groups, _ = self.normalize(sport, league_id, season, candidate["id"], payload, [])
                            if candidate_groups:
                                self.store_person(sport, league_id, candidate)
                    person = select_player([{"player_id": row["id"], "name": row["name"]} for row in profiles], value)
                    if person is None:
                        continue
                groups, coverage = self.normalize(sport, league_id, season, person["player_id"], payload, metadata.get(season, []))
                if groups:
                    found_scoped_stats = True
                    self.store_person(sport, league_id, {"id": person["player_id"], "name": person["name"]})
                self.table("api_sports_player_seasons").upsert({
                    "sport_slug": sport, "league_id": league_id, "player_id": person["player_id"],
                    "season": season, "groups": groups, "coverage": coverage,
                    "synced_at": self.clock().isoformat(),
                }, on_conflict="sport_slug,league_id,player_id,season").execute()
        if person is None:
            raise ValueError("No matching player season data in this league. Try the full provider name.")
        if not found_scoped_stats:
            raise ValueError("The player has no supplied statistics in either season for the selected league. No player identity was added to this league's suggestions.")
        return f"Season lookup reserved {needed} API request(s); cached previous-season data was not refreshed. Every reserved request counts against the daily limits, including failed or unused reservations."

    def normalize(self, sport, league_id, season, player_id, payload, metadata):
        if sport == "basketball":
            for game in metadata:
                scope = game.get("league") if isinstance(game, dict) else None
                if not isinstance(scope, dict) or str(scope.get("id")) != league_id or str(scope.get("season")) != season:
                    raise RuntimeError("Basketball season metadata returned a different league/season.")
            return basketball_totals(payload, metadata, player_id), "Totals from available provider game logs in the selected league; not guaranteed complete season coverage. Missing stat counts are disclosed."
        groups = []
        for row in payload:
            if not isinstance(row, dict):
                raise RuntimeError("Season statistics returned an invalid record.")
            person = identity(row.get("driver" if sport == "formula-1" else "player"))
            if person["id"] != player_id:
                if sport in {"football", "formula-1"}:
                    continue
                raise RuntimeError("Season statistics returned the wrong player.")
            if sport in {"nfl", "ncaa"}:
                team_ids = {identity(team)["id"] for team in metadata}
                if not isinstance(row.get("teams"), list):
                    raise RuntimeError("Football season statistics returned invalid teams.")
                for team in row["teams"]:
                    team_identity = identity(team.get("team"))
                    if team_identity["id"] not in team_ids:
                        continue
                    if not isinstance(team.get("groups"), list):
                        raise RuntimeError("Football season statistics returned invalid groups.")
                    for group in team["groups"]:
                        if not isinstance(group, dict) or not isinstance(group.get("name"), str) or not isinstance(group.get("statistics"), list):
                            raise RuntimeError("Football season statistics returned an invalid group.")
                        values = {}
                        for stat in group["statistics"]:
                            if not isinstance(stat, dict) or not isinstance(stat.get("name"), str):
                                raise RuntimeError("Football season statistics returned an invalid statistic.")
                            values[stat["name"]] = stat.get("value")
                        groups.extend(stat_groups({team_identity["name"] + " - " + group["name"]: values}))
            elif sport == "football":
                if not isinstance(row.get("statistics"), list):
                    raise RuntimeError("Soccer season statistics returned invalid groups.")
                for stats in row["statistics"]:
                    scope = stats.get("league") if isinstance(stats, dict) else None
                    if not isinstance(scope, dict) or str(scope.get("id")) != league_id or str(scope.get("season")) != season:
                        raise RuntimeError("Soccer season statistics returned a different league/season.")
                    team = identity(stats.get("team"))
                    groups.extend(stat_groups({
                        team["name"] + " - " + key: val for key, val in stats.items() if key not in {"team", "league"}
                    }))
            else:
                if str(row.get("season")) != season:
                    raise RuntimeError("Driver standings returned a different season.")
                groups.extend(stat_groups({"Driver season standings": {
                    key: row.get(key) for key in ("position", "points", "wins", "behind")
                }}))
        return groups, "Provider season totals, separated by team where supplied." if sport != "formula-1" else "Provider driver season standings; not telemetry or per-race results."
