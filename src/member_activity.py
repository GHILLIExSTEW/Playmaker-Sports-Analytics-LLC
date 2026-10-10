from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands

from src.config import FREE_CHAT_CHANNEL_ID, HIGHROLLER_ROLE_ID, ROOKIE_ROLE_ID, VIP_CHAT_CHANNEL_ID
from src.reporting import MEDALS, TRACKER_TIMEZONE, clip_lines, record_line
from src.services.membership_service import MembershipService
from src.services.play_features_service import PlayFeaturesService, vault_leaderboard

logger = logging.getLogger("official_play_bot")
SHARE_CHANNELS = {
    "free": ("FREE CHAT", FREE_CHAT_CHANNEL_ID, ROOKIE_ROLE_ID, "ROOKIE"),
    "vip": ("VIP CHAT", VIP_CHAT_CHANNEL_ID, HIGHROLLER_ROLE_ID, "HIGHROLLER"),
}
SHARE_WEBHOOK_NAME = "Playmaker Stats Share"


def build_mystats_embed(member: discord.abc.User, stats: dict) -> discord.Embed:
    embed = discord.Embed(title=f"📈 Stats for {member.display_name}", color=discord.Color.blurple())
    capper = stats["capper"]
    if capper:
        sports = ", ".join(f"{row['sport']} {row['net']:+.2f}u" for row in capper["sports"][:3])
        embed.add_field(name="📣 Your Official Plays", value=(
            f"This month: {record_line(capper['month'])}\n"
            f"All-time: {record_line(capper['all_time'])}\n"
            f"Streak: **{capper['streak']}**" + (f"\nTop sports: {sports}" if sports else "")
        )[:1024], inline=False)
    tails = stats["tails"]
    tail_text = f"{record_line(tails)}\nOpen tails: {tails['open']}" if tails["count"] else "You haven't tailed any plays yet — tap 🎯 Tail under a play."
    embed.add_field(name="🎯 Plays You Tailed (at the capper's units)", value=tail_text, inline=False)
    vault = stats["vault"]
    vault_text = (
        f"This month: {record_line(vault['month'])}\nAll-time: {record_line(vault)}"
        if vault["count"] else "No settled vault bets yet."
    )
    embed.add_field(name="🏦 Your Vault", value=vault_text, inline=False)
    embed.set_footer(text="Eastern Time")
    return embed


def find_share_role(guild, role_id: int | None, role_name: str):
    if guild is None:
        return None
    role = guild.get_role(role_id) if role_id else None
    return role or next((role for role in guild.roles if role.name.lower() == role_name.lower()), None)


class ShareStatsView(discord.ui.View):
    """Publish the requesting member's private stats card once."""

    def __init__(self, activity: MemberActivity, owner_id: int, embed: discord.Embed, tier: str):
        super().__init__(timeout=600)
        self.activity = activity
        self.owner_id = owner_id
        self.embed = embed
        self.tier = tier
        self.share_lock = asyncio.Lock()
        label, channel_id, _, _ = SHARE_CHANNELS[tier]
        if channel_id:
            button = discord.ui.Button(label=f"Share to {label}", emoji="\U0001F4E3", style=discord.ButtonStyle.primary)
            button.callback = self.make_callback(label)
            self.add_item(button)

    def make_callback(self, label: str):
        async def callback(interaction: discord.Interaction) -> None:
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message("Only the member who ran /mystats can share it.", ephemeral=True)
                return
            await interaction.response.defer()
            async with self.share_lock:
                if self.is_finished():
                    await interaction.followup.send("This stats card is no longer available to share. Run /mystats again.", ephemeral=True)
                    return
                if self.tier == "vip":
                    try:
                        allowed = await self.activity.has_vip_chat_access(interaction.user)
                    except Exception:
                        logger.exception("mystats_share_access_failed user=%s", interaction.user.id)
                        await interaction.followup.send("Couldn't verify your membership right now. Please try again shortly.", ephemeral=True)
                        return
                    if not allowed:
                        await interaction.followup.send("Sharing to VIP CHAT is for HIGHROLLER members. Run /mystats again to share to FREE CHAT.", ephemeral=True)
                        return
                try:
                    await self.activity.post_shared_stats(interaction.user, self.embed, self.tier)
                except Exception:
                    logger.exception("mystats_share_failed user=%s tier=%s", interaction.user.id, self.tier)
                    await interaction.followup.send(f"Couldn't post to {label}. Please let a moderator know.", ephemeral=True)
                    return
                self.stop()
                try:
                    await interaction.delete_original_response()
                except discord.HTTPException:
                    logger.warning("mystats_share_cleanup_failed user=%s", interaction.user.id, exc_info=True)
        return callback


class MemberActivity(commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
        *,
        features: PlayFeaturesService,
        resolve_channel: Callable[..., Awaitable],
        membership: MembershipService | None = None,
    ) -> None:
        self.bot = bot
        self.features = features
        self.resolve_channel = resolve_channel
        self.membership = membership if membership is not None else MembershipService()
        self.share_webhooks: dict[int, discord.Webhook] = {}

    async def has_vip_chat_access(self, member) -> bool:
        # Whop owns HIGHROLLER for paid/trial users; explicit grants use the ledger.
        if HIGHROLLER_ROLE_ID and any(role.id == HIGHROLLER_ROLE_ID for role in getattr(member, "roles", [])):
            return True
        return await asyncio.to_thread(self.membership.has_paid_access, member.id)

    async def get_share_webhook(self, channel) -> discord.Webhook:
        cached = self.share_webhooks.get(channel.id)
        if cached:
            return cached
        webhook = next(
            (hook for hook in await channel.webhooks()
             if hook.name == SHARE_WEBHOOK_NAME and hook.user and self.bot.user
             and hook.user.id == self.bot.user.id and hook.token),
            None,
        )
        if webhook is None:
            webhook = await channel.create_webhook(name=SHARE_WEBHOOK_NAME, reason="Members sharing /mystats")
        self.share_webhooks[channel.id] = webhook
        return webhook

    async def post_shared_stats(self, member, embed: discord.Embed, tier: str) -> discord.Message:
        label, channel_id, role_id, role_name = SHARE_CHANNELS[tier]
        channel = await self.resolve_channel(channel_id, label, required=True)
        thread = channel if isinstance(channel, discord.Thread) else discord.utils.MISSING
        webhook = await self.get_share_webhook(channel.parent if isinstance(channel, discord.Thread) else channel)
        role = find_share_role(getattr(channel, "guild", None), role_id, role_name)
        if role is None:
            logger.warning("mystats_share_role_missing tier=%s role=%s", tier, role_id or role_name)
        try:
            return await webhook.send(
                content=f"{role.mention} \U0001F4CA My stats" if role else "\U0001F4CA My stats",
                embed=embed,
                username=member.display_name,
                avatar_url=member.display_avatar.url,
                allowed_mentions=discord.AllowedMentions(everyone=False, users=False, roles=[role] if role else False),
                thread=thread,
                wait=True,
            )
        except discord.NotFound:
            self.share_webhooks.pop(webhook.channel_id, None)
            raise

    @app_commands.command(name="mystats", description="Your official play, tail, and vault records")
    async def mystats_command(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            stats = await asyncio.to_thread(self.features.personal_stats, interaction.user.id)
            embed = build_mystats_embed(interaction.user, stats)
        except Exception as exc:
            logger.exception("mystats_failed user=%s", interaction.user.id)
            await interaction.followup.send(f"Could not load your stats: {exc}", ephemeral=True)
            return
        try:
            vip = await self.has_vip_chat_access(interaction.user)
        except Exception:
            logger.warning("mystats_membership_lookup_failed user=%s", interaction.user.id, exc_info=True)
            await interaction.followup.send(
                "Your stats are available, but sharing is temporarily unavailable because membership "
                "couldn't be verified. Run /mystats again shortly.",
                embed=embed, ephemeral=True,
            )
            return
        view = ShareStatsView(self, interaction.user.id, embed, "vip" if vip else "free")
        if view.children:
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="vault_leaderboard", description="This month's top member vault records")
    async def vault_leaderboard_command(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        now = datetime.now(TRACKER_TIMEZONE)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        try:
            bets = await asyncio.to_thread(self.features.vault_bets)
            board = vault_leaderboard(bets, month_start, now + timedelta(seconds=1), limit=10)
            embed = discord.Embed(title=f"🏦 Vault Leaderboard — {now.strftime('%B')} {now.year}", color=discord.Color.gold())
            embed.description = clip_lines([
                f"{MEDALS[index] if index < 3 else f'{index + 1}.'} **{row['name']}** — {record_line(row)}"
                for index, row in enumerate(board)
            ], 4000) if board else "No settled vault bets this month yet."
            embed.set_footer(text="Ranked by net units • Eastern Time")
        except Exception as exc:
            logger.exception("vault_leaderboard_failed")
            await interaction.followup.send(f"Could not load the leaderboard: {exc}", ephemeral=True)
            return
        await interaction.followup.send(embed=embed)
