import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import src.member_onboarding as onboarding
from src.member_onboarding import MemberOnboarding


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    monkeypatch.setattr(onboarding, "GUILD_ID", 999)
    monkeypatch.setattr(onboarding, "WHOP_MEMBERSHIP_SYNC_ENABLED", True)
    monkeypatch.setattr(onboarding, "MEMBER_BET_CHANNEL_ID", 888)
    monkeypatch.setattr(onboarding, "MEMBER_STATS_REFRESH_ENABLED", False)
    monkeypatch.setattr(onboarding, "API_SPORTS_BUDGET_ENABLED", False)
    monkeypatch.setattr(onboarding, "is_stats_moderator", lambda interaction: False)


def request(guild_id=999):
    return SimpleNamespace(
        guild_id=guild_id, user=SimpleNamespace(id=42, roles=[]),
        response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


def respond(cog, interaction, getting_started=False):
    asyncio.run(cog.respond(interaction, getting_started=getting_started))
    if not interaction.followup.send.called:
        return None
    return interaction.followup.send.call_args.kwargs["embed"]


def fields(embed):
    return {field.name: field.value for field in embed.fields}


@pytest.mark.parametrize("vault,stats", [(True, True), (False, False), (True, False), (False, True)])
def test_feature_eligibility_is_checked_independently_and_privately(vault, stats):
    membership = Mock(
        has_vault_access=Mock(return_value=vault), has_stats_access=Mock(return_value=stats),
    )
    cog = MemberOnboarding(membership)
    interaction = request()
    embed = respond(cog, interaction)
    values = fields(embed)
    assert f"**{'Verified' if vault else 'Not verified'}**" in values["Member Vault"]
    assert f"**{'Verified' if stats else 'Not verified'}**" in values["Stats tools"]
    membership.has_vault_access.assert_called_once_with(42)
    membership.has_stats_access.assert_called_once_with(42)
    assert interaction.response.defer.call_args.kwargs["ephemeral"] is True
    kwargs = interaction.followup.send.call_args.kwargs
    assert kwargs["ephemeral"] is True
    assert kwargs["allowed_mentions"].users is False
    assert kwargs["allowed_mentions"].roles is False
    assert kwargs["allowed_mentions"].everyone is False
    assert embed.title == "Your Playmaker Access"
    assert "Account ID: `42`" in values["Your Discord account"]


@pytest.mark.parametrize("feature", ["vault", "stats"])
def test_failed_verification_is_not_denial_or_success(feature, caplog):
    membership = Mock(
        has_vault_access=Mock(return_value=True), has_stats_access=Mock(return_value=True),
    )
    getattr(membership, f"has_{feature}_access").side_effect = RuntimeError("private provider detail")
    embed = respond(MemberOnboarding(membership), request())
    values = fields(embed)
    failed = values["Member Vault" if feature == "vault" else "Stats tools"]
    healthy = values["Stats tools" if feature == "vault" else "Member Vault"]
    assert "Verification unavailable" in failed
    assert "**Verified**" in healthy
    assert "private provider detail" not in str(embed.to_dict())
    assert f"feature={feature} user=42" in caplog.text


def test_disabled_sync_does_not_query_membership(monkeypatch):
    monkeypatch.setattr(onboarding, "WHOP_MEMBERSHIP_SYNC_ENABLED", False)
    membership = Mock()
    values = fields(respond(MemberOnboarding(membership), request()))
    membership.has_vault_access.assert_not_called()
    membership.has_stats_access.assert_not_called()
    assert "Membership verification disabled" in values["Member Vault"]
    assert "Checkout remains closed" in values["Verification is not enabled"]


def test_highroller_role_does_not_replace_ledger_verification():
    membership = Mock(has_vault_access=Mock(return_value=False), has_stats_access=Mock(return_value=False))
    interaction = request()
    interaction.user.roles = [SimpleNamespace(name="HIGHROLLER", id=1328120234749464739)]
    values = fields(respond(MemberOnboarding(membership), interaction))
    assert "**Not verified**" in values["Member Vault"]
    assert "**Not verified**" in values["Stats tools"]


@pytest.mark.parametrize("sync_enabled", [True, False])
def test_moderator_override_only_applies_to_stats(monkeypatch, sync_enabled):
    monkeypatch.setattr(onboarding, "WHOP_MEMBERSHIP_SYNC_ENABLED", sync_enabled)
    monkeypatch.setattr(onboarding, "is_stats_moderator", lambda interaction: True)
    membership = Mock(has_vault_access=Mock(return_value=False))
    values = fields(respond(MemberOnboarding(membership), request()))
    assert "Available through moderator access" in values["Stats tools"]
    membership.has_stats_access.assert_not_called()
    assert "**Verified**" not in values["Member Vault"]


@pytest.mark.parametrize("guild_id", [None, 998])
def test_wrong_server_and_dm_rejected_before_lookup(guild_id):
    membership = Mock()
    interaction = request(guild_id)
    assert respond(MemberOnboarding(membership), interaction) is None
    assert interaction.response.send_message.call_args.kwargs["ephemeral"] is True
    interaction.response.defer.assert_not_awaited()
    membership.has_vault_access.assert_not_called()


def test_missing_configured_guild_rejects_lookup(monkeypatch):
    monkeypatch.setattr(onboarding, "GUILD_ID", None)
    membership = Mock()
    interaction = request()
    assert respond(MemberOnboarding(membership), interaction) is None
    membership.has_vault_access.assert_not_called()


@pytest.mark.parametrize("refresh,budget,enabled", [
    (False, False, False), (True, False, False), (False, True, False), (True, True, True),
])
def test_refresh_availability_requires_both_flags(monkeypatch, refresh, budget, enabled):
    monkeypatch.setattr(onboarding, "MEMBER_STATS_REFRESH_ENABLED", refresh)
    monkeypatch.setattr(onboarding, "API_SPORTS_BUDGET_ENABLED", budget)
    membership = Mock(has_vault_access=Mock(return_value=True), has_stats_access=Mock(return_value=True))
    values = fields(respond(MemberOnboarding(membership), request()))
    assert values["On-demand stats refresh"].startswith("Enabled" if enabled else "Disabled")


def test_start_includes_safe_support_and_submission_instructions():
    membership = Mock(has_vault_access=Mock(return_value=True), has_stats_access=Mock(return_value=True))
    embed = respond(MemberOnboarding(membership), request(), getting_started=True)
    values = fields(embed)
    assert embed.title == "Welcome to Playmaker Picks"
    assert "<#888>" in values["Member Vault"]
    assert "10 MB" in values["Member Vault"]
    assert "five minutes" in values["Missing access or checking expiration?"]
    assert onboarding.SUPPORT_EMAIL in values["Missing access or checking expiration?"]
    assert "do not return your plan or expiration date" in values["Missing access or checking expiration?"]
    assert "does not place a wager" in values["Get started"]
    assert len(embed) <= 6000
    assert all(len(field.value) <= 1024 for field in embed.fields)


def test_unconfigured_vault_is_not_advertised_as_available(monkeypatch):
    monkeypatch.setattr(onboarding, "MEMBER_BET_CHANNEL_ID", None)
    membership = Mock(has_vault_access=Mock(return_value=True), has_stats_access=Mock(return_value=True))
    values = fields(respond(MemberOnboarding(membership), request()))
    assert "not configured" in values["Member Vault"]
    assert "<#" not in values["Member Vault"]


def test_invalid_lookup_is_reported_as_unavailable():
    cog = MemberOnboarding(Mock())
    assert "Verification unavailable" in asyncio.run(cog.access_status(Mock(return_value="true"), 42, "vault"))


def test_both_commands_delegate_to_same_private_access_help():
    cog = MemberOnboarding(Mock())
    cog.respond = AsyncMock()
    interaction = request()
    asyncio.run(cog.start_command.callback(cog, interaction))
    cog.respond.assert_awaited_with(interaction, getting_started=True)
    asyncio.run(cog.membership_status_command.callback(cog, interaction))
    cog.respond.assert_awaited_with(interaction, getting_started=False)
