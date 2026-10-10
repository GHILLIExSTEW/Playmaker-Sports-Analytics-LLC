import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

import src.member_activity as activity_module
from src.member_activity import MemberActivity, ShareStatsView


def empty_stats():
    record = {
        "wins": 0, "losses": 0, "voids": 0, "partials": 0,
        "net": 0, "risked": 0, "roi": 0, "count": 0,
    }
    return {"capper": None, "tails": {**record, "open": 0}, "vault": {**record, "month": record}}


def activity():
    return MemberActivity(
        SimpleNamespace(user=SimpleNamespace(id=999)),
        features=Mock(personal_stats=Mock(return_value=empty_stats()), vault_bets=Mock(return_value=[])),
        resolve_channel=AsyncMock(),
        membership=Mock(has_paid_access=Mock(return_value=False)),
    )


def request(user_id=42):
    return SimpleNamespace(
        user=SimpleNamespace(
            id=user_id, roles=[], display_name="Ace", display_avatar=SimpleNamespace(url="https://cdn/ace.png"),
        ),
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
        delete_original_response=AsyncMock(),
    )


@pytest.mark.parametrize("vip,tier", [(False, "free"), (True, "vip")])
def test_mystats_retains_private_response_and_share_destination(vip, tier):
    cog = activity()
    cog.membership.has_paid_access.return_value = vip
    interaction = request()
    asyncio.run(cog.mystats_command.callback(cog, interaction))
    cog.features.personal_stats.assert_called_once_with(42)
    interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    kwargs = interaction.followup.send.call_args.kwargs
    assert kwargs["ephemeral"] is True
    assert kwargs["embed"].title == "📈 Stats for Ace"
    assert kwargs["view"].tier == tier
    assert kwargs["view"].activity is cog


def test_mystats_membership_failure_keeps_stats_private_without_public_share(caplog):
    cog = activity()
    cog.membership.has_paid_access.side_effect = RuntimeError("lookup unavailable")
    interaction = request()
    asyncio.run(cog.mystats_command.callback(cog, interaction))
    kwargs = interaction.followup.send.call_args.kwargs
    assert kwargs["embed"].title == "📈 Stats for Ace"
    assert kwargs["ephemeral"] is True
    assert "view" not in kwargs
    assert "sharing is temporarily unavailable" in interaction.followup.send.call_args.args[0]
    assert "mystats_membership_lookup_failed" in caplog.text


def test_mystats_database_failure_is_private():
    cog = activity()
    cog.features.personal_stats.side_effect = RuntimeError("database unavailable")
    interaction = request()
    asyncio.run(cog.mystats_command.callback(cog, interaction))
    assert "Could not load your stats" in interaction.followup.send.call_args.args[0]
    assert interaction.followup.send.call_args.kwargs["ephemeral"] is True
    cog.membership.has_paid_access.assert_not_called()


def test_missing_share_channel_keeps_private_stats_without_button(monkeypatch):
    monkeypatch.setitem(activity_module.SHARE_CHANNELS, "free", ("FREE CHAT", None, None, "ROOKIE"))
    cog = activity()
    interaction = request()
    asyncio.run(cog.mystats_command.callback(cog, interaction))
    assert "view" not in interaction.followup.send.call_args.kwargs
    assert interaction.followup.send.call_args.kwargs["ephemeral"] is True


def test_concurrent_share_clicks_publish_once():
    cog = activity()
    cog.post_shared_stats = AsyncMock()

    async def check():
        view = ShareStatsView(cog, 42, discord.Embed(title="Stats"), "free")
        first, second = request(), request()
        await asyncio.gather(
            view.children[0].callback(first), view.children[0].callback(second),
        )
        cog.post_shared_stats.assert_awaited_once()
        assert first.delete_original_response.await_count + second.delete_original_response.await_count == 1
        assert first.followup.send.await_count + second.followup.send.await_count == 1

    asyncio.run(check())


def test_share_membership_error_blocks_publication():
    cog = activity()
    cog.membership.has_paid_access.side_effect = RuntimeError("lookup unavailable")
    cog.post_shared_stats = AsyncMock()
    interaction = request()

    async def check():
        view = ShareStatsView(cog, 42, discord.Embed(title="Stats"), "vip")
        await view.children[0].callback(interaction)
        assert not view.is_finished()

    asyncio.run(check())
    cog.post_shared_stats.assert_not_awaited()
    assert "Couldn't verify" in interaction.followup.send.call_args.args[0]


def test_failed_share_can_be_retried_without_losing_private_card():
    cog = activity()
    cog.post_shared_stats = AsyncMock(side_effect=[RuntimeError("send failed"), None])
    interaction = request()

    async def check():
        view = ShareStatsView(cog, 42, discord.Embed(title="Stats"), "free")
        await view.children[0].callback(interaction)
        assert not view.is_finished()
        interaction.delete_original_response.assert_not_awaited()
        await view.children[0].callback(request())
        assert view.is_finished()

    asyncio.run(check())
    assert "Couldn't post" in interaction.followup.send.call_args.args[0]
    assert cog.post_shared_stats.await_count == 2


def test_webhook_cache_is_per_cog_and_reused():
    cog = activity()
    hook = SimpleNamespace(
        name=activity_module.SHARE_WEBHOOK_NAME, user=cog.bot.user, token="test",
    )
    channel = SimpleNamespace(
        id=777, webhooks=AsyncMock(return_value=[hook]), create_webhook=AsyncMock(),
    )

    async def check():
        assert await cog.get_share_webhook(channel) is hook
        assert await cog.get_share_webhook(channel) is hook

    asyncio.run(check())
    channel.webhooks.assert_awaited_once()
    channel.create_webhook.assert_not_awaited()
    assert activity().share_webhooks == {}


def test_webhook_discovery_does_not_reuse_another_bots_hook():
    cog = activity()
    foreign = SimpleNamespace(
        name=activity_module.SHARE_WEBHOOK_NAME, user=SimpleNamespace(id=1), token="test",
    )
    created = SimpleNamespace(channel_id=777)
    channel = SimpleNamespace(
        id=777, webhooks=AsyncMock(return_value=[foreign]),
        create_webhook=AsyncMock(return_value=created),
    )
    assert asyncio.run(cog.get_share_webhook(channel)) is created
    channel.create_webhook.assert_awaited_once_with(
        name=activity_module.SHARE_WEBHOOK_NAME, reason="Members sharing /mystats",
    )


def test_thread_sharing_uses_parent_webhook_and_preserves_thread_target():
    cog = activity()
    parent = SimpleNamespace(id=777)
    role = SimpleNamespace(id=activity_module.ROOKIE_ROLE_ID, mention="<@&123>")
    thread = Mock(spec=discord.Thread)
    thread.parent = parent
    thread.guild = SimpleNamespace(get_role=lambda role_id: role)
    hook = SimpleNamespace(send=AsyncMock())
    cog.resolve_channel.return_value = thread
    cog.get_share_webhook = AsyncMock(return_value=hook)
    asyncio.run(cog.post_shared_stats(request().user, discord.Embed(), "free"))
    cog.get_share_webhook.assert_awaited_once_with(parent)
    assert hook.send.call_args.kwargs["thread"] is thread
    assert hook.send.call_args.kwargs["wait"] is True


def test_missing_share_role_logs_warning_without_broad_mentions(caplog):
    cog = activity()
    hook = SimpleNamespace(send=AsyncMock())
    cog.resolve_channel.return_value = SimpleNamespace(
        guild=SimpleNamespace(get_role=lambda role_id: None, roles=[]),
    )
    cog.get_share_webhook = AsyncMock(return_value=hook)
    asyncio.run(cog.post_shared_stats(request().user, discord.Embed(), "free"))
    assert "mystats_share_role_missing" in caplog.text
    assert hook.send.call_args.kwargs["allowed_mentions"].roles is False


def test_deleted_webhook_invalidates_only_matching_cache():
    cog = activity()
    error = discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "deleted")
    hook = SimpleNamespace(channel_id=777, send=AsyncMock(side_effect=error))
    role = SimpleNamespace(id=activity_module.ROOKIE_ROLE_ID, mention="<@&123>")
    cog.resolve_channel.return_value = SimpleNamespace(
        id=777, guild=SimpleNamespace(get_role=lambda role_id: role),
    )
    cog.share_webhooks[777] = hook
    cog.share_webhooks[888] = object()
    with pytest.raises(discord.NotFound):
        asyncio.run(cog.post_shared_stats(request().user, discord.Embed(), "free"))
    assert 777 not in cog.share_webhooks
    assert 888 in cog.share_webhooks


def test_leaderboard_retains_public_response_and_eastern_month_window(monkeypatch):
    now = datetime(2026, 11, 1, 0, 30, tzinfo=activity_module.TRACKER_TIMEZONE)
    monkeypatch.setattr(activity_module, "datetime", SimpleNamespace(now=lambda zone: now))
    builder = Mock(return_value=[])
    monkeypatch.setattr(activity_module, "vault_leaderboard", builder)
    cog = activity()
    interaction = request()
    asyncio.run(cog.vault_leaderboard_command.callback(cog, interaction))
    interaction.response.defer.assert_awaited_once_with(thinking=True)
    kwargs = interaction.followup.send.call_args.kwargs
    assert kwargs.get("ephemeral", False) is False
    assert kwargs["embed"].title == "🏦 Vault Leaderboard — November 2026"
    assert kwargs["embed"].description == "No settled vault bets this month yet."
    assert builder.call_args.args[1] == now.replace(hour=0, minute=0)
    assert builder.call_args.kwargs["limit"] == 10


def test_leaderboard_failure_is_private():
    cog = activity()
    cog.features.vault_bets.side_effect = RuntimeError("database unavailable")
    interaction = request()
    asyncio.run(cog.vault_leaderboard_command.callback(cog, interaction))
    assert "Could not load the leaderboard" in interaction.followup.send.call_args.args[0]
    assert interaction.followup.send.call_args.kwargs["ephemeral"] is True
