from datetime import datetime, timedelta, timezone
import math
import re

from src.datetime_utils import parse_iso_datetime
from src.services.supabase_service import supabase_service
from src.services.api_budget_service import member_request_user
from src.services.api_sports_service import ApiSportsService, normalize_game
from src.services.api_sports_multi_service import ApiSportsMultiService, DATE_PRODUCTS, DATE_PRODUCT_PARAMS, normalize_event

SPORTS = {
    "nfl": "NFL", "ncaa": "College football", "basketball": "Basketball",
    "football": "Soccer", "hockey": "Hockey", "baseball": "Baseball",
    "formula-1": "Formula 1",
}
FINAL = {"FT", "AOT", "AET", "PEN", "COMPLETED", "FINISHED"}


def score(value) -> float | None:
    if isinstance(value, dict):
        value = value.get("total")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def team_name(value: str) -> str:
    value = " ".join(value.split())
    if not value or len(value) > 100:
        raise ValueError("Enter the full team name (1-100 characters).")
    return value


def name_tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", value.casefold()))


def resolve_team(value: str, names: set[str]) -> str | None:
    exact = sorted(name for name in names if name.casefold() == value.casefold())
    if exact:
        return exact[0]
    tokens = name_tokens(value)
    candidates = sorted(name for name in names if tokens and name_tokens(name) and (
        tokens <= name_tokens(name) or name_tokens(name) <= tokens
    ))
    if len(candidates) > 1:
        raise ValueError("Team name is ambiguous. Use one of: " + ", ".join(candidates[:10]))
    return candidates[0] if candidates else None


class MemberStatsService:
    """Cached reports with separately authorized, budgeted event refresh."""

    def __init__(self, database=None, clock=None):
        self.db = database or supabase_service
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def events(self, sport: str, upcoming: bool, team: str | None = None) -> list[dict]:
        if sport not in SPORTS:
            raise ValueError("Select a supported sport.")
        if sport == "formula-1" and team is not None:
            raise ValueError("Formula 1 schedules/results list sessions, not teams. Leave the team field empty.")
        now = self.clock()
        start = now if upcoming else now - timedelta(days=30)
        end = now + timedelta(days=7) if upcoming else now
        nfl = sport == "nfl"
        table = "api_sports_nfl_games" if nfl else "api_sports_events"
        time_column = "kickoff_at" if nfl else "start_at"
        fields = (
            "game_id,kickoff_at,home_team_name,away_team_name,home_score,away_score,status_short,synced_at"
            if nfl else
            "event_id,start_at,home_name,away_name,home_score,away_score,status_code,synced_at"
            + (",event_name,raw_event" if sport == "formula-1" else "")
        )
        rows = []
        # Fetch candidates safely, then resolve one team before displaying results.
        for side in (["home", "away"] if team is not None else [None]):
            query = self.db._ensure_client().table(table).select(fields)
            if not nfl:
                query = query.eq("sport_slug", sport)
            if side:
                name = team_name(team)
                words = re.findall(r"[^\W_]+", name, re.UNICODE)
                if not words:
                    raise ValueError("Enter a team name containing letters or numbers.")
                # Leading token handles provider abbreviations (LSU vs LSU Tigers).
                pattern = name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                if len(words) == 1:
                    pattern = "%" + pattern + "%"
                else:
                    pattern = "%" + words[0] + "%"
                query = query.ilike(f"{side}_team_name" if nfl else f"{side}_name", pattern)
            data = query.gte(time_column, start.isoformat()).lte(time_column, end.isoformat()).order(
                time_column, desc=not upcoming,
            ).limit(100).execute().data
            if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
                raise RuntimeError("Sports cache returned an invalid response.")
            if team is not None and len(data) >= 100:
                raise ValueError("Team search is too broad. Use a more specific team name.")
            for row in data:
                event = {
                    "id": row["game_id"] if nfl else row["event_id"],
                    "start": row[time_column], "home": row["home_team_name"] if nfl else row["home_name"],
                    "away": row["away_team_name"] if nfl else row["away_name"],
                    "home_score": score(row["home_score"]), "away_score": score(row["away_score"]),
                    "status": row["status_short"] if nfl else row["status_code"],
                    "synced_at": row["synced_at"],
                    "name": row.get("event_name"),
                    "session": (row.get("raw_event") or {}).get("type") if sport == "formula-1" else None,
                }
                rows.append(event)
        if team is not None:
            names = {str(row[side]) for row in rows for side in ("home", "away") if row[side]}
            resolved = resolve_team(team_name(team), names)
            rows = [row for row in rows if resolved in {row["home"], row["away"]}] if resolved else []
        unique = {row["id"]: row for row in rows}
        return sorted(unique.values(), key=lambda row: parse_iso_datetime(row["start"]), reverse=not upcoming)

    def report(self, mode: str, sport: str, team: str | None = None, opponent: str | None = None) -> tuple[str, str]:
        if mode not in {"matchup", "teamstats", "schedule", "results"}:
            raise ValueError("Unknown stats report.")
        if sport == "formula-1" and mode in {"teamstats", "matchup"}:
            raise ValueError("Formula 1 supports /schedule, /results and /playerstats, not team-form or matchup reports.")
        if team is not None:
            team = team_name(team)
        if mode in {"teamstats", "matchup"} and team is None:
            raise ValueError("Enter a full team name.")
        if mode == "matchup":
            if opponent is None:
                raise ValueError("Enter both full team names.")
            opponent = team_name(opponent)
            if team.casefold() == opponent.casefold():
                raise ValueError("Choose two different teams.")
        rows = self.events(sport, mode in {"schedule", "matchup"}, team)
        resolved_team = resolve_team(team, {
            str(row[side]) for row in rows for side in ("home", "away") if row[side]
        }) if team and rows else team
        if mode == "matchup":
            resolved_opponent = resolve_team(opponent, {
                str(row[side]) for row in rows for side in ("home", "away")
                if row[side] and row[side] != resolved_team
            })
            rows = [row for row in rows if resolved_opponent in {row["home"], row["away"]}] if resolved_opponent else []
        if mode in {"results", "teamstats"}:
            rows = [row for row in rows if row["status"] in FINAL]
        title = f"{SPORTS[sport]} • {mode.title()}"
        if not rows:
            return title, "No matching cached events in the next 7 days or past 30 days, as applicable. Use full team names. No live lookup was made."
        selected = rows[:10]
        lines = []
        for row in selected:
            stamp = int(parse_iso_datetime(row["start"]).timestamp())
            home_score, away_score = row["home_score"], row["away_score"]
            result = f"{home_score:g}–{away_score:g}" if home_score is not None and away_score is not None else "Scores unavailable"
            if sport == "formula-1":
                lines.append(f"<t:{stamp}:f> • {row['name']} • {row['session']} • {row['status']}")
            else:
                lines.append(f"<t:{stamp}:f> • {row['home']} vs {row['away']} • {row['status']} • {result}")
        if mode == "teamstats":
            wins = losses = ties = 0
            points_for = points_against = 0.0
            for row in selected:
                own, other = (row["home_score"], row["away_score"]) if row["home"] == resolved_team else (row["away_score"], row["home_score"])
                if own is not None and other is not None:
                    wins += own > other
                    losses += own < other
                    ties += own == other
                    points_for += own
                    points_against += other
            scored = wins + losses + ties
            summary = f"Recent cached form: {wins}W / {losses}L / {ties}T from {scored} scored games. Not season standings."
            if scored:
                summary += f"\nAverage scored: {points_for / scored:.1f}; conceded: {points_against / scored:.1f}."
            lines.insert(0, summary)
        updated = min(parse_iso_datetime(row["synced_at"]) for row in selected)
        lines.append(f"\nShowing {len(selected)} cached events maximum. Oldest displayed update: <t:{int(updated.timestamp())}:R>. Data may be stale or incomplete; scores are not betting advice.")
        return title, "\n".join(lines)

    def refresh(self, sport: str, user_id: int) -> str:
        if sport not in SPORTS:
            raise ValueError("Select a supported sport.")
        if sport == "formula-1":
            raise ValueError("Formula 1 schedules/results are cached only. Use /gamestats and pick a session for a limited driver-result refresh.")
        now = self.clock()
        day = now.astimezone(timezone.utc).date().isoformat()
        nfl = sport == "nfl"
        query = self.db._ensure_client().table("api_sports_nfl_games" if nfl else "api_sports_events").select("synced_at")
        if not nfl:
            query = query.eq("sport_slug", sport)
        column = "kickoff_at" if nfl else "start_at"
        midnight = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        data = query.gte(column, midnight.isoformat()).lt(
            column, (midnight + timedelta(days=1)).isoformat(),
        ).order("synced_at", desc=True).limit(1).execute().data
        if not isinstance(data, list):
            raise RuntimeError("Refresh cache returned an invalid response.")
        if data and timedelta(0) <= now - parse_iso_datetime(data[0]["synced_at"]) < timedelta(minutes=5):
            return "Today's cache was updated within five minutes; reused it without a provider call. Other dates remain cached."
        token = member_request_user.set(str(user_id))
        try:
            if sport == "nfl":
                api = ApiSportsService(client=self.db._ensure_client())
                rows = api._request("games", {"date": day, "league": 1})
                api._upsert("api_sports_nfl_games", [normalize_game(row, now) for row in rows], "game_id")
            else:
                api = ApiSportsMultiService(client=self.db._ensure_client())
                rows = api._request(*DATE_PRODUCTS[sport], params={"date": day, **DATE_PRODUCT_PARAMS.get(sport, {})})
                api._store_events([normalize_event(sport, row, now) for row in rows])
        finally:
            member_request_user.reset(token)
        return f"Provider refresh completed for {day} (UTC) only, at <t:{int(now.timestamp())}:T>. Other dates remain cached. Provider data may lag; this is not a real-time guarantee."
