from datetime import datetime, timezone

import pytest

from src.services.nflverse_service import (
    PBP_REQUIRED_COLUMNS,
    NflverseService,
    build_team_game_efficiency,
)

SYNCED_AT = datetime(2026, 10, 11, 17, tzinfo=timezone.utc)


class FakeFrame:
    columns = sorted(PBP_REQUIRED_COLUMNS)

    def __init__(self, rows):
        self.rows = rows

    def iter_rows(self, *, named=False):
        assert named
        return iter(self.rows)


def play(**overrides):
    row = {
        "game_id": "2025_01_ARI_NO",
        "game_date": "2025-09-07",
        "season": 2025,
        "season_type": "REG",
        "posteam": "ARI",
        "defteam": "NO",
        "play_type": "pass",
        "epa": 1.5,
        "success": 1.0,
    }
    return {**row, **overrides}


def test_build_team_game_efficiency_aggregates_pass_run_and_defense():
    frame = FakeFrame([
        play(),
        play(play_type="run", epa=-0.5, success=0.0),
        play(play_type="kickoff", epa=10.0),
        play(season_type="POST", epa=20.0),
        play(epa=None),
        play(posteam="NO", defteam="ARI", epa=2.0, success=None),
    ])

    rows = build_team_game_efficiency(frame, SYNCED_AT)

    arizona, new_orleans = sorted(rows, key=lambda row: str(row["team_code"]))
    assert arizona["team_code"] == "ARI"
    assert arizona["offense_epa_sum"] == 1.0
    assert arizona["offense_plays"] == 2
    assert arizona["offense_successes"] == 1
    assert arizona["offense_success_plays"] == 2
    assert arizona["defense_epa_sum"] == 2.0
    assert arizona["defense_plays"] == 1
    assert arizona["defense_success_plays"] == 0
    assert new_orleans["defense_epa_sum"] == 1.0
    assert new_orleans["defense_plays"] == 2
    assert new_orleans["defense_successes_allowed"] == 1
    assert new_orleans["synced_at"] == SYNCED_AT.isoformat()


def test_missing_provider_columns_and_empty_regular_season_are_explicit():
    frame = FakeFrame([play()])
    frame.columns = ["game_id"]
    with pytest.raises(RuntimeError, match="missing columns"):
        build_team_game_efficiency(frame, SYNCED_AT)

    with pytest.raises(RuntimeError, match="no regular-season"):
        build_team_game_efficiency(FakeFrame([play(season_type="POST")]), SYNCED_AT)


def test_service_loads_current_and_prior_seasons_and_upserts_rows(monkeypatch):
    calls = []
    stored = []
    frame = FakeFrame([play()])

    class Query:
        def upsert(self, rows, on_conflict):
            assert on_conflict == "season,game_id,team_code"
            stored.extend(rows)
            return self

        def execute(self):
            return None

    class Client:
        def table(self, table):
            assert table == "nflverse_team_game_efficiency"
            return Query()

    service = NflverseService(
        load_pbp=lambda seasons: calls.append(seasons) or frame,
        client=Client(),
        clock=lambda: SYNCED_AT,
    )
    monkeypatch.setattr(
        "src.services.nflverse_service.supabase_service._execute",
        lambda operation: operation(),
    )

    result = service.sync_team_game_efficiency()

    assert calls == [[2025, 2026]]
    assert len(stored) == 2
    assert result == {"seasons": 2, "team_games": 2, "batches": 1}
