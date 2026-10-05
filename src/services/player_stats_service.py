from datetime import datetime, timedelta, timezone
import math

import discord

from src.datetime_utils import parse_iso_datetime
from src.services.api_budget_service import member_request_user
from src.services.api_sports_service import ApiSportsService
from src.services.api_sports_multi_service import ApiSportsMultiService
from src.services.supabase_service import supabase_service

PLAYER_SPORTS = {
    "nfl": "NFL", "ncaa": "College football", "basketball": "Basketball",
    "football": "Soccer", "formula-1": "Formula 1", "mma": "MMA",
    "baseball": "Baseball", "hockey": "Hockey", "rugby": "Rugby",
    "handball": "Handball", "volleyball": "Volleyball", "cricket": "Cricket",
    "cycling": "Cycling",
}
PLAYER_ENDPOINTS = {
    "basketball": ("https://v1.basketball.api-sports.io", "games/statistics/players", "id"),
    "football": ("https://v3.football.api-sports.io", "fixtures/players", "fixture"),
    "formula-1": ("https://v1.formula-1.api-sports.io", "rankings/races", "race"),
}
SUPPORTED_PLAYER_SPORTS = {"nfl", "ncaa", *PLAYER_ENDPOINTS}


def identity(value, allow_zero: bool = False) -> dict:
    if not isinstance(value, dict) or type(value.get("id")) is not int or value["id"] < (0 if allow_zero else 1) or not isinstance(value.get("name"), str):
        raise RuntimeError("Player statistics returned an invalid identity.")
    if not value["name"] or len(value["name"]) > 200:
        raise RuntimeError("Player statistics returned an invalid identity name.")
    return value


def stat_groups(values: dict) -> list[dict]:
    def flatten(value, prefix: str, depth: int = 0) -> list[dict]:
        if depth > 5:
            raise RuntimeError("Player statistics exceeded the supported nesting depth.")
        if isinstance(value, dict):
            return [
                stat for key, child in value.items()
                for stat in flatten(child, f"{prefix} {key}".strip(), depth + 1)
            ]
        if value is not None and not isinstance(value, (str, int, float, bool)):
            raise RuntimeError("Player statistics returned an invalid value.")
        if isinstance(value, float) and not math.isfinite(value):
            raise RuntimeError("Player statistics returned a non-finite value.")
        return [{"name": prefix.replace("_", " "), "value": value}]

    return [
        {"name": key.replace("_", " ").title(), "statistics": flatten(value, "" if isinstance(value, dict) else key)}
        for key, value in values.items()
    ]


def player_records(payload: list) -> list[dict]:
    if not isinstance(payload, list):
        raise RuntimeError("Player statistics returned an invalid response.")
    records = {}
    for team in payload:
        if not isinstance(team, dict) or not isinstance(team.get("team"), dict) or not isinstance(team.get("groups"), list):
            raise RuntimeError("Player statistics returned an invalid team.")
        team_identity = identity(team["team"])
        for group in team["groups"]:
            if not isinstance(group, dict) or not isinstance(group.get("name"), str) or not isinstance(group.get("players"), list):
                raise RuntimeError("Player statistics returned an invalid group.")
            for entry in group["players"]:
                if not isinstance(entry, dict) or not isinstance(entry.get("player"), dict) or not isinstance(entry.get("statistics"), list):
                    raise RuntimeError("Player statistics returned an invalid player.")
                player = identity(entry["player"])
                for stat in entry["statistics"]:
                    if not isinstance(stat, dict) or not isinstance(stat.get("name"), str) or not (
                        stat.get("value") is None or isinstance(stat["value"], (str, int, float))
                    ):
                        raise RuntimeError("Player statistics returned an invalid statistic.")
                    if isinstance(stat.get("value"), float) and not math.isfinite(stat["value"]):
                        raise RuntimeError("Player statistics returned a non-finite value.")
                key = (team_identity["id"], player["id"])
                record = records.setdefault(key, {
                    "id": player["id"], "name": player["name"], "team": team_identity["name"], "groups": [],
                })
                record["groups"].append({"name": group["name"], "statistics": entry["statistics"]})
    return list(records.values())


def athlete_records(sport: str, payload: list, game_id: int, game: dict) -> list[dict]:
    if sport not in SUPPORTED_PLAYER_SPORTS:
        raise ValueError("No verified player adapter for this sport.")
    if sport in {"nfl", "ncaa"}:
        return player_records(payload)
    if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
        raise RuntimeError("Player statistics returned an invalid response.")
    if sport == "football":
        records = []
        for row in payload:
            team = identity(row.get("team"))
            if not isinstance(row.get("players"), list):
                raise RuntimeError("Soccer statistics returned an invalid player list.")
            for entry in row["players"]:
                if not isinstance(entry, dict) or not isinstance(entry.get("statistics"), list):
                    raise RuntimeError("Soccer statistics returned invalid player statistics.")
                player = identity(entry.get("player"), allow_zero=True)
                groups = []
                for stats in entry["statistics"]:
                    if not isinstance(stats, dict):
                        raise RuntimeError("Soccer statistics returned an invalid stat group.")
                    groups.extend(stat_groups(stats))
                records.append({
                    "id": player["id"] if player["id"] != 0 else None,
                    "name": player["name"], "team": team["name"], "groups": groups,
                })
        return records
    records = []
    for row in payload:
        event = row.get("game" if sport == "basketball" else "race")
        if not isinstance(event, dict) or event.get("id") != game_id:
            raise RuntimeError("Player statistics returned data for a different event.")
        player = identity(row.get("player" if sport == "basketball" else "driver"))
        team = row.get("team")
        if not isinstance(team, dict) or type(team.get("id")) is not int:
            raise RuntimeError("Player statistics returned an invalid team.")
        if sport == "basketball":
            names = {str(game["home_id"]): game["home_name"], str(game["away_id"]): game["away_name"]}
            if str(team["id"]) not in names or not names[str(team["id"])]:
                raise RuntimeError("Basketball statistics returned a team outside the cached game.")
            team_name = names[str(team["id"])]
        else:
            team_name = identity(team)["name"]
        values = {key: value for key, value in row.items() if key not in {"game", "race", "team", "player", "driver"}}
        records.append({"id": player["id"], "name": player["name"], "team": team_name, "groups": stat_groups(values)})
    return records


class PlayerStatsService:
    def __init__(self, database=None, clock=None, api=None, multi_api=None):
        self.db = database or supabase_service
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.api = api or ApiSportsService()
        self.multi_api = multi_api or ApiSportsMultiService()

    def game(self, sport: str, game_id: int) -> dict:
        if sport not in SUPPORTED_PLAYER_SPORTS:
            if sport == "mma":
                raise ValueError("MMA fighter stats are documented, but no completed cached fight was available to verify the response. Fighter reports are not enabled yet.")
            if sport in {"cricket", "cycling"}:
                raise ValueError(f"{PLAYER_SPORTS[sport]} has no configured data feed. Individual statistics are unavailable.")
            if sport in PLAYER_SPORTS:
                raise ValueError(f"The supplied API-Sports {PLAYER_SPORTS[sport]} documentation has no individual-player statistics endpoint. A different data source is required; team scores are not player stats.")
            raise ValueError("Select a tracked sport.")
        if isinstance(game_id, bool) or not isinstance(game_id, int) or game_id <= 0:
            raise ValueError("Enter a positive game ID from /results or /schedule.")
        nfl = sport == "nfl"
        query = self.db._ensure_client().table("api_sports_nfl_games" if nfl else "api_sports_events")
        query = query.select("*").eq("game_id" if nfl else "event_id", game_id)
        if not nfl:
            query = query.eq("sport_slug", sport)
        rows = query.limit(1).execute().data
        if not isinstance(rows, list):
            raise RuntimeError("Game cache returned an invalid response.")
        if not rows:
            raise ValueError("Game ID is not in this sport's cache. Get an ID from /results or /schedule.")
        return rows[0]

    def snapshot(self, sport: str, game_id: int) -> dict | None:
        rows = self.db._ensure_client().table("api_sports_player_game_stats").select("*").eq(
            "sport_slug", sport,
        ).eq("game_id", game_id).limit(1).execute().data
        if not isinstance(rows, list):
            raise RuntimeError("Player cache returned an invalid response.")
        return rows[0] if rows else None

    def report(self, sport: str, game_id: int, player: str | None = None) -> tuple[str, str]:
        if player is not None:
            player = " ".join(player.split())
            if not player or len(player) > 100:
                raise ValueError("Enter a player name or numeric player ID (1-100 characters), or omit it to list available players.")
        game = self.game(sport, game_id)
        title = f"{PLAYER_SPORTS[sport]} " + ("driver results" if sport == "formula-1" else "player statistics")
        snapshot = self.snapshot(sport, game_id)
        if snapshot is None:
            return title, "Player data has not been fetched for this game. Verified paid members, eligible trial members, or authorized owner/moderator grants can request refresh when enabled."
        records = athlete_records(sport, snapshot["payload"], game_id, game)
        escape = discord.utils.escape_markdown
        updated = int(parse_iso_datetime(snapshot["synced_at"]).timestamp())
        if player is None:
            if not records:
                return title, f"No individual statistics were supplied for this game/session (updated <t:{updated}:R>). This is unavailable data, not zero."
            lines = [f"Available players/drivers for game/session {game_id}. Run /playerstats again with a name or player/driver ID."]
            shown = 0
            for row in records:
                identifier = f"ID {row['id']}" if row["id"] is not None else "Provider returned ID 0; search by name"
                line = f"{escape(row['name'])} - {identifier} - {escape(row['team'])}"
                if shown >= 20 or len("\n".join(lines) + line) > 3000:
                    break
                lines.append(line)
                shown += 1
            lines.append(f"\nShowing {shown} of {len(records)} available records; name search includes all records. Updated <t:{updated}:R>. Provider coverage may be incomplete.")
            return title, "\n".join(lines)
        if player.isdecimal():
            matches = [row for row in records if row["id"] == int(player)]
        else:
            matches = [row for row in records if row["name"].casefold() == player.casefold()]
            if not matches:
                matches = [row for row in records if player.casefold() in row["name"].casefold()]
        if not matches:
            return title, f"No matching player statistics in this game snapshot (updated <t:{updated}:R>). This does not mean the player recorded zero. Try a full name or player ID."
        if len(matches) != 1:
            names = ", ".join(
                f"{escape(row['name'][:80])} ({row['id'] if row['id'] is not None else 'ID unavailable'}, {escape(row['team'][:80])})"
                for row in matches[:5]
            )
            raise ValueError("Player name is ambiguous. Use a full name or available player ID: " + names)
        record = matches[0]
        nfl = sport == "nfl"
        start = game["kickoff_at"] if nfl else game["start_at"]
        if sport == "formula-1":
            label = game["event_name"] + " - " + str(game["raw_event"]["type"])
            scope = "Per-session provider result, not season totals or necessarily a Grand Prix race."
        else:
            home = game["home_team_name"] if nfl else game["home_name"]
            away = game["away_team_name"] if nfl else game["away_name"]
            label = f"{home} vs {away}"
            scope = "Per-game provider stats, not season totals."
        lines = [
            f"**{escape(record['name'])}** ({record['id'] if record['id'] is not None else 'provider returned ID 0; name-only lookup'}) - {escape(record['team'])}",
            f"{escape(label)} - <t:{int(parse_iso_datetime(start).timestamp())}:f>",
            f"Game/session ID: {game_id}. {scope}",
        ]
        footer = f"\nUpdated <t:{updated}:R>. Data may be stale or incomplete. Missing values are unavailable, not zero."
        omitted = False
        if not any(group["statistics"] for group in record["groups"]):
            lines.append("No statistic values supplied for this player/driver. Data is unavailable, not zero.")
        for group in record["groups"]:
            block = "\n**" + escape(group["name"]) + "**\n" + "\n".join(
                f"{escape(stat['name'])}: {escape(str(stat['value'])) if stat['value'] is not None else 'Unavailable'}"
                for stat in group["statistics"]
            )
            if len("\n".join(lines) + block + footer) > 3800:
                omitted = True
                continue
            lines.append(block)
        if omitted:
            lines.append("Some stat groups omitted to fit this report.")
        lines.append(footer)
        return title, "\n".join(lines)

    def refresh(self, sport: str, game_id: int, user_id: int) -> str:
        game = self.game(sport, game_id)
        snapshot = self.snapshot(sport, game_id)
        now = self.clock()
        if snapshot and timedelta(0) <= now - parse_iso_datetime(snapshot["synced_at"]) < timedelta(minutes=5):
            return "Reused this game's player snapshot updated within five minutes; no provider call."
        token = member_request_user.set(str(user_id))
        try:
            if sport in {"nfl", "ncaa"}:
                payload = self.api._request("games/statistics/players", {"id": game_id})
            else:
                base, endpoint, parameter = PLAYER_ENDPOINTS[sport]
                payload = self.multi_api._request(base, endpoint, {parameter: game_id})
            athlete_records(sport, payload, game_id, game)
            self.db._ensure_client().table("api_sports_player_game_stats").upsert({
                "sport_slug": sport, "game_id": game_id, "payload": payload,
                "synced_at": self.clock().isoformat(),
            }, on_conflict="sport_slug,game_id").execute()
        finally:
            member_request_user.reset(token)
        return "Refreshed one game's player statistics with one shared-budget provider request. Provider data may lag."
