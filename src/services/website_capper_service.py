from src.services.supabase_service import supabase_service

WEBSITE_OWNER_ROLE_ID = 1347741218158678097


def sync_website_owner_roster(guild_id: int, member_ids: set[int]) -> None:
    if guild_id <= 0 or any(member_id <= 0 for member_id in member_ids):
        raise ValueError("A configured guild and valid Owner identities are required.")
    result = supabase_service._execute(
        lambda: supabase_service._ensure_client().rpc(
            "sync_website_owner_roster",
            {"p_guild_id": str(guild_id), "p_role_id": str(WEBSITE_OWNER_ROLE_ID),
             "p_discord_user_ids": [str(member_id) for member_id in sorted(member_ids)]},
        ).execute()
    )
    if result.data is not True:
        raise RuntimeError("Website Owner roster sync was not confirmed.")


def sync_website_capper_roster(guild_id: int, role_id: int, member_ids: set[int]) -> None:
    if guild_id <= 0 or role_id <= 0 or any(member_id <= 0 for member_id in member_ids):
        raise ValueError("A configured guild, tracker role, and valid Discord identities are required.")
    result = supabase_service._execute(
        lambda: supabase_service._ensure_client().rpc(
            "sync_website_capper_roster",
            {
                "p_guild_id": str(guild_id),
                "p_role_id": str(role_id),
                "p_discord_user_ids": [str(member_id) for member_id in sorted(member_ids)],
            },
        ).execute()
    )
    if result.data is not True:
        raise RuntimeError("Website capper roster sync was not confirmed.")
