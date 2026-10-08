from src.services.supabase_service import supabase_service


def _rpc(name: str, params: dict):
    return supabase_service._execute(
        lambda: supabase_service._ensure_client().rpc(name, params).execute()
    ).data


def claim(play_id: int, channel_id: int) -> bool:
    # A retry after an uncertain commit would look like someone else's claim.
    result = supabase_service._ensure_client().rpc(
        "claim_play_bang", {"p_play_id": play_id, "p_channel_id": str(channel_id)},
    ).execute().data
    if not isinstance(result, bool):
        raise RuntimeError("BANG notification claim returned an unexpected response.")
    return result


def complete(play_id: int, channel_id: int, message_id: int) -> None:
    if _rpc("complete_play_bang", {
        "p_play_id": play_id, "p_channel_id": str(channel_id), "p_message_id": str(message_id),
    }) is not True:
        raise RuntimeError("BANG notification delivery tracking was not confirmed.")
