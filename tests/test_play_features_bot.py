import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

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
        return bot_module.build_engagement_view(7, 2), bot_module.build_suggestion_view(7, "win")

    engagement, suggestion = asyncio.run(build())
    assert [item.custom_id for item in engagement.children] == ["pm:tail:7"]
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


class ShareInteraction:
    def __init__(self, user_id=42, roles=()):
        self.user = SimpleNamespace(
            id=user_id, mention=f"<@{user_id}>", display_name="Ace", roles=list(roles),
            display_avatar=SimpleNamespace(url="https://cdn/ace.png"),
        )
        self.response = SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock())
        self.followup = SimpleNamespace(send=AsyncMock())
        self.delete_original_response = AsyncMock()


class FakeRole:
    def __init__(self, role_id, name):
        self.id, self.name, self.mention = role_id, name, f"<@&{role_id}>"


def share_view(monkeypatch, tier="vip", allowed=True, webhooks=None):
    bot_module.share_webhooks.clear()
    roles = [FakeRole(bot_module.HIGHROLLER_ROLE_ID, "HIGHROLLER"), FakeRole(bot_module.ROOKIE_ROLE_ID, "ROOKIE")]
    guild = SimpleNamespace(roles=roles, get_role=lambda role_id: next((r for r in roles if r.id == role_id), None))
    webhook = SimpleNamespace(send=AsyncMock(), channel_id=0)
    channel = SimpleNamespace(
        guild=guild, webhooks=AsyncMock(return_value=webhooks or []), create_webhook=AsyncMock(return_value=webhook),
    )

    async def resolve(channel_id, name, required=False):
        channel.id = webhook.channel_id = channel_id
        return channel

    monkeypatch.setattr(bot_module, "resolve_channel", resolve)
    membership = Mock()
    membership.has_paid_access.return_value = allowed
    monkeypatch.setattr(bot_module, "MembershipService", lambda: membership)

    async def build():
        return bot_module.ShareStatsView(42, bot_module.discord.Embed(title="Stats"), tier)

    return asyncio.run(build()), channel, webhook


def test_share_view_offers_one_chat_per_tier(monkeypatch):
    vip, _, _ = share_view(monkeypatch, "vip")
    free, _, _ = share_view(monkeypatch, "free")
    assert [item.label for item in vip.children] == ["Share to VIP CHAT"]
    assert [item.label for item in free.children] == ["Share to FREE CHAT"]
    assert str(vip.children[0].emoji) == "\U0001F4E3"


def test_vip_share_posts_as_member_via_webhook_tags_highroller_and_deletes_private_card(monkeypatch):
    view, channel, webhook = share_view(monkeypatch, "vip")
    interaction = ShareInteraction()
    asyncio.run(view.children[0].callback(interaction))
    assert channel.id == bot_module.VIP_CHAT_CHANNEL_ID == 1328136977463378051
    channel.create_webhook.assert_awaited_once()
    kwargs = webhook.send.call_args.kwargs
    assert kwargs["username"] == "Ace" and kwargs["avatar_url"] == "https://cdn/ace.png"
    assert kwargs["content"].startswith("<@&1328120234749464739>") and kwargs["embed"].title == "Stats"
    assert [role.id for role in kwargs["allowed_mentions"].roles] == [1328120234749464739]
    assert kwargs["allowed_mentions"].users is False and kwargs["allowed_mentions"].everyone is False
    interaction.delete_original_response.assert_awaited_once()
    interaction.followup.send.assert_not_awaited()
    assert view.is_finished()


def test_free_share_tags_rookie_and_reuses_existing_webhook(monkeypatch):
    bot_user = SimpleNamespace(id=999)
    monkeypatch.setattr(type(bot_module.bot), "user", property(lambda self: bot_user))
    existing = SimpleNamespace(name=bot_module.SHARE_WEBHOOK_NAME, user=bot_user, token="t", send=AsyncMock(), channel_id=0)
    view, channel, _ = share_view(monkeypatch, "free", allowed=False, webhooks=[existing])
    asyncio.run(view.children[0].callback(ShareInteraction()))
    assert channel.id == bot_module.FREE_CHAT_CHANNEL_ID == 1556482816974389290
    channel.create_webhook.assert_not_awaited()
    assert existing.send.call_args.kwargs["content"].startswith("<@&1556484440396660757>")


def test_share_rejects_other_users_and_lapsed_vip(monkeypatch):
    view, _, webhook = share_view(monkeypatch, "vip", allowed=False)
    stranger = ShareInteraction(user_id=7)
    asyncio.run(view.children[0].callback(stranger))
    assert "Only the member" in stranger.response.send_message.call_args.args[0]
    owner = ShareInteraction()
    asyncio.run(view.children[0].callback(owner))
    assert "HIGHROLLER members" in owner.followup.send.call_args.args[0]
    webhook.send.assert_not_awaited()
    owner.delete_original_response.assert_not_awaited()


def test_highroller_role_from_whop_unlocks_vip_without_paid_ledger(monkeypatch):
    view, _, webhook = share_view(monkeypatch, "vip", allowed=False)
    trial = ShareInteraction(roles=[SimpleNamespace(id=bot_module.HIGHROLLER_ROLE_ID)])
    asyncio.run(view.children[0].callback(trial))
    webhook.send.assert_awaited_once()
    trial.delete_original_response.assert_awaited_once()


def test_share_role_defaults_match_server_roles():
    assert bot_module.HIGHROLLER_ROLE_ID == 1328120234749464739
    assert bot_module.ROOKIE_ROLE_ID == 1556484440396660757
