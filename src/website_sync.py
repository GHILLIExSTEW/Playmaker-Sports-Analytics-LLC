from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import discord
from discord import app_commands
from discord.ext import commands, tasks

from src.config import GUILD_ID, TRACKER_ROLE_ID
from src.services import capper_insight_service
from src.services.website_capper_service import (
    WEBSITE_OWNER_ROLE_ID,
    sync_website_capper_roster,
    sync_website_owner_roster,
)

logger = logging.getLogger("official_play_bot")


def can_submit_insight(interaction: discord.Interaction, owner_id: int) -> bool:
    return (
        interaction.user.id == owner_id
        and interaction.guild is not None
        and interaction.guild.id == GUILD_ID
        and any(role.id == TRACKER_ROLE_ID for role in getattr(interaction.user, "roles", []))
    )


class CapperInsightModal(discord.ui.Modal, title="Add capper insight"):
    justification = discord.ui.TextInput(
        label="Why are you taking this pick?",
        style=discord.TextStyle.paragraph, required=True, max_length=2000,
        placeholder="Your reasoning will be shown to authorized website members.",
    )

    def __init__(self, play_id: int, owner_id: int, current: str):
        super().__init__()
        self.play_id = play_id
        self.owner_id = owner_id
        self.justification.default = current

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not can_submit_insight(interaction, self.owner_id):
            await interaction.response.send_message(
                "Only this pick's original author, while holding OPERATOR, can submit insight.", ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await asyncio.to_thread(
                capper_insight_service.save_insight, self.play_id, interaction.user.id, self.justification.value,
            )
        except Exception:
            logger.exception("capper_insight_save_failed play=%s user=%s", self.play_id, interaction.user.id)
            await interaction.followup.send(
                "Insight was not saved. Check that the pick is still open and your OPERATOR role is current, then retry.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            f"Insight saved for Play #{self.play_id}. Website members can reveal it after refreshing picks.",
            ephemeral=True,
        )


class WebsiteSync(commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
        *,
        tracked_operators: Callable[[], Awaitable[set[int] | None]],
        play_post_target: Callable[[], tuple[str, int | None]],
        resolve_channel: Callable[..., Awaitable],
        can_manage_plays: Callable[[discord.abc.User, discord.Guild | None], bool],
        staff_alert: Callable[[str, str], Awaitable[None]],
    ) -> None:
        self.bot = bot
        self.tracked_operators = tracked_operators
        self.play_post_target = play_post_target
        self.resolve_channel = resolve_channel
        self.can_manage_plays = can_manage_plays
        self.staff_alert = staff_alert
        self.insight_prompt_lock = asyncio.Lock()

    async def cog_load(self) -> None:
        if self.bot.is_ready():
            self.start_jobs()

    def cog_unload(self) -> None:
        self.website_capper_sync.cancel()

    def start_jobs(self) -> None:
        if not self.website_capper_sync.is_running():
            self.website_capper_sync.start()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        self.start_jobs()

    @tasks.loop(minutes=5)
    async def website_capper_sync(self) -> None:
        try:
            if not GUILD_ID or not TRACKER_ROLE_ID:
                raise RuntimeError("Website capper sync requires GUILD_ID and TRACKER_ROLE_ID.")
            member_ids = await self.tracked_operators()
            if member_ids is None:
                raise RuntimeError("Cannot verify the Discord OPERATOR roster.")
            await asyncio.to_thread(sync_website_capper_roster, GUILD_ID, TRACKER_ROLE_ID, member_ids)
            logger.info("website_capper_sync_complete members=%s", len(member_ids))
        except Exception as exc:
            logger.exception("website_capper_sync_failed")
            await self.staff_alert("website_cappers", f"Website capper roster sync failed: `{str(exc)[:1500]}`")
        try:
            guild = self.bot.get_guild(GUILD_ID) if GUILD_ID else None
            if guild is None:
                raise RuntimeError("Cannot verify Owner roster: configured guild is unavailable.")
            if not guild.chunked:
                await guild.chunk(cache=True)
            role = guild.get_role(WEBSITE_OWNER_ROLE_ID)
            if role is None:
                raise RuntimeError("Configured website Owner role is missing.")
            owner_ids = {member.id for member in role.members}
            await asyncio.to_thread(sync_website_owner_roster, guild.id, owner_ids)
            logger.info("website_owner_sync_complete members=%s", len(owner_ids))
        except Exception as exc:
            logger.exception("website_owner_sync_failed")
            await self.staff_alert("website_owners", f"Website Owner roster sync failed: `{str(exc)[:1500]}`")

    @website_capper_sync.before_loop
    async def before_website_capper_sync(self) -> None:
        await self.bot.wait_until_ready()

    async def handle_insight_click(self, interaction: discord.Interaction, play_id: int) -> None:
        rows = await asyncio.to_thread(capper_insight_service.insight_context, play_id)
        if not rows:
            await interaction.response.send_message(
                "This pick is no longer open or its author is no longer a current OPERATOR.", ephemeral=True,
            )
            return
        context = rows[0]
        owner_id = int(context["discord_user_id"])
        if not can_submit_insight(interaction, owner_id):
            await interaction.response.send_message(
                "Only this pick's original author, while holding OPERATOR, can add or edit its insight.", ephemeral=True,
            )
            return
        await interaction.response.send_modal(CapperInsightModal(play_id, owner_id, context["justification"]))

    @commands.Cog.listener("on_interaction")
    async def on_insight_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type != discord.InteractionType.component:
            return
        custom_id = str((interaction.data or {}).get("custom_id") or "")
        if not custom_id.startswith("pm:insight:"):
            return
        try:
            parts = custom_id.split(":")
            if len(parts) != 3 or not parts[2].isdecimal() or int(parts[2]) <= 0:
                raise ValueError("Invalid insight button ID.")
            await self.handle_insight_click(interaction, int(parts[2]))
        except Exception:
            logger.exception("capper_insight_interaction_failed custom_id=%s user=%s", custom_id, interaction.user.id)
            try:
                if interaction.response.is_done():
                    await interaction.followup.send("Something went wrong. Please try again.", ephemeral=True)
                else:
                    await interaction.response.send_message("Something went wrong. Please try again.", ephemeral=True)
            except discord.HTTPException:
                logger.warning("capper_insight_error_response_failed user=%s", interaction.user.id, exc_info=True)

    async def send_insight_request(self, play_id: int) -> bool:
        async with self.insight_prompt_lock:
            rows = await asyncio.to_thread(capper_insight_service.insight_context, play_id)
            if not rows or rows[0]["prompt_message_id"] or rows[0]["justification"]:
                return False
            name, channel_id = self.play_post_target()
            channel = await self.resolve_channel(channel_id, name, required=True)
            if channel is None:
                raise RuntimeError("Insight confirmation channel is unavailable.")
            owner_id = int(rows[0]["discord_user_id"])
            view = discord.ui.View(timeout=None)
            view.add_item(discord.ui.Button(
                label="Add / edit insight", style=discord.ButtonStyle.secondary, custom_id=f"pm:insight:{play_id}",
            ))
            view.stop()
            message = await channel.send(
                f"<@{owner_id}> Please add your justification for Play #{play_id} "
                f"({rows[0]['selection'][:200]}). Only you can use this button. "
                "The form is private; your insight will be published to authorized website members.",
                view=view, allowed_mentions=discord.AllowedMentions(
                    users=[discord.Object(id=owner_id)], roles=False, everyone=False,
                ),
            )
            await asyncio.to_thread(capper_insight_service.mark_prompt, play_id, message.id)
            return True

    @app_commands.command(name="request_insights", description="Request author insight for up to 50 open picks missing a request")
    async def request_insights_command(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or interaction.guild.id != GUILD_ID or not self.can_manage_plays(interaction.user, interaction.guild):
            await interaction.response.send_message("Only play managers in the configured server can request insights.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        sent = 0
        try:
            rows = await asyncio.to_thread(capper_insight_service.insight_context)
            for row in rows:
                sent += int(await self.send_insight_request(int(row["play_id"])))
        except Exception:
            logger.exception("capper_insight_backfill_failed sent=%s", sent)
            await interaction.followup.send(
                f"Sent {sent} requests, then stopped because a request failed. Check bot logs before retrying.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            f"Sent {sent} insight requests. Run again for remaining picks if this batch reached 50.", ephemeral=True,
        )
