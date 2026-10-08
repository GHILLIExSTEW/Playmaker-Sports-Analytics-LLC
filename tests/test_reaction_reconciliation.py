import asyncio
from types import SimpleNamespace

import src.bot as bot_module


class FakeReaction:
    def __init__(self, emoji, users):
        self.emoji = emoji
        self._users = users

    def users(self):
        async def iterate():
            for user in self._users:
                yield user

        return iterate()


class FakeChannel:
    def __init__(self, message):
        self.message = message

    async def fetch_message(self, message_id):
        assert message_id == 500
        return self.message


def test_reconcile_open_play_reactions_settles_owner_reaction(monkeypatch):
    message = SimpleNamespace(
        reactions=[FakeReaction("✅", [SimpleNamespace(id=999), SimpleNamespace(id=123)])],
        embeds=[],
        guild=None,
    )
    async def edit(**kwargs):
        message.edited = kwargs

    message.edit = edit
    plays = [{"id": 42, "user_id": 7, "status": "open", "message_id": "500"}]
    users = [{"id": 7, "discord_user_id": "123"}]
    settlements = []
    monkeypatch.setattr(
        bot_module.official_play_service,
        "settle_play",
        lambda play_id, result: settlements.append((play_id, result)),
    )

    settled_count = asyncio.run(
        bot_module.reconcile_open_play_reactions(plays, users, [FakeChannel(message)])
    )

    assert settled_count == 1
    assert settlements == [(42, "win")]
    assert message.edited["embed"].title == "Play #42 • Win"


def test_reconcile_settles_owner_role_reaction_on_another_users_play(monkeypatch):
    operator = SimpleNamespace(
        id=555,
        roles=[SimpleNamespace(id=bot_module.WEBSITE_OWNER_ROLE_ID)],
        guild_permissions=SimpleNamespace(manage_guild=False),
    )
    guild = SimpleNamespace(get_member=lambda user_id: operator if user_id == 555 else None)
    message = SimpleNamespace(
        reactions=[FakeReaction("❌", [operator])],
        embeds=[],
        guild=guild,
    )

    async def edit(**kwargs):
        message.edited = kwargs

    message.edit = edit
    plays = [{"id": 43, "user_id": 7, "status": "open", "message_id": "500"}]
    users = [{"id": 7, "discord_user_id": "123"}]
    settlements = []
    monkeypatch.setattr(bot_module, "OPERATOR_ROLE_IDS", {4242})
    monkeypatch.setattr(
        bot_module.official_play_service,
        "settle_play",
        lambda play_id, result: settlements.append((play_id, result)),
    )

    settled_count = asyncio.run(
        bot_module.reconcile_open_play_reactions(plays, users, [FakeChannel(message)])
    )

    assert settled_count == 1
    assert settlements == [(43, "loss")]


def test_reconcile_ignores_reaction_from_unrelated_member(monkeypatch):
    stranger = SimpleNamespace(
        id=555,
        roles=[SimpleNamespace(id=1)],
        guild_permissions=SimpleNamespace(manage_guild=False),
    )
    guild = SimpleNamespace(get_member=lambda user_id: stranger if user_id == 555 else None)
    message = SimpleNamespace(
        reactions=[FakeReaction("✅", [stranger])],
        embeds=[],
        guild=guild,
    )
    plays = [{"id": 44, "user_id": 7, "status": "open", "message_id": "500"}]
    users = [{"id": 7, "discord_user_id": "123"}]
    monkeypatch.setattr(bot_module, "OPERATOR_ROLE_IDS", {4242})

    settled_count = asyncio.run(
        bot_module.reconcile_open_play_reactions(plays, users, [FakeChannel(message)])
    )

    assert settled_count == 0


def test_operator_or_server_manager_cannot_react_to_settle_someone_elses_pick(monkeypatch):
    for roles, manager in [([bot_module.TRACKER_ROLE_ID], False), ([4242], False), ([], True)]:
        member = SimpleNamespace(id=555, roles=[SimpleNamespace(id=role) for role in roles],
                                 guild_permissions=SimpleNamespace(manage_guild=manager))
        guild = SimpleNamespace(get_member=lambda user_id: member)
        monkeypatch.setattr(bot_module, "OPERATOR_ROLE_IDS", {4242, bot_module.TRACKER_ROLE_ID})
        assert not bot_module.user_can_settle(member, "123", guild)
        assert bot_module.user_can_settle(member, "555", guild)


def test_bot_cannot_settle_even_as_author():
    assert not bot_module.user_can_settle(SimpleNamespace(id=123, bot=True), "123", None)