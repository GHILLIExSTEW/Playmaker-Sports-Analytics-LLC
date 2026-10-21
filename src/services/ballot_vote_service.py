from __future__ import annotations

import re

from src.services.supabase_service import supabase_service

POLICY_VERSION = "roster-v1"


def ballot_rating(yes: int, no: int, roster_size: int) -> tuple[int, int] | None:
    if roster_size not in {3, 4, 5}:
        raise ValueError("Official ballots require a verified roster of 3-5 cappers.")
    if min(yes, no) < 0 or yes + no > roster_size:
        raise ValueError("Vote counts exceed the ballot's eligible roster.")
    if yes <= roster_size // 2:
        return None
    units = yes if roster_size == 3 else yes - roster_size + 3
    return units + 2, units


def infer_vote(text: str) -> str | None:
    """Recognize explicit statements only; confirmation supplies the final authority."""
    text = re.sub(r"https://(?:www\.)?discord(?:app)?\.com/channels/\d+/\d+/\d+", "", text)
    text = re.sub(r"<[@#][!&]?\d+>", "", text).strip().casefold()
    text = text.replace("\u2019", "'")
    text = re.sub(r"[.!]+$", "", text).strip()
    patterns = {
        "yes": r"(?:yes|agreed|agree|i agree|i'm good with that|im good with that|i am good with that|"
               r"i like (?:that|it)|(?:i would like|i'?m on|i am on) "
               r"(?:(?:that|this|it)|(?:the )?(?:over|under)\s*\d+(?:\.\d+)?|[ou]\d+(?:\.\d+)?|"
               r".+ (?:ml|moneyline|[+-]\d+(?:\.\d+)?)))",
        "no": r"(?:no|disagree|i disagree|i'?m not good(?: with that)?|i am not good(?: with that)?|i don'?t like (?:that|it))",
        "pass": r"(?:pass|i pass|i'?ll pass|i will pass|i'?m passing|i am passing)",
    }
    return next((vote for vote, pattern in patterns.items() if re.fullmatch(pattern, text)), None)


def statement_matches_ballot(text: str, question: str) -> bool:
    text = re.sub(r"https://(?:www\.)?discord(?:app)?\.com/channels/\d+/\d+/\d+", "", text)
    match = re.match(r"^(?:i would like|i'?m on|i am on)\s+(.+)", text.strip().casefold().replace("\u2019", "'"))
    if match is None or match[1].rstrip(".! ") in {"that", "this", "it"}:
        return True

    def tokens(value: str) -> set[str]:
        value = re.sub(r"\bo(?=\d)", "over ", value.casefold())
        value = re.sub(r"\bu(?=\d)", "under ", value)
        return set(re.findall(r"[a-z]+|\d+(?:\.\d+)?", value)) - {"the"}

    return tokens(match[1]) <= tokens(question)


def approval_text(approval: dict | None, units: float) -> str | None:
    if not approval:
        return None
    return (
        f"Team approval: {approval['yes']} of {approval['roster_size']} cappers"
        f" | Official stake: {float(units):g}u"
    )


class ChatVoteStore:
    def requests(self, ballot_id: int) -> list[dict]:
        return supabase_service.select(
            "capper_chat_votes", "*", {"ballot_message_id": str(ballot_id)},
        ).data or []

    def get(self, source_id: str) -> dict:
        rows = supabase_service.select("capper_chat_votes", "*", {"source_message_id": source_id}).data or []
        if not rows:
            raise ValueError("This vote verification request no longer exists.")
        return rows[0]

    def remember(self, payload: dict) -> None:
        supabase_service._execute(lambda: supabase_service._ensure_client().table("capper_chat_votes").upsert(
            payload, on_conflict="ballot_message_id,voter_id", ignore_duplicates=True,
        ).execute())

    def update(self, source_id: str, **values) -> None:
        result = supabase_service.update("capper_chat_votes", values, {"source_message_id": source_id})
        if not result.data:
            raise RuntimeError("Chat vote verification state was not saved.")

    def approve(self, source_id: str, actor_id: str, reject: bool) -> str:
        result = supabase_service._execute(lambda: supabase_service._ensure_client().rpc(
            "verify_capper_chat_vote",
            {"p_source_id": source_id, "p_actor_id": actor_id, "p_reject": reject},
        ).execute())
        if result.data not in {"confirmed", "rejected", "awaiting-second-capper"}:
            raise RuntimeError("The database returned an unknown vote verification state.")
        return result.data
