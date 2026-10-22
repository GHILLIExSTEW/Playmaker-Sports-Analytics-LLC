from src.services.supabase_service import supabase_service


def _rpc(name: str, params: dict):
    return supabase_service._execute(
        lambda: supabase_service._ensure_client().rpc(name, params).execute()
    ).data


def deliveries(play_id: int) -> list[dict]:
    return supabase_service.select("play_bang_notifications", "*", {"play_id": play_id}).data or []


def recent_plays() -> list[dict]:
    response = supabase_service._execute(
        lambda: supabase_service._ensure_client().table("plays")
        .select("id,created_at,team_name,play_text,status,units,odds,message_id,tracking_scope")
        .eq("tracking_scope", "official").not_.is_("message_id", "null")
        .order("created_at", desc=True).order("id", desc=True).limit(10).execute()
    )
    result = response.data
    if not isinstance(result, list):
        raise RuntimeError("Recent official plays lookup returned an unexpected response.")
    return result


def pending() -> list[dict]:
    return supabase_service._execute(
        lambda: supabase_service._ensure_client().table("play_bang_events")
        .select("play_id,revision").order("updated_at").limit(100).execute()
    ).data or []


def finish_event(play_id: int, revision: str) -> None:
    result = _rpc("finish_play_bang_event", {"p_play_id": play_id, "p_revision": revision})
    if not isinstance(result, bool):
        raise RuntimeError("BANG event acknowledgement returned an unexpected response.")


def claim(play_id: int, channel_id: int, is_test: bool) -> str | None:
    # A retry after an uncertain commit would look like someone else's claim.
    result = supabase_service._ensure_client().rpc(
        "begin_play_bang", {
            "p_play_id": play_id, "p_channel_id": str(channel_id), "p_is_test": is_test,
        },
    ).execute().data
    if result is not None and not isinstance(result, str):
        raise RuntimeError("BANG notification claim returned an unexpected response.")
    return result


def complete(play_id: int, channel_id: int, attempt_id: str, message_id: int, status: str) -> None:
    if _rpc("finish_play_bang", {
        "p_play_id": play_id, "p_channel_id": str(channel_id), "p_attempt_id": attempt_id,
        "p_message_id": str(message_id), "p_status": status,
    }) is not True:
        raise RuntimeError("BANG notification delivery tracking was not confirmed.")


def fail(play_id: int, channel_id: int, attempt_id: str, error: str, uncertain: bool) -> None:
    result = _rpc("fail_play_bang", {
        "p_play_id": play_id, "p_channel_id": str(channel_id), "p_attempt_id": attempt_id,
        "p_error": error[:1500], "p_uncertain": uncertain,
    })
    if not isinstance(result, bool):
        raise RuntimeError("BANG failure tracking returned an unexpected response.")


def recover(play_id: int, channel_id: int, attempt_id: str, actor_id: int, message_id: int | None) -> str:
    # Recovery rotates the token, so an uncertain response must not be retried.
    result = supabase_service._ensure_client().rpc("recover_play_bang", {
        "p_play_id": play_id, "p_channel_id": str(channel_id), "p_attempt_id": attempt_id,
        "p_actor_id": str(actor_id), "p_message_id": str(message_id) if message_id else None,
    }).execute().data
    if not isinstance(result, str):
        raise RuntimeError("BANG recovery returned an unexpected response. Check /bang_status before retrying.")
    return result


def stale(is_test: bool, test_channel_id: int | None = None) -> list[dict]:
    result = _rpc("stale_play_bangs", {
        "p_is_test": is_test, "p_test_channel_id": str(test_channel_id) if test_channel_id else None,
    })
    if not isinstance(result, list):
        raise RuntimeError("BANG correction scan returned an unexpected response.")
    return result
