import asyncio
from types import SimpleNamespace

import src.bot as bot_module
import src.services.official_play_service as official_play_module
from src.services.official_play_service import OfficialPlayService


class FakeQuery:
    def __init__(self, table, data, calls):
        self.table_name = table
        self.data = data
        self.calls = calls

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((self.table_name, name, args, kwargs))
            return self
        return record

    def execute(self):
        return SimpleNamespace(data=self.data)


class FakeClient:
    def __init__(self, plays, users):
        self.rows = {"plays": plays, "users": users}
        self.calls = []

    def table(self, name):
        return FakeQuery(name, self.rows[name], self.calls)


def patch_client(monkeypatch, plays, users):
    client = FakeClient(plays, users)
    monkeypatch.setattr(official_play_module.supabase_service, "_ensure_client", lambda: client)
    monkeypatch.setattr(official_play_module.supabase_service, "_execute", lambda operation: operation())
    return client


PLAYS = [
    {"id": 12, "user_id": 1, "units": 2, "legs": 3, "odds": 450, "team_name": "Lakers", "play_text": ""},
    {"id": 9, "user_id": 2, "units": 1, "legs": 1, "odds": -110, "team_name": None, "play_text": "Chiefs ML"},
]
USERS = [{"id": 1, "display_name": "Mike", "username": "mike1"}, {"id": 2, "display_name": None, "username": "sara"}]


def test_list_open_plays_queries_only_open_plays_and_names_cappers(monkeypatch):
    client = patch_client(monkeypatch, PLAYS, USERS)

    plays = OfficialPlayService().list_open_plays()

    assert ("plays", "eq", ("status", "open"), {}) in client.calls
    assert [play["id"] for play in plays] == [12, 9]
    assert [play["user_name"] for play in plays] == ["Mike", "sara"]


def test_list_open_plays_filters_by_search_text(monkeypatch):
    patch_client(monkeypatch, PLAYS, USERS)
    service = OfficialPlayService()

    assert [play["id"] for play in service.list_open_plays("chiefs")] == [9]
    assert [play["id"] for play in service.list_open_plays("#12")] == [12]
    assert [play["id"] for play in service.list_open_plays("mike")] == [12]


def test_open_play_label_is_readable_and_fits_discord_limit():
    label = OfficialPlayService.open_play_label({**PLAYS[0], "user_name": "Mike"})
    assert label == "#12 • Mike • 2u 3-leg +450 • Lakers"

    long_label = OfficialPlayService.open_play_label({**PLAYS[1], "user_name": "sara", "play_text": "x" * 300})
    assert len(long_label) == 100


def test_autocomplete_hidden_from_non_officials(monkeypatch):
    monkeypatch.setattr(bot_module, "OFFICIAL_ROLE_IDS", {555})
    called = []
    monkeypatch.setattr(bot_module.official_play_service, "list_open_plays", lambda search: called.append(search) or [])
    interaction = SimpleNamespace(user=SimpleNamespace(id=1, roles=[SimpleNamespace(id=1)]))

    assert asyncio.run(bot_module.open_play_autocomplete(interaction, "")) == []
    assert called == []


def test_autocomplete_returns_open_play_choices_for_officials(monkeypatch):
    monkeypatch.setattr(bot_module, "OFFICIAL_ROLE_IDS", {555})
    monkeypatch.setattr(
        bot_module.official_play_service,
        "list_open_plays",
        lambda search: [{**PLAYS[0], "user_name": "Mike"}],
    )
    interaction = SimpleNamespace(user=SimpleNamespace(id=1, roles=[SimpleNamespace(id=555)]))

    choices = asyncio.run(bot_module.open_play_autocomplete(interaction, "lak"))

    assert [(choice.name, choice.value) for choice in choices] == [("#12 • Mike • 2u 3-leg +450 • Lakers", "12")]
