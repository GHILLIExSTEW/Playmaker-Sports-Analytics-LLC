from datetime import date, datetime, timezone

from src.services.api_sports_service import ApiSportsService, normalize_game, normalize_standing, normalize_team, nfl_season_for_date


def test_nfl_season_rolls_over_in_august():
    assert nfl_season_for_date(date(2026, 7, 31)) == 2025
    assert nfl_season_for_date(date(2026, 8, 1)) == 2026


def test_normalize_game_keeps_schedule_status_and_quarter_scores():
    synced_at = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)
    row = normalize_game({
        "game": {
            "id": 123,
            "stage": "Regular Season",
            "week": "Week 1",
            "date": {"date": "2026-09-10", "time": "00:15", "timestamp": 1788999300},
            "venue": {"name": "Example Stadium", "city": "Example"},
            "status": {"short": "FT", "long": "Finished"},
        },
        "league": {"id": 1, "season": 2026},
        "teams": {
            "home": {"id": 10, "name": "Home Team", "logo": "https://img.example/home.png"},
            "away": {"id": 20, "name": "Away Team", "logo": "https://img.example/away.png"},
        },
        "scores": {"home": {"total": 24, "quarter_1": 7}, "away": {"total": 17, "quarter_1": 3}},
    }, synced_at)

    assert row["game_id"] == 123
    assert row["home_team_name"] == "Home Team"
    assert row["away_score"] == 17
    assert row["status_short"] == "FT"
    assert row["scores"]["home"]["quarter_1"] == 7
    assert row["synced_at"] == synced_at.isoformat()


def test_normalize_standing_maps_records_and_point_differential():
    synced_at = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)
    row = normalize_standing({
        "league": {"id": 1, "season": 2026},
        "team": {"id": 10, "name": "Home Team", "logo": "https://img.example/home.png"},
        "conference": "AFC",
        "division": "North",
        "position": 1,
        "won": 3,
        "lost": 1,
        "ties": 0,
        "points": {"for": 100, "against": 70, "difference": 30},
        "streak": "W2",
        "records": {"home": "2-0"},
    }, synced_at)

    assert row["team_id"] == 10
    assert row["wins"] == 3
    assert row["point_difference"] == 30
    assert row["records"]["home"] == "2-0"


def test_normalize_team_preserves_directory_fields():
    synced_at = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)
    row = normalize_team({"id": 10, "name": "Home Team", "code": "HOM", "city": "Example", "stadium": {"name": "Example Stadium"}, "logo": "https://img.example/home.png"}, synced_at)

    assert row["team_id"] == 10
    assert row["code"] == "HOM"
    assert row["stadium"]["name"] == "Example Stadium"


def test_nfl_model_history_backfills_prior_and_current_season(monkeypatch):
    calls = []
    saved = {}
    stored = []

    def fake_get(_url, params, **_kwargs):
        assert "page" not in params, "The NFL games endpoint rejects the page parameter."
        calls.append(params.copy())
        season = params["season"]

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "errors": {},
                    "response": [{
                        "game": {
                            "id": season, "date": {"date": f"{season}-10-01", "time": "12:00", "timestamp": 1790856000},
                            "status": {"short": "FT", "long": "Finished"},
                        },
                        "league": {"id": 1, "season": season},
                        "teams": {"home": {"id": 1, "name": "Home"}, "away": {"id": 2, "name": "Away"}},
                        "scores": {"home": {"total": 20}, "away": {"total": 10}},
                    }],
                }

        return Response()

    service = ApiSportsService(api_key="test-key", client=object(), get=fake_get,
                               clock=lambda: datetime(2026, 10, 1, tzinfo=timezone.utc))
    monkeypatch.setattr("src.services.api_sports_service.reserve_request", lambda _url: None)
    monkeypatch.setattr(service, "_model_backfill_state", lambda season: saved.get(season))
    monkeypatch.setattr(
        service, "_save_model_backfill_state",
        lambda season, page, total, complete: saved.update({
            season: {"next_page": page, "total_pages": total, "complete": complete},
        }),
    )
    monkeypatch.setattr(service, "_upsert", lambda _table, rows, _conflict: stored.extend(rows))

    result = service.sync_model_history()

    assert [call["season"] for call in calls] == [2025, 2026]
    assert result["total_requests"] == 2
    assert result["seasons"] == {"2025": {"pages": 1, "events": 1}, "2026": {"pages": 1, "events": 1}}
    assert len(stored) == 2
    assert all(saved[season]["complete"] for season in (2025, 2026))