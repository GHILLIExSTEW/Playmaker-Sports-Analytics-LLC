from src.services.api_sports_player_service import ApiSportsPlayerService


def test_parse_league_value_requires_autocomplete_value():
    assert ApiSportsPlayerService.parse_league_value("39|2026") == ("39", "2026")

    try:
        ApiSportsPlayerService.parse_league_value("Premier League")
    except ValueError as error:
        assert "suggestions" in str(error)
    else:
        raise AssertionError("Expected a validation error for an unselected league")


def test_provider_player_accepts_nested_player_and_team():
    player = ApiSportsPlayerService._provider_player({
        "player": {"id": 88, "name": "Example Player"},
        "statistics": [{"team": {"name": "Example FC"}}],
    })

    assert player == {"player_id": 88, "name": "Example Player", "team_name": "Example FC"}


def test_player_directory_sync_caches_all_pages(monkeypatch):
    service = ApiSportsPlayerService()
    requests = []
    stored = []

    def provider_request(sport_slug, endpoint, params):
        requests.append((sport_slug, endpoint, params["page"]))
        if params["page"] == 1:
            return {
                "response": [{"player": {"id": 10, "name": "First Player"}}],
                "paging": {"total": 2},
            }
        return {
            "response": [{"player": {"id": 20, "name": "Second Player"}}],
            "paging": {"total": 2},
        }

    monkeypatch.setattr(service, "_request", provider_request)
    monkeypatch.setattr(
        "src.services.api_sports_player_service.supabase_service.upsert",
        lambda table, rows, conflict_columns: stored.extend(rows),
    )

    count = service.sync_player_directory("basketball", "12|2026")

    assert count == 2
    assert requests == [("basketball", "players", 1), ("basketball", "players", 2)]
    assert [row["player_id"] for row in stored] == [10, 20]
    assert all(row["league_id"] == "12" and row["sport_slug"] == "basketball" for row in stored)


def test_record_player_matching_handles_nested_event_payload():
    event_stats = [{
        "team": {"name": "Example FC"},
        "players": [{"player": {"id": 88}, "statistics": [{"minutes": 90}]}],
    }]

    assert ApiSportsPlayerService._record_contains_player(event_stats, 88)
    assert not ApiSportsPlayerService._record_contains_player(event_stats, 99)