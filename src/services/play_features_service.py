"""Auto-settle suggestions, recaps, personal stats, tails, and capper follows for official plays."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from src.datetime_utils import parse_iso_datetime
from src.services.settlement_service import SettlementService
from src.services.supabase_service import supabase_service

EASTERN = ZoneInfo("America/New_York")
SPORT_NAMES = {
    "nfl": "NFL", "ncaa": "College Football", "basketball": "Basketball", "baseball": "Baseball",
    "hockey": "Hockey", "football": "Soccer", "mma": "MMA", "other": "Other", "mixed": "Mixed",
    "official": "Unspecified",
}
# Sports whose cached API-Sports events carry final full-game scores we can grade against.
GRADABLE_SPORTS = {"nfl", "ncaa", "basketball", "baseball", "hockey"}
FINAL_CODES = {"FT", "AOT", "AP"}
LEG_TEXT_FIELDS = ("sport", "home_name", "away_name", "event_date", "market", "side", "scope")
SETTLED_RESULTS = {"win", "loss", "void", "partial"}
SUGGEST_LOOKBACK = timedelta(days=14)


def _clean_text(value, limit: int = 120) -> str | None:
    if not isinstance(value, str):
        return None
    value = " ".join(value.split())[:limit]
    return value or None


def clean_leg_details(leg: dict) -> dict | None:
    """Keep only the structured game fields the grader understands; drop anything malformed."""
    details = {field: _clean_text(leg.get(field)) for field in LEG_TEXT_FIELDS}
    sport = (details["sport"] or "").lower()
    details["sport"] = sport if sport in SPORT_NAMES and sport not in {"mixed", "official"} else None
    for field in ("market", "side", "scope"):
        details[field] = details[field].lower() if details[field] else None
    try:
        details["event_date"] = date.fromisoformat(details["event_date"] or "").isoformat()
    except ValueError:
        details["event_date"] = None
    line = leg.get("line")
    try:
        details["line"] = float(line) if line is not None and not isinstance(line, bool) else None
    except (TypeError, ValueError):
        details["line"] = None
    if not details["sport"]:
        return None
    return details


def play_sport_slug(leg_records: list[dict] | None) -> str | None:
    sports = {
        (leg.get("details") or {}).get("sport")
        for leg in leg_records or []
    } - {None}
    if not sports:
        return None
    return sports.pop() if len(sports) == 1 else "mixed"


def team_key(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def team_matches(slip_name: str | None, api_name: str | None) -> bool:
    slip, api = team_key(slip_name), team_key(api_name)
    if not slip or not api:
        return False
    if slip == api:
        return True
    # Slips often abbreviate ("Lakers" vs "Los Angeles Lakers"); require a meaningful fragment.
    shorter, longer = sorted((slip, api), key=len)
    return len(shorter) >= 4 and shorter in longer


def score_value(value) -> float | None:
    if isinstance(value, dict):
        value = value.get("total")
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def grade_leg(details: dict, event: dict) -> str | None:
    """Grade one full-game moneyline/spread/total leg against a final event, or return None."""
    if details.get("scope") not in {None, "full_game", "full_game_including_overtime"}:
        return None
    if str(event.get("status_code") or "").upper() not in FINAL_CODES:
        return None
    home, away = score_value(event.get("home_score")), score_value(event.get("away_score"))
    if home is None or away is None:
        return None
    market, side, line = details.get("market"), details.get("side"), details.get("line")
    if market == "moneyline" and side in {"home", "away"}:
        if home == away:
            return None
        margin = home - away if side == "home" else away - home
    elif market == "spread" and side in {"home", "away"} and line is not None:
        margin = (home - away if side == "home" else away - home) + line
    elif market == "total" and side in {"over", "under"} and line is not None and line >= 0:
        margin = home + away - line
        if side == "under":
            margin = -margin
    else:
        return None
    return "win" if margin > 0 else "loss" if margin < 0 else "void"


def combine_leg_results(results: list[str | None]) -> str | None:
    """A known losing leg loses the play; otherwise every leg must be known and all win or all push."""
    if not results:
        return None
    if "loss" in results:
        return "loss"
    if all(result == "win" for result in results):
        return "win"
    if all(result == "void" for result in results):
        return "void"
    return None


def signed_units(play: dict) -> float:
    return SettlementService.tally_for_result(play["status"], float(play["units"]), play.get("odds"))


def settled_time(play: dict) -> datetime:
    parsed = parse_iso_datetime(str(play.get("settled_at") or play["created_at"]))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(EASTERN)


def record_summary(plays: list[dict]) -> dict:
    counts = {result: 0 for result in SETTLED_RESULTS}
    net = risked = 0.0
    for play in plays:
        status = play.get("status")
        if status not in SETTLED_RESULTS:
            continue
        counts[status] += 1
        net += signed_units(play)
        if status != "void":
            risked += float(play["units"])
    return {
        "wins": counts["win"], "losses": counts["loss"], "voids": counts["void"], "partials": counts["partial"],
        "net": round(net, 2), "risked": round(risked, 2),
        "roi": round(net / risked * 100, 1) if risked else 0.0,
    }


def format_record(summary: dict) -> str:
    text = f"{summary['wins']}-{summary['losses']}"
    if summary["voids"]:
        text += f"-{summary['voids']}"
    if summary["partials"]:
        text += f" ({summary['partials']} partial)"
    return text


def current_streak(plays: list[dict]) -> str:
    """Current win/loss streak from the most recent settled plays, ignoring pushes."""
    streak_result, length = None, 0
    for play in sorted(plays, key=settled_time, reverse=True):
        status = play.get("status")
        if status not in {"win", "loss"}:
            continue
        if streak_result is None:
            streak_result = status
        if status != streak_result:
            break
        length += 1
    if not length:
        return "—"
    return f"{'W' if streak_result == 'win' else 'L'}{length}"


def unique_published(plays: list[dict]) -> list[dict]:
    """Mirror the tracker: count only published plays, once per tracked message."""
    linked = {}
    for play in sorted(plays, key=lambda row: int(row["id"])):
        if play.get("message_id"):
            linked.setdefault(str(play["message_id"]), play)
    return list(linked.values())


def build_recap(plays: list[dict], users: list[dict], sports: dict, start: datetime, end: datetime) -> dict:
    names = {str(user["id"]): user.get("display_name") or user.get("username") or str(user["id"]) for user in users}
    period = [
        play for play in unique_published(plays)
        if play.get("status") in SETTLED_RESULTS and start <= settled_time(play) < end
    ]
    history = [play for play in unique_published(plays) if play.get("status") in SETTLED_RESULTS and settled_time(play) < end]

    cappers = []
    for user_id in sorted({str(play["user_id"]) for play in period}):
        mine = [play for play in period if str(play["user_id"]) == user_id]
        summary = record_summary(mine)
        summary["name"] = names.get(user_id, user_id)
        summary["streak"] = current_streak([play for play in history if str(play["user_id"]) == user_id])
        cappers.append(summary)
    cappers.sort(key=lambda row: row["net"], reverse=True)

    by_sport = {}
    for play in period:
        slug = sports.get(play.get("sport_id"), "official")
        by_sport.setdefault(SPORT_NAMES.get(slug, slug.title()), []).append(play)
    sport_rows = sorted(
        ({"sport": sport, **record_summary(rows)} for sport, rows in by_sport.items()),
        key=lambda row: row["net"], reverse=True,
    )

    best = max(period, key=signed_units, default=None)
    best_play = None
    if best is not None and signed_units(best) > 0:
        best_play = {"id": best["id"], "name": names.get(str(best["user_id"]), ""), "units": signed_units(best), "odds": best.get("odds")}
    return {"total": record_summary(period), "cappers": cappers, "sports": sport_rows, "best_play": best_play, "count": len(period)}


def previous_week(now: datetime) -> tuple[datetime, datetime]:
    today = now.astimezone(EASTERN).replace(hour=0, minute=0, second=0, microsecond=0)
    end = today - timedelta(days=today.weekday())
    return end - timedelta(days=7), end


def previous_month(now: datetime) -> tuple[datetime, datetime]:
    end = now.astimezone(EASTERN).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    start = (end - timedelta(days=1)).replace(day=1)
    return start, end


def vault_leaderboard(bets: list[dict], start: datetime, end: datetime, limit: int = 10) -> list[dict]:
    rows = {}
    for bet in bets:
        if bet.get("status") not in {"win", "loss", "void"} or not bet.get("units") or not bet.get("settled_at"):
            continue
        if not start <= settled_time(bet) < end:
            continue
        rows.setdefault(str(bet["owner_id"]), {"name": bet.get("owner_name") or bet["owner_id"], "bets": []})["bets"].append(bet)
    board = []
    for owner_id, data in rows.items():
        summary = record_summary(data["bets"])
        board.append({"owner_id": owner_id, "name": data["name"], **summary})
    board.sort(key=lambda row: (row["net"], row["roi"]), reverse=True)
    return board[:limit]


class PlayFeaturesService:
    def __init__(self, database=None) -> None:
        self.db = database or supabase_service

    def _client(self):
        return self.db._ensure_client()

    def _all(self, build) -> list[dict]:
        rows, offset = [], 0
        while True:
            batch = self.db._execute(lambda: build().range(offset, offset + 999).execute()).data or []
            rows.extend(batch)
            if len(batch) < 1000:
                return rows
            offset += 1000

    # ---- Sports ---------------------------------------------------------
    def ensure_sport(self, slug: str) -> int:
        existing = self.db.select("sports", "id", {"api_slug": slug}).data
        if existing:
            return int(existing[0]["id"])
        return int(self.db.insert("sports", {"name": SPORT_NAMES.get(slug, slug.title()), "api_slug": slug, "is_active": True}).data[0]["id"])

    def sport_slugs(self) -> dict:
        return {row["id"]: row["api_slug"] for row in self.db.select("sports", "id,api_slug").data or []}

    # ---- History --------------------------------------------------------
    def history(self) -> tuple[list[dict], list[dict], dict]:
        client = self._client()
        plays = self._all(lambda: client.table("plays").select(
            "id,user_id,sport_id,units,odds,status,message_id,created_at,settled_at"
        ).order("id"))
        users = self._all(lambda: client.table("users").select("id,discord_user_id,display_name,username").order("id"))
        return plays, users, self.sport_slugs()

    def vault_bets(self, owner_id: str | None = None) -> list[dict]:
        client = self._client()

        def build():
            query = client.table("member_bets").select("owner_id,owner_name,status,units,odds,settled_at,created_at").in_(
                "status", ["win", "loss", "void"]
            )
            if owner_id is not None:
                query = query.eq("owner_id", str(owner_id))
            return query.order("id")

        return self._all(build)

    def user_id_for_discord(self, discord_user_id: int) -> int | None:
        rows = self.db.select("users", "id", {"discord_user_id": str(discord_user_id)}).data
        return int(rows[0]["id"]) if rows else None

    def personal_stats(self, discord_user_id: int, now: datetime | None = None) -> dict:
        now = (now or datetime.now(EASTERN)).astimezone(EASTERN)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        plays, users, sports = self.history()
        published = [play for play in unique_published(plays) if play.get("status") in SETTLED_RESULTS]

        capper = None
        user_id = next((str(user["id"]) for user in users if str(user.get("discord_user_id")) == str(discord_user_id)), None)
        mine = [play for play in published if str(play["user_id"]) == user_id] if user_id else []
        if mine:
            by_sport = {}
            for play in mine:
                slug = sports.get(play.get("sport_id"), "official")
                by_sport.setdefault(SPORT_NAMES.get(slug, slug.title()), []).append(play)
            capper = {
                "month": record_summary([play for play in mine if settled_time(play) >= month_start]),
                "all_time": record_summary(mine),
                "streak": current_streak(mine),
                "sports": sorted(({"sport": name, **record_summary(rows)} for name, rows in by_sport.items()), key=lambda row: row["net"], reverse=True),
            }

        tailed_ids = {int(row["play_id"]) for row in self.db.select("play_tails", "play_id", {"discord_user_id": str(discord_user_id)}).data or []}
        tailed = [play for play in published if int(play["id"]) in tailed_ids]
        open_tails = len(tailed_ids) - len(tailed)
        vault = self.vault_bets(str(discord_user_id))
        return {
            "capper": capper,
            "tails": {**record_summary(tailed), "open": open_tails, "count": len(tailed_ids)},
            "vault": {**record_summary(vault), "count": len(vault), "month": record_summary([bet for bet in vault if bet.get("settled_at") and settled_time(bet) >= month_start])},
        }

    # ---- Tails ---------------------------------------------------------
    def toggle_tail(self, play_id: int, discord_user_id: int) -> tuple[bool, int]:
        match = {"play_id": int(play_id), "discord_user_id": str(discord_user_id)}
        if self.db.select("play_tails", "play_id", match).data:
            self.db.delete("play_tails", match)
            tailing = False
        else:
            self.db.upsert("play_tails", match, ["play_id", "discord_user_id"])
            tailing = True
        return tailing, self.tail_count(play_id)

    def tail_count(self, play_id: int) -> int:
        return len(self.db.select("play_tails", "discord_user_id", {"play_id": int(play_id)}).data or [])

    # ---- Auto-settle suggestions ---------------------------------------
    def suggestion_candidates(self, now: datetime | None = None) -> list[dict]:
        now = now or datetime.now(timezone.utc)
        client = self._client()
        plays = self.db._execute(lambda: client.table("plays").select(
            "id,user_id,units,odds,legs,status,message_id,team_name,created_at"
        ).eq("status", "open").is_("auto_suggested_at", "null").gte(
            "created_at", (now - SUGGEST_LOOKBACK).isoformat()
        ).order("id").limit(200).execute()).data or []
        if not plays:
            return []
        ids = [int(play["id"]) for play in plays]
        legs = self.db._execute(lambda: client.table("play_legs").select(
            "play_id,leg_number,selection,odds,details"
        ).in_("play_id", ids).execute()).data or []
        for play in plays:
            play["leg_records"] = sorted((leg for leg in legs if int(leg["play_id"]) == int(play["id"])), key=lambda leg: leg["leg_number"])
        # Only plays whose every leg has structured details from the image reader can be graded.
        return [
            play for play in plays
            if play["leg_records"] and len(play["leg_records"]) == int(play.get("legs") or 0)
            and all((leg.get("details") or {}).get("sport") in GRADABLE_SPORTS for leg in play["leg_records"])
        ]

    def find_event(self, details: dict, created_at: str) -> tuple[dict, bool] | None:
        sport = details.get("sport")
        if sport not in GRADABLE_SPORTS or not details.get("home_name") or not details.get("away_name"):
            return None
        if details.get("event_date"):
            start = datetime.combine(date.fromisoformat(details["event_date"]), datetime.min.time(), EASTERN)
            end = start + timedelta(days=1)
        else:
            posted = parse_iso_datetime(created_at)
            if posted.tzinfo is None:
                posted = posted.replace(tzinfo=timezone.utc)
            start, end = posted - timedelta(hours=12), posted + timedelta(days=4)
        client = self._client()
        if sport == "nfl":
            rows = self.db._execute(lambda: client.table("api_sports_nfl_games").select(
                "game_id,kickoff_at,home_team_name,away_team_name,status_short,home_score,away_score"
            ).gte("kickoff_at", start.isoformat()).lt("kickoff_at", end.isoformat()).execute()).data or []
            events = [{
                "id": str(row["game_id"]), "start_at": row["kickoff_at"], "home_name": row["home_team_name"],
                "away_name": row["away_team_name"], "status_code": row["status_short"],
                "home_score": row["home_score"], "away_score": row["away_score"],
            } for row in rows]
        else:
            rows = self.db._execute(lambda: client.table("api_sports_events").select(
                "event_id,start_at,home_name,away_name,status_code,home_score,away_score"
            ).eq("sport_slug", sport).gte("start_at", start.isoformat()).lt("start_at", end.isoformat()).execute()).data or []
            events = [{**row, "id": str(row["event_id"])} for row in rows]

        matches = []
        for event in events:
            if team_matches(details["home_name"], event.get("home_name")) and team_matches(details["away_name"], event.get("away_name")):
                matches.append((event, False))
            elif team_matches(details["home_name"], event.get("away_name")) and team_matches(details["away_name"], event.get("home_name")):
                matches.append((event, True))
        return matches[0] if len(matches) == 1 else None

    def suggest(self, play: dict) -> dict | None:
        results, notes = [], []
        for leg in play["leg_records"]:
            details = dict(leg.get("details") or {})
            found = self.find_event(details, play["created_at"])
            if found is None:
                results.append(None)
                continue
            event, flipped = found
            if flipped and details.get("side") in {"home", "away"}:
                details["side"] = "away" if details["side"] == "home" else "home"
            result = grade_leg(details, event)
            results.append(result)
            if result:
                notes.append(
                    f"Leg {leg['leg_number']}: {leg['selection']} → **{result.upper()}** "
                    f"({event.get('away_name')} {score_value(event.get('away_score')):g} @ "
                    f"{event.get('home_name')} {score_value(event.get('home_score')):g})"
                )
        result = combine_leg_results(results)
        if result is None:
            return None
        return {"result": result, "notes": notes}

    def mark_suggested(self, play_id: int) -> None:
        self.db.update("plays", {"auto_suggested_at": datetime.now(timezone.utc).isoformat()}, {"id": int(play_id)})


play_features_service = PlayFeaturesService()
