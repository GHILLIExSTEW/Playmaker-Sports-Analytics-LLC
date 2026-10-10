from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import httpx

from src.services import supabase_service as supabase_module
from src.services.api_sports_multi_service import ApiSportsMultiService, DATE_PRODUCTS, DATE_PRODUCT_PARAMS, normalize_event


@pytest.fixture(autouse=True)
def mock_request_budget(monkeypatch):
    monkeypatch.setattr("src.services.api_sports_multi_service.reserve_request", lambda _base_url: None)


@pytest.fixture
def synced_at():
    return datetime(2026, 10, 1, 12, tzinfo=timezone.utc)


def test_normalize_football_fixture(synced_at):
    result = normalize_event("football", {
        "fixture": {"id": 100, "date": "2026-10-01T00:00:00+00:00", "venue": {"name": "Park"}, "status": {"short": "FT", "long": "Match Finished"}},
        "league": {"id": 1, "name": "League", "season": 2026, "round": "Week 1"},
        "teams": {"home": {"id": 10, "name": "Home", "logo": "home.png"}, "away": {"id": 20, "name": "Away", "logo": "away.png"}},
        "goals": {"home": 2, "away": 1},
    }, synced_at)

    assert result["event_id"] == "100"
    assert result["event_name"] == "Home vs Away"
    assert result["home_score"] == 2
    assert result["status_code"] == "FT"


def test_normalize_basketball_total_scores(synced_at):
    result = normalize_event("basketball", {
        "id": 200,
        "date": "2026-10-01T00:00:00+00:00",
        "league": {"id": 18, "name": "Liga A", "season": "2026-2027"},
        "teams": {"home": {"id": 30, "name": "Argentino"}, "away": {"id": 31, "name": "Ferro"}},
        "scores": {"home": {"total": 79}, "away": {"total": 68}},
        "status": {"short": "FT", "long": "Game Finished"},
    }, synced_at)

    assert result["home_score"] == {"total": 79}
    assert result["away_score"] == {"total": 68}
    assert result["season"] == "2026-2027"


def test_normalize_formula_one_race_without_teams(synced_at):
    result = normalize_event("formula-1", {
        "id": 300,
        "date": "2026-03-06T01:30:00+00:00",
        "competition": {"id": 1, "name": "Australia Grand Prix", "location": {"country": "Australia", "city": "Melbourne"}},
        "circuit": {"name": "Albert Park Circuit"},
        "season": 2026,
        "type": "Practice",
        "status": "Completed",
    }, synced_at)

    assert result["event_name"] == "Australia Grand Prix"
    assert result["venue"]["name"] == "Albert Park Circuit"
    assert result["status_code"] == "COMPLETED"

def test_normalize_mma_uses_fighters_and_fight_card(synced_at):
    result = normalize_event("mma", {
        "id": 2912, "date": "2026-10-03T12:00:00+00:00",
        "slug": "UFC 332: Silva vs. Wang", "category": "Middleweight",
        "status": {"short": "FT", "long": "Finished"},
        "fighters": {
            "first": {"id": 241, "name": "Ismail Naurdiev", "logo": "first.png", "winner": True},
            "second": {"id": 503, "name": "Marvin Vettori", "logo": "second.png", "winner": False},
        },
    }, synced_at)
    assert result["event_name"] == "Ismail Naurdiev vs Marvin Vettori"
    assert result["home_name"] == "Ismail Naurdiev"
    assert result["away_name"] == "Marvin Vettori"
    assert result["home_id"] == "241"
    assert result["away_id"] == "503"
    assert result["home_logo"] == "first.png"
    assert result["away_logo"] == "second.png"
    assert result["league_name"] == "UFC 332: Silva vs. Wang"
    assert result["round_name"] == "Middleweight"
    assert result["status_code"] == "FT"
    assert result["home_score"] is None
    assert result["away_score"] is None


def test_normalize_ncaa_nested_game(synced_at):
    result = normalize_event("ncaa", {
        "game": {"id": 400, "date": "2026-10-02T19:00:00+00:00", "venue": {"name": "Stadium"}, "week": 6, "status": {"short": "NS", "long": "Not Started"}},
        "league": {"id": 2, "name": "NCAA", "season": 2026},
        "teams": {"home": {"id": 40, "name": "Home College", "logo": "home.png"}, "away": {"id": 41, "name": "Away College", "logo": "away.png"}},
        "scores": {"home": {"total": 0}, "away": {"total": 0}},
    }, synced_at)

    assert result["event_id"] == "400"
    assert result["round_name"] == "6"
    assert result["event_name"] == "Home College vs Away College"
    assert result["status_code"] == "NS"


class FakeQuery:
    def __init__(self, data=None):
        self.data = data or []

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def gte(self, *_args, **_kwargs):
        return self

    def lte(self, *_args, **_kwargs):
        return self

    def in_(self, *_args, **_kwargs):
        return self

    def upsert(self, *_args, **_kwargs):
        return self

    def execute(self):
        return SimpleNamespace(data=self.data)


class FakeSupabase:
    def table(self, _table):
        return FakeQuery()


def test_daily_complete_accepts_trimmed_postgres_fraction():
    client = SimpleNamespace(table=lambda _name: FakeQuery([
        {"success": True, "last_success_at": "2026-10-02T10:25:23.42324+00:00"},
    ]))
    service = ApiSportsMultiService(client=client)
    assert service._daily_complete("mma", datetime(2026, 10, 2).date())
    assert not service._daily_complete("mma", datetime(2026, 10, 3).date())


class FakeApiResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"errors": {}, "response": []}


def test_daily_sync_uses_seven_date_requests_per_sport_and_one_formula_one_request(synced_at):
    calls = []

    def fake_get(url, params, **_kwargs):
        calls.append((url, params))
        return FakeApiResponse()

    service = ApiSportsMultiService(api_key="test-key", client=FakeSupabase(), get=fake_get, clock=lambda: synced_at)
    result = service.sync_daily(force=True)

    assert result["total_requests"] == len(DATE_PRODUCTS) * 7 + 1
    assert len(calls) == result["total_requests"]
    assert result["sports"]["formula-1"]["requests"] == 1


def test_live_sync_with_no_active_sports_uses_no_api_requests(synced_at):
    calls = []
    service = ApiSportsMultiService(
        api_key="test-key",
        client=FakeSupabase(),
        get=lambda *args, **kwargs: calls.append((args, kwargs)) or FakeApiResponse(),
        clock=lambda: synced_at,
    )

    result = service.sync_live_scores()

    assert result == {"total_requests": 0, "sports": {}}
    assert calls == []


def test_ncaa_feed_uses_american_football_ncaa_league_filter():
    assert DATE_PRODUCTS["ncaa"] == ("https://v1.american-football.api-sports.io", "games")
    assert DATE_PRODUCT_PARAMS["ncaa"] == {"league": 2}

def test_formula_one_is_active_and_live_sync_uses_season_request(synced_at):
    client = SimpleNamespace(table=lambda _name: FakeQuery([
        {"sport_slug": "formula-1", "status_code": "NS"},
        {"sport_slug": "hockey", "status_code": "FT"},
        {"sport_slug": "cricket", "status_code": "NS"},
    ]))
    calls = []
    service = ApiSportsMultiService(
        api_key="test-key", client=client, clock=lambda: synced_at,
        get=lambda url, params, **_kwargs: calls.append((url, params)) or FakeApiResponse(),
    )
    assert service.active_sports() == ["formula-1"]
    result = service.sync_live_scores()
    assert calls == [("https://v1.formula-1.api-sports.io/races", {"season": 2026})]
    assert result["sports"]["formula-1"] == {"requests": 1, "events": 0}


@pytest.mark.parametrize("operation", ["daily_check", "record", "store", "active"])
def test_cache_requests_retry_disconnect_with_a_fresh_client(monkeypatch, synced_at, operation):
    calls = []

    class CacheClient:
        def __init__(self, disconnected):
            self.disconnected = disconnected

        def table(self, name):
            query = FakeQuery([{"success": True, "last_success_at": synced_at.isoformat()}] if name == "api_sports_sync_state" else [])

            def execute():
                calls.append((name, self.disconnected))
                if self.disconnected:
                    raise httpx.RemoteProtocolError("Server disconnected")
                return SimpleNamespace(data=query.data)

            query.execute = execute
            return query

    monkeypatch.setattr(supabase_module.supabase_service, "client", CacheClient(True))
    monkeypatch.setattr(supabase_module, "create_client", lambda *_args: CacheClient(False))
    service = ApiSportsMultiService(clock=lambda: synced_at)
    if operation == "daily_check":
        assert service._daily_complete("formula-1", synced_at.date())
    elif operation == "record":
        service._record("events:formula-1", synced_at, True, 1)
    elif operation == "store":
        service._store_events([{"sport_slug": "formula-1", "event_id": "1"}])
    else:
        assert service.active_sports() == []
    assert len(calls) == 2
    assert calls[0][1] is True and calls[1][1] is False
    assert service.request_count == 0


def test_persistent_cache_disconnect_is_not_reported_as_success(monkeypatch, synced_at):
    calls = []

    class DisconnectedClient:
        def table(self, _name):
            query = FakeQuery()

            def execute():
                calls.append(True)
                raise httpx.RemoteProtocolError("Server disconnected")

            query.execute = execute
            return query

    monkeypatch.setattr(supabase_module.supabase_service, "client", DisconnectedClient())
    monkeypatch.setattr(supabase_module, "create_client", lambda *_args: DisconnectedClient())
    service = ApiSportsMultiService(clock=lambda: synced_at)
    with pytest.raises(httpx.RemoteProtocolError, match="Server disconnected"):
        service.sync_daily()
    assert len(calls) == 3


def test_daily_sync_retries_event_storage_without_repeating_provider_request(monkeypatch, synced_at):
    provider_calls = []
    storage_calls = []

    class CacheClient:
        def __init__(self, disconnected):
            self.disconnected = disconnected

        def table(self, name):
            query = FakeQuery()
            query.eq = lambda _column, key: (
                setattr(query, "data", [] if key == "events:formula-1" else [
                    {"success": True, "last_success_at": synced_at.isoformat()},
                ]) or query
            )

            def execute():
                if name == "api_sports_events":
                    storage_calls.append(self.disconnected)
                    if self.disconnected:
                        raise httpx.RemoteProtocolError("Server disconnected")
                return SimpleNamespace(data=query.data)

            query.execute = execute
            return query

    class RaceResponse(FakeApiResponse):
        def json(self):
            return {"response": [{"id": 300, "date": synced_at.isoformat(), "season": 2026}]}

    monkeypatch.setattr(supabase_module.supabase_service, "client", CacheClient(True))
    monkeypatch.setattr(supabase_module, "create_client", lambda *_args: CacheClient(False))
    service = ApiSportsMultiService(
        api_key="test-key", clock=lambda: synced_at,
        get=lambda *args, **kwargs: provider_calls.append((args, kwargs)) or RaceResponse(),
    )
    result = service.sync_daily()
    assert storage_calls == [True, False]
    assert len(provider_calls) == 1
    assert result["total_requests"] == 1
    assert result["sports"]["formula-1"] == {"requests": 1, "events": 1}