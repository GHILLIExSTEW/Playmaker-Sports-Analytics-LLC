from src.services.supabase_service import supabase_service


def _rpc(name: str, params: dict):
    return supabase_service._execute(
        lambda: supabase_service._ensure_client().rpc(name, params).execute()
    ).data


def insight_context(play_id: int | None = None) -> list[dict]:
    data = _rpc("capper_insight_context", {"p_play_id": play_id})
    if not isinstance(data, list):
        raise RuntimeError("Insight request lookup returned an unexpected response.")
    return data


def save_insight(play_id: int, discord_user_id: int, justification: str) -> None:
    text = justification.strip()
    if not text or len(justification) > 2000:
        raise ValueError("Insight must contain 1 to 2000 characters.")
    if _rpc("save_capper_insight", {
        "p_play_id": play_id, "p_discord_user_id": str(discord_user_id), "p_justification": text,
    }) is not True:
        raise RuntimeError("Insight save was not confirmed.")


def mark_prompt(play_id: int, message_id: int) -> None:
    if _rpc("mark_capper_insight_prompt", {
        "p_play_id": play_id, "p_message_id": str(message_id),
    }) is not True:
        raise RuntimeError("Insight prompt tracking was not confirmed.")
