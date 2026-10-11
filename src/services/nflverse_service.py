from __future__ import annotations

import math
from datetime import date, datetime, timezone
from threading import Lock
from typing import Callable, Iterator, Protocol
from zoneinfo import ZoneInfo

from src.services.supabase_service import supabase_service

PBP_REQUIRED_COLUMNS = {
    "game_id", "game_date", "season", "season_type", "posteam", "defteam",
    "play_type", "epa", "success",
}
UPSERT_BATCH_SIZE = 500
NFL_TIMEZONE = ZoneInfo("America/New_York")


class PlayByPlayFrame(Protocol):
    @property
    def columns(self) -> list[str]: ...

    def iter_rows(self, *, named: bool = False) -> Iterator[dict[str, object]]: ...


def _load_pbp(seasons: list[int]) -> PlayByPlayFrame:
    import nflreadpy as nfl

    return nfl.load_pbp(seasons=seasons)


def _game_date(value: object) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        return date.fromisoformat(value[:10]).isoformat()
    raise ValueError(f"nflverse returned an invalid game date: {value!r}")


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _success(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    return None


def build_team_game_efficiency(
    frame: PlayByPlayFrame, synced_at: datetime,
) -> list[dict[str, object]]:
    missing = PBP_REQUIRED_COLUMNS.difference(frame.columns)
    if missing:
        raise RuntimeError(f"nflverse play-by-play is missing columns: {sorted(missing)}")

    accumulated: dict[tuple[int, str, str], dict[str, object]] = {}
    for play in frame.iter_rows(named=True):
        if str(play["season_type"]).upper() != "REG":
            continue
        if str(play["play_type"]).lower() not in {"pass", "run"}:
            continue
        epa = _number(play["epa"])
        offense = play["posteam"]
        defense = play["defteam"]
        if epa is None or not isinstance(offense, str) or not offense.strip():
            continue
        if not isinstance(defense, str) or not defense.strip():
            continue

        season = int(play["season"])
        game_id = str(play["game_id"])
        game_date = _game_date(play["game_date"])
        successful = _success(play["success"])
        for team, is_offense in ((offense, True), (defense, False)):
            key = (season, game_id, team.upper())
            stats = accumulated.setdefault(key, {
                "season": season,
                "game_id": game_id,
                "game_date": game_date,
                "team_code": team.upper(),
                "offense_epa_sum": 0.0,
                "offense_plays": 0,
                "offense_successes": 0,
                "offense_success_plays": 0,
                "defense_epa_sum": 0.0,
                "defense_plays": 0,
                "defense_successes_allowed": 0,
                "defense_success_plays": 0,
                "synced_at": synced_at.isoformat(),
            })
            prefix = "offense" if is_offense else "defense"
            stats[f"{prefix}_epa_sum"] = float(stats[f"{prefix}_epa_sum"]) + epa
            stats[f"{prefix}_plays"] = int(stats[f"{prefix}_plays"]) + 1
            if successful is not None:
                success_key = "offense_successes" if is_offense else "defense_successes_allowed"
                stats[success_key] = int(stats[success_key]) + int(successful)
                stats[f"{prefix}_success_plays"] = int(stats[f"{prefix}_success_plays"]) + 1

    rows = list(accumulated.values())
    if not rows:
        raise RuntimeError("nflverse returned no regular-season pass or run plays.")
    return rows


class NflverseService:
    def __init__(
        self, *, load_pbp: Callable[[list[int]], PlayByPlayFrame] | None = None,
        client=None, clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.load_pbp = load_pbp or _load_pbp
        self.client = client
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._sync_lock = Lock()

    def _client(self):
        return self.client or supabase_service._ensure_client()

    def sync_team_game_efficiency(self) -> dict[str, int]:
        with self._sync_lock:
            today = self.clock().astimezone(NFL_TIMEZONE).date()
            current_season = today.year if today.month >= 8 else today.year - 1
            frame = self.load_pbp([current_season - 1, current_season])
            rows = build_team_game_efficiency(frame, self.clock())
            client = self._client()
            for offset in range(0, len(rows), UPSERT_BATCH_SIZE):
                batch = rows[offset:offset + UPSERT_BATCH_SIZE]
                supabase_service._execute(
                    lambda batch=batch: client.table("nflverse_team_game_efficiency")
                    .upsert(batch, on_conflict="season,game_id,team_code").execute()
                )
            return {
                "seasons": 2,
                "team_games": len(rows),
                "batches": (len(rows) + UPSERT_BATCH_SIZE - 1) // UPSERT_BATCH_SIZE,
            }


nflverse_service = NflverseService()
