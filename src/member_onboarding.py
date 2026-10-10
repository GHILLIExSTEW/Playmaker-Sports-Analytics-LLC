from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

import discord
from discord import app_commands
from discord.ext import commands

from src.config import (
    API_SPORTS_BUDGET_ENABLED,
    GUILD_ID,
    MEMBER_BET_CHANNEL_ID,
    MEMBER_STATS_REFRESH_ENABLED,
    WHOP_MEMBERSHIP_SYNC_ENABLED,
)
from src.member_stats import is_stats_moderator
from src.services.membership_service import MembershipService

logger = logging.getLogger("member_onboarding")
SUPPORT_EMAIL = "support@playmakersportsanalytics.com"


class MemberOnboarding(commands.Cog):
    def __init__(self, membership: MembershipService | None = None) -> None:
        self.membership = membership if membership is not None else MembershipService()

    async def access_status(
        self, lookup: Callable[[int], bool], user_id: int, feature: str,
    ) -> str:
        try:
            eligible = await asyncio.to_thread(lookup, user_id)
            if not isinstance(eligible, bool):
                raise RuntimeError("Membership lookup returned an invalid access response.")
            return "Verified" if eligible else "Not verified"
        except Exception:
            logger.exception("onboarding_access_failed feature=%s user=%s", feature, user_id)
            return "Verification unavailable - retry later or contact support"

    async def respond(self, interaction: discord.Interaction, *, getting_started: bool) -> None:
        if not GUILD_ID or interaction.guild_id != GUILD_ID:
            await interaction.response.send_message(
                "Use this command in the Playmaker Discord server.", ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        moderator = is_stats_moderator(interaction)
        if WHOP_MEMBERSHIP_SYNC_ENABLED:
            if moderator:
                vault = await self.access_status(self.membership.has_vault_access, interaction.user.id, "vault")
                stats = "Available through moderator access"
            else:
                vault, stats = await asyncio.gather(
                    self.access_status(self.membership.has_vault_access, interaction.user.id, "vault"),
                    self.access_status(self.membership.has_stats_access, interaction.user.id, "stats"),
                )
        else:
            vault = "Membership verification disabled"
            stats = "Available through moderator access" if moderator else "Membership verification disabled"

        embed = discord.Embed(
            title="Welcome to Playmaker Picks" if getting_started else "Your Playmaker Access",
            description=(
                "Sports analysis and play tracking, not a sportsbook. "
                "This check is private and does not purchase, extend or grant access."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Your Discord account",
            value=f"Account ID: `{interaction.user.id}`\nUse this same account when linking Discord in Whop.",
            inline=False,
        )
        vault_text = f"Eligibility: **{vault}**"
        if MEMBER_BET_CHANNEL_ID:
            vault_text += (
                f"\nIf verified, submit in <#{MEMBER_BET_CHANNEL_ID}>: attach exactly one static "
                "JPEG, PNG or WebP slip photo (up to 10 MB). Units can go in the caption."
                "\nThis is a submission-only channel; read the confirmation before completing it."
            )
        else:
            vault_text += "\nThe submission channel is not configured; contact staff."
        embed.add_field(name="Member Vault", value=vault_text, inline=False)
        embed.add_field(
            name="Stats tools",
            value=(
                f"Access: **{stats}**\n"
                "`/matchup`, `/teamstats`, `/schedule`, `/results`, `/gamestats`, `/playerstats`."
                "\nUse commands without refresh for cached data; check the report's update time."
            ),
            inline=False,
        )
        refresh_enabled = MEMBER_STATS_REFRESH_ENABLED and API_SPORTS_BUDGET_ENABLED
        embed.add_field(
            name="On-demand stats refresh",
            value=(
                "Enabled for eligible stats users, subject to provider coverage, cooldowns and request limits."
                if refresh_enabled else
                "Disabled until both on-demand refresh and the shared API budget are enabled. Cached stats may still be available."
            ),
            inline=False,
        )
        embed.add_field(
            name="Get started",
            value=(
                "Read the server rules and channel permissions. ROOKIE community access alone does not "
                "qualify for premium stats or new Vault submissions. Member checks use the verified "
                "paid/trial or explicit grant ledger, not your HIGHROLLER role alone.\n"
                "Run `/mystats` privately to view your records. Tail tracking records interest at the "
                "capper's units; it does not place a wager or prove your personal betting results."
            ),
            inline=False,
        )
        embed.add_field(
            name="Missing access or checking expiration?",
            value=(
                "Connect this Discord account in Whop and allow up to five minutes for the next sync; "
                "delays or failures can take longer. Then rerun `/membership_status`.\n"
                "These checks do not return your plan or expiration date. Check your pass details in Whop. "
                "Paid passes and eligible trials do not automatically renew or convert to paid access.\n"
                f"If unresolved, email {SUPPORT_EMAIL} with your Discord ID and the affected feature. "
                "Do not post receipts, payment details, tokens or passwords in public channels."
            ),
            inline=False,
        )
        if not WHOP_MEMBERSHIP_SYNC_ENABLED:
            embed.add_field(
                name="Verification is not enabled",
                value="This bot cannot verify member access yet. Checkout remains closed; contact staff for launch updates.",
                inline=False,
            )
        embed.set_footer(text="Access is checked again when you use a protected feature.")
        await interaction.followup.send(
            embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="start", description="Privately view getting-started steps and your feature access")
    @app_commands.guild_only()
    async def start_command(self, interaction: discord.Interaction) -> None:
        await self.respond(interaction, getting_started=True)

    @app_commands.command(name="membership_status", description="Privately check feature access and get membership help")
    @app_commands.guild_only()
    async def membership_status_command(self, interaction: discord.Interaction) -> None:
        await self.respond(interaction, getting_started=False)
