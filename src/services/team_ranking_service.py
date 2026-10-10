from __future__ import annotations

from src.services.supabase_service import supabase_service


class TeamRankingService:
    @staticmethod
    def build_rankings(rows: list[dict]) -> list[dict]:
        grouped: dict[tuple[str, object], dict] = {}
        for row in rows:
            team_id = row.get("team_id")
            team_name = " ".join(str(row.get("team_name") or "").strip().split())
            if team_id is None and not team_name:
                continue
            if row.get("status") not in {"win", "loss", "void", "partial"}:
                continue

            key = ("id", int(team_id)) if team_id is not None else ("name", team_name.lower())
            bucket = grouped.setdefault(
                key,
                {
                    "team_id": int(team_id) if team_id is not None else None,
                    "team_name": team_name or None,
                    "wins": 0,
                    "losses": 0,
                    "voids": 0,
                    "partials": 0,
                    "net_units": 0.0,
                },
            )

            units = float(row.get("units") or 0)
            status = row.get("status")
            if status == "win":
                bucket["wins"] += 1
                bucket["net_units"] += units
            elif status == "loss":
                bucket["losses"] += 1
                bucket["net_units"] -= units
            elif status == "void":
                bucket["voids"] += 1
            elif status == "partial":
                bucket["partials"] += 1
                bucket["net_units"] += units * 0.5

        result = list(grouped.values())
        result.sort(key=lambda item: (-item["net_units"], -item["wins"], item.get("team_name") or str(item.get("team_id"))))
        return result

    @staticmethod
    def fetch_rankings_from_supabase() -> list[dict]:
        response = supabase_service.select("plays", "team_id, team_name, status, units")
        rows = response.data or []
        return TeamRankingService.build_rankings(rows)
