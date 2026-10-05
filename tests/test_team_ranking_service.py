from src.services.team_ranking_service import TeamRankingService


ROWS = [
    {"team_id": 1, "sport_id": 1, "league_id": 10, "status": "win", "units": 10},
    {"team_id": 1, "sport_id": 1, "league_id": 10, "status": "loss", "units": 5},
    {"team_id": 1, "sport_id": 1, "league_id": 10, "status": "partial", "units": 4},
    {"team_id": 2, "sport_id": 1, "league_id": 10, "status": "loss", "units": 8},
    {"team_id": 2, "sport_id": 1, "league_id": 10, "status": "void", "units": 6},
]


def test_team_rankings_aggregate_net_units():
    ranking = TeamRankingService().build_rankings(ROWS)
    assert ranking[0]["team_id"] == 1
    assert ranking[0]["net_units"] == 10.0 - 5.0 + 2.0
    assert ranking[1]["team_id"] == 2
    assert ranking[1]["net_units"] == -8.0


def test_team_rankings_skip_missing_team_id():
    rows = [{"team_id": None, "sport_id": 1, "status": "win", "units": 9}]
    ranking = TeamRankingService().build_rankings(rows)
    assert ranking == []


def test_team_rankings_include_untracked_team_name():
    rows = [
        {"team_id": None, "team_name": "Other College", "status": "win", "units": 3},
        {"team_id": None, "team_name": "Other College", "status": "loss", "units": 1},
    ]
    ranking = TeamRankingService().build_rankings(rows)
    assert ranking[0]["team_name"] == "Other College"
    assert ranking[0]["net_units"] == 2.0


def test_fetch_rankings_filters_by_sport(monkeypatch):
    calls = []

    class Response:
        data = ROWS[:3]

    def select(table, columns, filters):
        calls.append((table, columns, filters))
        return Response()

    monkeypatch.setattr("src.services.team_ranking_service.supabase_service.select", select)

    rankings = TeamRankingService.fetch_rankings_from_supabase(7)

    assert rankings[0]["team_id"] == 1
    assert calls == [("plays", "team_id, team_name, status, units", {"sport_id": 7})]


def test_fetch_active_sports_excludes_internal_official_bucket(monkeypatch):
    class Response:
        data = [
            {"id": 1, "api_slug": "official", "name": "Official"},
            {"id": 2, "api_slug": "baseball", "name": "Baseball"},
        ]

    monkeypatch.setattr("src.services.team_ranking_service.supabase_service.select", lambda *args: Response())

    assert TeamRankingService.fetch_active_sports() == [
        {"id": 2, "api_slug": "baseball", "name": "Baseball"},
    ]
