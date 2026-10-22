from __future__ import annotations

from datetime import datetime, timezone

from src.capper_ballots import BallotStore
from src.services.ballot_vote_service import SLIP_POLICY_VERSION
from src.services.play_service import PlayService
from src.services.supabase_service import supabase_service


def review_time(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Use YYYY-MM-DDTHH:MM with a timezone offset, for example 2026-10-22T19:00-04:00.") from exc
    if result.tzinfo is None:
        raise ValueError("Include a timezone offset in both the event start and voting cutoff.")
    return result.astimezone(timezone.utc)


def confirmed_legs(text: str, previous: list[dict]) -> list[dict]:
    legs = []
    for line in text.splitlines():
        if not line.strip():
            continue
        selection, separator, odds = line.rpartition("|")
        if not separator or not selection.strip():
            raise ValueError("Enter one leg per line as Selection | American odds.")
        normalized = PlayService.normalize_odds(odds.strip())
        if normalized == 0:
            raise ValueError("Every leg must have nonzero odds.")
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
