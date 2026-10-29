from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from src.capper_ballots import BallotStore
from src.datetime_utils import parse_iso_datetime
from src.services.ballot_vote_service import SLIP_POLICY_VERSION
from src.services.play_service import PlayService
from src.services.supabase_service import supabase_service

EASTERN = ZoneInfo("America/New_York")
ZONES = {
    "et": EASTERN, "est": EASTERN, "edt": EASTERN,
    "ct": ZoneInfo("America/Chicago"), "cst": ZoneInfo("America/Chicago"), "cdt": ZoneInfo("America/Chicago"),
    "mt": ZoneInfo("America/Denver"), "mst": ZoneInfo("America/Denver"), "mdt": ZoneInfo("America/Denver"),
    "pt": ZoneInfo("America/Los_Angeles"), "pst": ZoneInfo("America/Los_Angeles"),
    "pdt": ZoneInfo("America/Los_Angeles"), "utc": timezone.utc, "gmt": timezone.utc,
}
MONTHS = {name: number for number, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
TIME_HELP = "Try a time like 7:30 PM, tonight 8pm, tomorrow 1pm, or Oct 22 7:30 PM (Eastern Time unless you add CT/PT/etc.)."


def review_time(value: str) -> datetime:
    try:
        result = parse_iso_datetime(value.strip())
    except ValueError as exc:
        raise ValueError("Use YYYY-MM-DDTHH:MM with a timezone offset, for example 2026-10-22T19:00-04:00.") from exc
    if result.tzinfo is None:
        raise ValueError("Include a timezone offset in both the event start and voting cutoff.")
    return result.astimezone(timezone.utc)


def friendly_time(value: datetime) -> str:
    local = value.astimezone(EASTERN)
    hour = local.hour % 12 or 12
    return f"{local:%b} {local.day}, {hour}:{local:%M} {'AM' if local.hour < 12 else 'PM'} ET"


def _parse_clock(text: str, now: datetime, base_day: date | None) -> datetime:
    """Parse a casual date/time; dates default to base_day, or to the next occurrence of the time."""
    raw = text.strip().lower().replace(",", " ")
    if not raw:
        raise ValueError(f"Enter a time. {TIME_HELP}")
    zone = EASTERN
    zone_match = re.search(r"\b(" + "|".join(ZONES) + r")\b\.?\s*$", raw)
    if zone_match:
        zone = ZONES[zone_match.group(1)]
        raw = raw[:zone_match.start()].strip()
    local_now = now.astimezone(zone)
    day, explicit_day = base_day or local_now.date(), False

    def take(pattern: str) -> re.Match | None:
        nonlocal raw
        match = re.search(pattern, raw)
        if match:
            raw = f"{raw[:match.start()]} {raw[match.end():]}"
        return match

    if match := take(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"):
        day, explicit_day = date(*(int(part) for part in match.groups())), True
    elif match := take(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b"):
        year = int(match.group(3)) if match.group(3) else local_now.year
        day, explicit_day = date(year + 2000 if year < 100 else year, int(match.group(1)), int(match.group(2))), True
        if not match.group(3) and day < local_now.date() - timedelta(days=1):
            day = day.replace(year=day.year + 1)
    elif match := take(r"\b(" + "|".join(MONTHS) + r")[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s+(\d{4}))?\b"):
        year = int(match.group(3)) if match.group(3) else local_now.year
        day, explicit_day = date(year, MONTHS[match.group(1)], int(match.group(2))), True
        if not match.group(3) and day < local_now.date() - timedelta(days=1):
            day = day.replace(year=day.year + 1)
    elif take(r"\b(today|tonight)\b"):
        day, explicit_day = local_now.date(), True
    elif take(r"\b(tomorrow|tmrw|tmw)\b"):
        day, explicit_day = local_now.date() + timedelta(days=1), True
    elif match := take(r"\b(" + "|".join(WEEKDAYS) + r")[a-z]*\b"):
        ahead = (WEEKDAYS.index(match.group(1)) - local_now.weekday()) % 7
        day, explicit_day = local_now.date() + timedelta(days=ahead), True

    match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(a|p)\.?m?\.?(?!\w)", raw)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2) or 0)
        if not 1 <= hour <= 12:
            raise ValueError(f"'{text.strip()}' is not a valid time. {TIME_HELP}")
        hour = hour % 12 + (12 if match.group(3) == "p" else 0)
    elif match := re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", raw):
        hour, minute = int(match.group(1)), int(match.group(2))
    elif re.search(r"\b\d{1,2}\b", raw):
        raise ValueError(f"Add AM or PM to '{text.strip()}', for example 7:30 PM.")
    else:
        raise ValueError(f"Couldn't understand '{text.strip()}'. {TIME_HELP}")
    if minute > 59:
        raise ValueError(f"'{text.strip()}' is not a valid time. {TIME_HELP}")
    try:
        result = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)
    except ValueError as exc:
        raise ValueError(f"'{text.strip()}' is not a valid date. {TIME_HELP}") from exc
    if not explicit_day and base_day is None and result <= now:
        result += timedelta(days=1)
    return result.astimezone(timezone.utc)


def parse_event_start(text: str, now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    try:
        return review_time(text)
    except ValueError:
        pass
    try:
        naive = datetime.fromisoformat(text.strip())
        return naive.replace(tzinfo=EASTERN).astimezone(timezone.utc)
    except ValueError:
        return _parse_clock(text, now, None)


def parse_cutoff(text: str, start: datetime, now: datetime | None = None, *, default_minutes: int = 10) -> datetime:
    """Blank means a default lead before start; also accepts '30 min before', '1 hour', or a clock time."""
    now = now or datetime.now(timezone.utc)
    raw = (text or "").strip().lower()
    if not raw:
        return start - timedelta(minutes=default_minutes)
    relative = re.fullmatch(
        r"(\d+(?:\.\d+)?)\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours)(?:\s+(?:before|prior)(?:\s+(?:start|kickoff|tip|first pitch))?)?",
        raw,
    )
    if relative:
        amount = float(relative.group(1))
        return start - (timedelta(hours=amount) if relative.group(2).startswith("h") else timedelta(minutes=amount))
    try:
        return review_time(text)
    except ValueError:
        pass
    local_start = start.astimezone(EASTERN)
    result = _parse_clock(text, now, local_start.date())
    if result >= start and not re.search(r"\d{1,2}/\d|\b(?:today|tonight|tomorrow|tmrw)\b|"
                                         + "|".join(MONTHS), raw):
        result -= timedelta(days=1)
    return result


def confirmed_legs(text: str, previous: list[dict]) -> list[dict]:
    legs = []
    for line in text.splitlines():
        if not line.strip():
            continue
        selection, separator, odds = line.rpartition("|")
        if not separator or not selection.strip():
            raise ValueError("Enter one leg per line as Selection | American odds.")
        try:
            normalized = PlayService.normalize_odds(odds.strip())
        except ValueError:
            raise ValueError(f"Add valid odds for '{selection.strip()}' after the | (for example -110 or +150).") from None
        if normalized == 0:
            raise ValueError(f"Add valid odds for '{selection.strip()}' after the | (for example -110 or +150).")
        old = next((leg for leg in previous if leg["selection"] == selection.strip() and leg["odds"] == normalized), {})
        legs.append({**old, "selection": selection.strip(), "odds": normalized})
    if not 1 <= len(legs) <= 10:
        raise ValueError("Confirm 1-10 legs.")
    return legs


def response_choices(row: dict) -> dict[str, str]:
    choices = {}
    for voter in row.get("eligible_voter_ids") or []:
        response = row["response_states"].get(voter, {})
        if response.get("yes") and response.get("no"):
            continue
        for choice in ("pass", "yes", "no"):
            if response.get(choice):
                choices[voter] = choice
                break
    return choices


class SlipReviewStore(BallotStore):
    @staticmethod
    def rpc(name: str, **params):
        return supabase_service._execute(
            lambda: supabase_service._ensure_client().rpc(name, params).execute(),
        ).data

    def register(self, message_id: int, review: dict) -> bool:
        return self.rpc("register_capper_slip", p_message_id=str(message_id), p_review=review) is True

    def find(self, message_id: int, *, columns: str = "*") -> dict | None:
        rows = supabase_service.select("capper_ballots", columns, {"message_id": str(message_id)}).data or []
        return rows[0] if rows else None

    def known_ids(self, message_ids: list[int]) -> set[int]:
        known: set[int] = set()
        for offset in range(0, len(message_ids), 100):
            batch = [str(value) for value in message_ids[offset:offset + 100]]
            rows = supabase_service._execute(
                lambda: supabase_service._ensure_client().table("capper_ballots")
                .select("message_id").in_("message_id", batch).execute(),
            ).data or []
            known.update(int(row["message_id"]) for row in rows)
        return known

    def claim_invalid(self, message_id: int, fingerprint: str, reason: str, forum_id: str | None) -> bool:
        return self.rpc(
            "claim_invalid_capper_slip", p_message_id=str(message_id),
            p_fingerprint=fingerprint, p_reason=reason, p_forum_id=forum_id,
        ) is True

    def invalid_ids(self, forum_id: int) -> list[int]:
        values: list[int] = []
        offset = 0
        while True:
            batch = supabase_service._execute(
                lambda: supabase_service._ensure_client().table("capper_invalid_slip_work")
                .select("message_id").eq("forum_channel_id", str(forum_id))
                .order("message_id").range(offset, offset + 99).execute(),
            ).data or []
            values.extend(int(row["message_id"]) for row in batch)
            if len(batch) < 100:
                return values
            offset += 100

    def advance_forum(self, scanned_at: datetime) -> None:
        if not supabase_service.update(
            "capper_ballot_scan", {"forum_scanned_at": scanned_at.isoformat()}, {"singleton": True},
        ).data:
            raise RuntimeError("The forum reconciliation cursor was not saved.")

    def retire_invalid(self, message_id: int) -> None:
        if not supabase_service.update("capper_invalid_slips", {"retired": True}, {"message_id": str(message_id)}).data:
            raise RuntimeError("The deleted invalid-post marker was not saved.")

    def pending(self) -> list[dict]:
        return supabase_service.select(
            "capper_ballots", "*", {"state": "pending", "policy_version": SLIP_POLICY_VERSION},
        ).data or []

    def update_review(self, message_id: int, **values) -> None:
        result = supabase_service.update(
            "capper_ballots", values, {"message_id": str(message_id), "state": "pending"},
        )
        if not result.data:
            raise RuntimeError("The slip review update was not saved.")

    def open(self, message_id: int, author_id: int, roster: set[int], parsed: dict, start: datetime, cutoff: datetime) -> None:
        self.rpc(
            "open_capper_slip", p_message_id=str(message_id), p_author_id=str(author_id),
            p_roster=sorted(str(voter) for voter in roster), p_parsed=parsed,
            p_start=start.isoformat(), p_cutoff=cutoff.isoformat(),
        )

    def respond(self, message_id: int, voter_id: int, action: str) -> str:
        status = self.rpc(
            "respond_capper_slip", p_message_id=str(message_id), p_voter_id=str(voter_id), p_action=action,
        )
        if status not in {"saved", "conflict", "closed", "ineligible", "author-confirmed"}:
            raise RuntimeError("Unknown slip response result.")
        return status

    def finalize(self, message_id: int) -> dict | None:
        return self.rpc("finalize_capper_slip", p_message_id=str(message_id))

    def cancel(self, message_id: int, author_id: int) -> bool:
        return self.rpc("cancel_capper_slip", p_message_id=str(message_id), p_author_id=str(author_id)) is True

    def fail(self, message_id: int, reason: str) -> None:
        self.rpc("fail_capper_slip", p_message_id=str(message_id), p_reason=reason)

    def claim_reminder(self, message_id: int) -> bool:
        return bool(supabase_service.update(
            "capper_ballots", {"reminder_sent": True},
            {"message_id": str(message_id), "state": "pending", "reminder_sent": False},
        ).data)
