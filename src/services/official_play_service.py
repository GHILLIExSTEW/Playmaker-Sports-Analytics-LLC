from __future__ import annotations

from datetime import datetime, timezone

from src.services.play_service import PlayService
from src.services.settlement_service import SettlementService
from src.services.supabase_service import supabase_service


class OfficialPlayService:
    def __init__(self) -> None:
        self.play_service = PlayService()
        self.settlement_service = SettlementService()

    def _ensure_default_sport(self) -> int:
        result = supabase_service.select("sports", "id", {"api_slug": "official"})
        if result.data:
            return int(result.data[0]["id"])

        inserted = supabase_service.insert("sports", {"name": "Official", "api_slug": "official", "is_active": True})
        return int(inserted.data[0]["id"])

    def _ensure_user(self, discord_user_id: str, username: str) -> int:
        existing = supabase_service.select("users", "id", {"discord_user_id": discord_user_id})
        if existing.data:
            return int(existing.data[0]["id"])

        inserted = supabase_service.insert("users", {
            "discord_user_id": discord_user_id,
            "username": username,
            "display_name": username,
            "role": "official",
            "is_active": True,
        })
        return int(inserted.data[0]["id"])

    def save_draft_leg(self, draft_id: str, discord_user_id: str, units: float, expected_legs: int, leg_number: int, selection: str, odds: int, team_name: str | None = None) -> None:
        supabase_service.upsert("play_draft_legs", {
            "draft_id": draft_id,
            "discord_user_id": discord_user_id,
            "units": units,
            "expected_legs": expected_legs,
            "leg_number": leg_number,
            "selection": selection,
            "odds": odds,
            "team_name": team_name or None,
        }, ["draft_id", "leg_number"])

    def get_draft_legs(self, draft_id: str, discord_user_id: str) -> list[dict]:
        result = supabase_service.select("play_draft_legs", "*", {
            "draft_id": draft_id,
            "discord_user_id": discord_user_id,
        })
        return sorted(result.data or [], key=lambda item: item["leg_number"])

    def clear_draft_legs(self, draft_id: str, discord_user_id: str) -> None:
        supabase_service.delete("play_draft_legs", {
            "draft_id": draft_id,
            "discord_user_id": discord_user_id,
        })

    def create_play_record(
        self,
        discord_user_id: str,
        username: str,
        units: float,
        legs: int,
        odds: str,
        play_text: str = "",
        team_name: str | None = None,
        leg_records: list[dict] | None = None,
    ) -> dict:
        validation_error = self.play_service.validate_play(units, legs, odds)
        if validation_error:
            return {"error": validation_error["message"]}

        try:
            odds_value = self.play_service.normalize_odds(odds)
        except ValueError as exc:
            return {"error": str(exc)}

        to_win = self.play_service.calculate_to_win(float(units), odds_value)
        sport_id = self._ensure_default_sport()
        user_id = self._ensure_user(discord_user_id, username)

        payload = {
            "user_id": user_id,
            "team_id": None,
            "sport_id": sport_id,
            "league_id": None,
            "team_name": " ".join((team_name or "").strip().split()) or None,
            "units": float(units),
            "legs": int(legs),
            "odds": odds_value,
            "status": "open",
            "play_text": play_text or "",
            "message_id": None,
        }

        inserted = supabase_service.insert("plays", payload)
        play_id = int(inserted.data[0]["id"])
        if leg_records:
            supabase_service.insert("play_legs", [
                {
                    "play_id": play_id,
                    "leg_number": index,
                    "selection": leg["selection"],
                    "odds": int(leg["odds"]),
                }
                for index, leg in enumerate(leg_records, start=1)
            ])

        return {
            "play_id": play_id,
            "units": float(units),
            "legs": int(legs),
            "odds": odds_value,
            "odds_text": str(odds_value),
            "to_win": to_win,
            "user_name": username,
            "team_name": payload["team_name"],
            "play_text": play_text or "",
            "summary": f"{float(units):g}u • {int(legs)}-leg • {odds_value:+d}",
        }

    def attach_message_id(self, play_id: int, message_id: int) -> dict:
        return supabase_service.update("plays", {"message_id": str(message_id)}, {"id": int(play_id)})

    def edit_play_record(self, play_id: int, units: float, team_name: str, selections: list[str], odds: list[int], note: str = "") -> dict:
        if len(selections) != len(odds) or not selections:
            raise ValueError("Selections and odds must contain the same number of legs.")
        combined_odds = self.play_service.combine_american_odds(odds)
        validation_error = self.play_service.validate_play(units, len(selections), combined_odds)
        if validation_error:
            raise ValueError(validation_error["message"])
        supabase_service.update("plays", {
            "units": float(units),
            "legs": len(selections),
            "odds": combined_odds,
            "team_name": " ".join((team_name or "").strip().split()) or None,
            "play_text": note or "\n".join(
                f"Leg {index}: {selection} ({leg_odds:+d})"
                for index, (selection, leg_odds) in enumerate(zip(selections, odds), start=1)
            ),
        }, {"id": int(play_id)})
        supabase_service.delete("play_legs", {"play_id": int(play_id)})
        supabase_service.insert("play_legs", [
            {"play_id": int(play_id), "leg_number": index, "selection": selection, "odds": int(leg_odds)}
            for index, (selection, leg_odds) in enumerate(zip(selections, odds), start=1)
        ])
        return {"play_id": play_id, "units": float(units), "legs": len(selections), "odds": combined_odds, "team_name": team_name}

    def get_play_legs(self, play_id: int) -> list[dict]:
        result = supabase_service.select("play_legs", "leg_number,selection,odds", {"play_id": int(play_id)})
        return sorted(result.data or [], key=lambda leg: leg["leg_number"])

    def get_play_for_message(self, message_id: int) -> dict | None:
        result = supabase_service.select("plays", "*", {"message_id": str(message_id)})
        if not result.data:
            return None
        play = result.data[0]
        user = supabase_service.select("users", "discord_user_id", {"id": play["user_id"]})
        play["discord_user_id"] = user.data[0]["discord_user_id"] if user.data else None
        return play

    def settle_play(self, play_id: int, result: str) -> dict:
        if result not in {"win", "loss", "void", "partial", "regraded"}:
            raise ValueError(f"Unsupported result: {result}")

        play = self._fetch_play(play_id)
        tally = self.settlement_service.tally_for_result(result, float(play["units"]), play.get("odds"))
        supabase_service.update(
            "plays",
            {"status": result, "settled_at": datetime.now(timezone.utc).isoformat()},
            {"id": int(play_id)},
        )
        return {"result": result, "tally": tally}

    def regrade_play(self, play_id: int, legs_left: int, odds: int, note: str = "") -> dict:
        validated = self.settlement_service.validate_regrade(legs_left, odds)
        supabase_service.update(
            "plays",
            {
                "status": "regraded",
                "legs": validated["legs_left"],
                "odds": validated["odds"],
                "play_text": note,
            },
            {"id": int(play_id)},
        )
        return {"result": "regraded", **validated}

    def list_plays(
        self,
        page: int = 0,
        page_size: int = 10,
        statuses: list[str] | None = None,
        since: datetime | None = None,
    ) -> tuple[list[dict], bool]:
        """Return one newest-first page of plays and whether another page exists."""
        client = supabase_service._ensure_client()
        start = max(0, int(page)) * page_size

        def operation():
            query = client.table("plays").select("id,user_id,units,legs,odds,status,team_name,play_text,created_at")
            if statuses:
                query = query.in_("status", statuses)
            if since is not None:
                query = query.gte("created_at", since.isoformat())
            # Fetch one extra row to learn whether a next page exists.
            return query.order("created_at", desc=True).order("id", desc=True).range(start, start + page_size).execute()

        rows = supabase_service._execute(operation).data or []
        plays, has_more = rows[:page_size], len(rows) > page_size

        user_ids = sorted({play["user_id"] for play in plays if play.get("user_id") is not None})
        names = {}
        if user_ids:
            users = supabase_service._execute(lambda: client.table("users").select(
                "id,display_name,username"
            ).in_("id", user_ids).execute()).data or []
            names = {user["id"]: user.get("display_name") or user.get("username") or "" for user in users}
        for play in plays:
            play["user_name"] = names.get(play.get("user_id"), "")
        return plays, has_more

    @staticmethod
    def open_play_label(play: dict) -> str:
        try:
            odds = f"{int(play.get('odds')):+d}"
        except (TypeError, ValueError):
            odds = str(play.get("odds") or "?")
        parts = [f"#{play['id']}"]
        if play.get("status") and play["status"] != "open":
            parts[0] += f" [{str(play['status']).upper()}]"
        if play.get("user_name"):
            parts.append(str(play["user_name"]))
        parts.append(f"{float(play.get('units') or 0):g}u {play.get('legs') or '?'}-leg {odds}")
        detail = play.get("team_name") or " ".join(str(play.get("play_text") or "").split())
        if detail:
            parts.append(detail)
        label = " • ".join(parts)
        return label if len(label) <= 100 else label[:99] + "…"

    def _fetch_play(self, play_id: int) -> dict:
        result = supabase_service.select("plays", "*", {"id": int(play_id)})
        if not result.data:
            raise ValueError(f"Play {play_id} not found.")
        return result.data[0]


official_play_service = OfficialPlayService()
