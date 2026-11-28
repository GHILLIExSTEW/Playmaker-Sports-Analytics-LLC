from __future__ import annotations

from datetime import date, datetime, timezone

from src.services.supabase_service import supabase_service


class CapperTrackingSettingsService:
    def _user_id(self, discord_user_id: int) -> str:
        rows = supabase_service.select(
            "users", "id", {"discord_user_id": str(discord_user_id)},
        ).data or []
        if len(rows) != 1:
            raise RuntimeError("The selected capper does not have a unique tracked user record.")
        return str(rows[0]["id"])

    def get_start(self, discord_user_id: int) -> date | None:
        user_id = self._user_id(discord_user_id)
        rows = supabase_service.select(
            "capper_tracking_settings", "starts_at", {"user_id": user_id},
        ).data or []
        return date.fromisoformat(str(rows[0]["starts_at"])[:10]) if rows else None

    def set_start(self, discord_user_id: int, starts_at: date | None) -> None:
        user_id = self._user_id(discord_user_id)
        if starts_at is None:
            supabase_service.delete("capper_tracking_settings", {"user_id": user_id})
            if self.get_start(discord_user_id) is not None:
                raise RuntimeError("The capper tracking start date was not cleared.")
            return
        supabase_service.upsert(
            "capper_tracking_settings",
            {
                "user_id": user_id,
                "starts_at": starts_at.isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            ["user_id"],
        )
        if self.get_start(discord_user_id) != starts_at:
            raise RuntimeError("The capper tracking start date was not saved.")

    def get_all_starts(self) -> dict[str, date]:
        settings = supabase_service.select(
            "capper_tracking_settings", "user_id,starts_at",
        ).data or []
        if not settings:
            return {}
        user_ids = [str(setting["user_id"]) for setting in settings]
        users = supabase_service._execute(
            lambda: supabase_service._ensure_client().table("users")
            .select("id,discord_user_id").in_("id", user_ids).execute()
        ).data or []
        discord_ids = {
            str(user["id"]): str(user["discord_user_id"])
            for user in users
            if user.get("discord_user_id") is not None
        }
        starts = {}
        for setting in settings:
            discord_user_id = discord_ids.get(str(setting["user_id"]))
            if discord_user_id is None:
                raise RuntimeError("A capper tracking setting has no matching user record.")
            starts[discord_user_id] = date.fromisoformat(str(setting["starts_at"])[:10])
        return starts


capper_tracking_settings = CapperTrackingSettingsService()
