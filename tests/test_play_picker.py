import asyncio
from datetime import datetime, timedelta, timezone
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


def make_play(play_id, **overrides):
    return {"id": play_id, "user_id": 1, "units": 1, "legs": 2, "odds": 264, "status": "open", "team_name": None, "play_text": "", **overrides}


USERS = [{"id": 1, "display_name": "Mike", "username": "mike1"}]


def test_list_plays_pages_newest_first_and_reports_more(monkeypatch):
    client = patch_client(monkeypatch, [make_play(i) for i in range(30, 19, -1)], USERS)

    plays, has_more = OfficialPlayService().list_plays(page=1, page_size=10, statuses=["open"])

    assert len(plays) == 10 and has_more
    assert all(play["user_name"] == "Mike" for play in plays)
    assert ("plays", "in_", ("status", ["open"]), {}) in client.calls
    assert ("plays", "range", (10, 20), {}) in client.calls
    assert ("plays", "order", ("created_at",), {"desc": True}) in client.calls


def test_list_plays_last_page_and_since_filter(monkeypatch):
    client = patch_client(monkeypatch, [make_play(1)], USERS)
    since = datetime(2026, 10, 3, tzinfo=timezone.utc)

    plays, has_more = OfficialPlayService().list_plays(since=since)

    assert [play["id"] for play in plays] == [1] and not has_more
    assert ("plays", "gte", ("created_at", since.isoformat()), {}) in client.calls
    assert not any(call[1] == "in_" and call[0] == "plays" for call in client.calls)


def test_open_play_label_shows_status_and_fits_limit():
    assert OfficialPlayService.open_play_label({**make_play(12, team_name="Lakers"), "user_name": "Mike"}) == "#12 • Mike • 1u 2-leg +264 • Lakers"
    assert OfficialPlayService.open_play_label(make_play(5, status="win")).startswith("#5 [WIN]")
    assert len(OfficialPlayService.open_play_label(make_play(9, play_text="x" * 300))) == 100


def test_load_play_page_scopes_settle_and_regrade(monkeypatch):
    calls = []
    monkeypatch.setattr(bot_module.official_play_service, "list_plays", lambda *args, **kwargs: calls.append((args, kwargs)) or ([], False))

    bot_module.load_play_page("settle", 0)
    bot_module.load_play_page("regrade", 2)

    assert calls[0] == ((0, 10), {"statuses": ["open"]})
    assert calls[1][0] == (2, 10)
    assert abs(datetime.now(timezone.utc) - timedelta(days=2) - calls[1][1]["since"]) < timedelta(seconds=5)


def test_picker_view_has_ten_options_and_paging_buttons():
    async def build():
        return bot_module.PlayPickerView(1, "settle", [make_play(i) for i in range(10)], page=0, has_more=True)

    view = asyncio.run(build())
    buttons = [item for item in view.children if isinstance(item, bot_module.discord.ui.Button)]

    assert len(view.select.options) == 10
    assert [button.disabled for button in buttons] == [True, False]


class FakeInteraction:
    def __init__(self, user_id=1, roles=()):
        self.user = SimpleNamespace(id=user_id, roles=[SimpleNamespace(id=role) for role in roles])
        self.sent = []
        self.edits = []

        async def send(content=None, **kwargs):
            self.sent.append((content, kwargs))

        async def edit_original_response(**kwargs):
            self.edits.append(kwargs)

        self.followup = SimpleNamespace(send=send)
        self.edit_original_response = edit_original_response


def test_show_play_picker_reports_empty_list(monkeypatch):
    monkeypatch.setattr(bot_module, "load_play_page", lambda mode, page: ([], False))
    interaction = FakeInteraction()

    asyncio.run(bot_module.show_play_picker(interaction, "regrade", 0))

    assert interaction.sent == [("There are no plays from the last 2 days to regrade.", {"ephemeral": True})]


def test_show_play_picker_sends_view(monkeypatch):
    monkeypatch.setattr(bot_module, "load_play_page", lambda mode, page: ([make_play(3)], False))
    interaction = FakeInteraction()

    asyncio.run(bot_module.show_play_picker(interaction, "settle", 0))

    content, kwargs = interaction.sent[0]
    assert content == "Select an open play to settle (page 1):"
    assert isinstance(kwargs["view"], bot_module.PlayPickerView)


def test_is_official(monkeypatch):
    monkeypatch.setattr(bot_module, "OFFICIAL_ROLE_IDS", {555})
    assert bot_module.is_official(FakeInteraction(roles=[555]).user)
    assert not bot_module.is_official(FakeInteraction(roles=[1]).user)
