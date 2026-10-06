import asyncio
from types import SimpleNamespace

import src.bot as bot_module


def card_message():
    embed = bot_module.build_play_embed({
        "play_id": 7, "summary": "Capper play", "units": 2, "odds": 150, "to_win": 3, "play_text": "Leg 1: A (+150)",
    })
    return SimpleNamespace(embeds=[embed])


def test_play_card_embed_reflects_regrade_and_reopen():
    embed = bot_module.build_play_card_embed(card_message(), {
        "id": 7, "status": "regraded", "units": 2, "odds": -110, "play_text": "Leg 1: B (-110)",
    })
    fields = {field.name: field.value for field in embed.fields}
    assert embed.title == "Play #7 • Regraded"
    assert fields["Odds"] == "-110"
    assert fields["To win"] == "1.82u"
    assert fields["Selections"] == "Leg 1: B (-110)"

    reopened = bot_module.build_play_card_embed(card_message(), {"id": 7, "status": "open", "units": 2, "odds": 150})
    assert reopened.title == "Play #7 • Open"


def test_engagement_and_suggestion_views_use_dispatcher_ids_and_are_not_stored():
    async def build():
        return bot_module.build_engagement_view(7, 3, "Ace", 2), bot_module.build_suggestion_view(7, "win")

    engagement, suggestion = asyncio.run(build())
    assert [item.custom_id for item in engagement.children] == ["pm:tail:7", "pm:follow:3"]
    assert engagement.children[0].label == "Tail (2)"
    assert [item.custom_id for item in suggestion.children] == ["pm:as:7:win", "pm:asx:7"]
    assert engagement.is_finished() and suggestion.is_finished()


def test_partial_reaction_settles_play(monkeypatch):
    settled = []
    monkeypatch.setattr(bot_module.official_play_service, "settle_play", lambda play_id, result: settled.append((play_id, result)))

    async def no_update(*args):
        return None

    monkeypatch.setattr(bot_module, "update_play_message", no_update)
    owner = SimpleNamespace(id=42)

    class Reaction:
        emoji = bot_module.PARTIAL_REACTION

        async def users(self):
            yield owner

    message = SimpleNamespace(reactions=[Reaction()], guild=None)

    class Channel:
        async def fetch_message(self, message_id):
            return message

    plays = [{"id": 5, "user_id": 1, "status": "regraded", "message_id": "99"}]
    users = [{"id": 1, "discord_user_id": "42"}]
    count = asyncio.run(bot_module.reconcile_open_play_reactions(plays, users, [Channel()]))
    assert count == 1
    assert settled == [(5, "partial")]


def test_mystats_embed_sections():
    summary = {"wins": 2, "losses": 1, "voids": 0, "partials": 0, "net": 1.5, "risked": 3, "roi": 50.0}
    stats = {
        "capper": {"month": summary, "all_time": summary, "streak": "W2", "sports": [{"sport": "NFL", **summary}]},
        "tails": {**summary, "open": 1, "count": 4},
        "vault": {**summary, "count": 3, "month": summary},
    }
    embed = bot_module.build_mystats_embed(SimpleNamespace(display_name="Ace"), stats)
    names = [field.name for field in embed.fields]
    assert names[0] == "📣 Your Official Plays"
    assert "W2" in embed.fields[0].value
    assert "Open tails: 1" in embed.fields[1].value

    empty = bot_module.build_mystats_embed(SimpleNamespace(display_name="New"), {
        "capper": None,
        "tails": {**summary, "open": 0, "count": 0},
        "vault": {**summary, "count": 0, "month": summary},
    })
    assert [field.name for field in empty.fields][0].startswith("🎯")
