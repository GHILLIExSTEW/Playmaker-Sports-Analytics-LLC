from __future__ import annotations

from src.services.supabase_service import supabase_service
from src.config import WHOP_ACCOUNT_ID, WHOP_PAID_PLAN_IDS, WHOP_TRIAL_PLAN_IDS


class MembershipService:
    """Read paid, trial, owner, or OPERATOR team access without fabricating payment."""

    def __init__(self, database=None) -> None:
        self.db = database or supabase_service

    def has_paid_access(self, discord_user_id: int) -> bool:
        result = self.db._ensure_client().rpc(
            "discord_has_paid_access", {"p_discord_user_id": str(discord_user_id)},
        ).execute().data
        if not isinstance(result, bool):
            raise RuntimeError("Membership lookup returned an invalid access response.")
        return result

    def ready(self) -> None:
        # Fails at startup if the access migration is missing; no role-based fallback.
        self.has_paid_access(0)
        if WHOP_TRIAL_PLAN_IDS:
            self.has_trial_access(0)

    def grandfather_captured(self, guild_id: int, role_id: int) -> bool:
        rows = self.db.select(
            "highroller_grandfather_captures", "guild_id",
            {"guild_id": str(guild_id), "role_id": str(role_id)},
        ).data
        if not isinstance(rows, list):
            raise RuntimeError("Grandfather capture lookup returned an invalid response.")
        return bool(rows)

    def capture_grandfather(self, guild_id: int, role_id: int, member_ids: set[int]) -> bool:
        if not WHOP_ACCOUNT_ID:
            raise RuntimeError("Configure the Whop seller before capturing grandfather access.")
        result = self.db._ensure_client().rpc(
            "capture_highroller_grandfather", {
                "p_guild_id": str(guild_id), "p_role_id": str(role_id),
                "p_account_id": WHOP_ACCOUNT_ID,
                "p_member_ids": sorted(str(member_id) for member_id in member_ids),
            },
        ).execute().data
        if not isinstance(result, bool):
            raise RuntimeError("Grandfather capture returned an invalid response.")
        return result

    def has_trial_access(self, discord_user_id: int) -> bool:
        if not WHOP_TRIAL_PLAN_IDS:
            return False
        if not WHOP_ACCOUNT_ID:
            raise RuntimeError("Configure a seller and approved trial-capable plans.")
        result = self.db._ensure_client().rpc(
            "discord_has_trial_access", {
                "p_discord_user_id": str(discord_user_id),
                "p_account_id": WHOP_ACCOUNT_ID,
                "p_plan_ids": sorted(WHOP_TRIAL_PLAN_IDS),
            },
        ).execute().data
        if not isinstance(result, bool):
            raise RuntimeError("Trial lookup returned an invalid access response.")
        return result

    def highroller_role_entitlements(self) -> tuple[set[int], set[int]]:
        if not WHOP_ACCOUNT_ID or not WHOP_PAID_PLAN_IDS:
            raise RuntimeError("Configure the Whop seller and paid plans before syncing HIGHROLLER.")
        rows = self.db._ensure_client().rpc(
            "highroller_role_entitlements", {
                "p_account_id": WHOP_ACCOUNT_ID,
                "p_paid_plan_ids": sorted(WHOP_PAID_PLAN_IDS),
                "p_trial_plan_ids": sorted(WHOP_TRIAL_PLAN_IDS),
            },
        ).execute().data
        if not isinstance(rows, list) or any(
            not isinstance(row, dict) or not str(row.get("discord_user_id", "")).isdigit()
            or row.get("source") not in {"whop", "grandfather"} for row in rows
        ):
            raise RuntimeError("HIGHROLLER role lookup returned invalid entitlements.")
        return (
            {int(row["discord_user_id"]) for row in rows if row["source"] == "whop"},
            {int(row["discord_user_id"]) for row in rows if row["source"] == "grandfather"},
        )

    def has_vault_access(self, discord_user_id: int) -> bool:
        return self.has_paid_access(discord_user_id) or self.has_trial_access(discord_user_id)

    def has_stats_access(self, discord_user_id: int) -> bool:
        if not WHOP_ACCOUNT_ID or not WHOP_PAID_PLAN_IDS:
            raise RuntimeError("Configure approved Whop paid plans and seller before using stats commands.")
        result = self.db._ensure_client().rpc(
            "discord_has_paid_plan_access", {
                "p_discord_user_id": str(discord_user_id),
                "p_account_id": WHOP_ACCOUNT_ID,
                "p_plan_ids": sorted(WHOP_PAID_PLAN_IDS),
            },
        ).execute().data
        if not isinstance(result, bool):
            raise RuntimeError("Stats membership lookup returned an invalid access response.")
        return result or self.has_trial_access(discord_user_id)

    def paid_discord_ids(self) -> set[int]:
        rows = self.db._ensure_client().rpc("paid_discord_member_ids").execute().data
        if not isinstance(rows, list) or any(
            not isinstance(row, dict) or not str(row.get("discord_user_id", "")).isdigit()
            for row in rows
        ):
            raise RuntimeError("Membership lookup returned invalid Discord identities.")
        return {int(row["discord_user_id"]) for row in rows}

    def member_discord_ids(self) -> set[int]:
        ids = self.paid_discord_ids()
        if not WHOP_TRIAL_PLAN_IDS:
            return ids
        if not WHOP_ACCOUNT_ID:
            raise RuntimeError("Configure a seller and approved trial-capable plans.")
        rows = self.db._ensure_client().rpc(
            "trial_discord_member_ids", {
                "p_account_id": WHOP_ACCOUNT_ID, "p_plan_ids": sorted(WHOP_TRIAL_PLAN_IDS),
            },
        ).execute().data
        if not isinstance(rows, list) or any(
            not isinstance(row, dict) or not str(row.get("discord_user_id", "")).isdigit()
            for row in rows
        ):
            raise RuntimeError("Trial lookup returned invalid Discord identities.")
        return ids | {int(row["discord_user_id"]) for row in rows}
