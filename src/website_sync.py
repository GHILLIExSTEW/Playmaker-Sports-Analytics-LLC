from __future__ import annotations

import asyncio
import logging
import re
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
_INSIGHT_REPLY_PREFIX = re.compile(r"^\s*(?:play\s*)?#?(\d+)\s*[:\-]\s*(.*?)\s*$", re.IGNORECASE | re.DOTALL)
_INSIGHT_PLAY_ID = re.compile(r"\bPlay\s+#(\d+)\b", re.IGNORECASE)


class WebsiteSync(commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
        *,
        tracked_operators: Callable[[], Awaitable[set[int] | None]],
        can_manage_plays: Callable[[discord.abc.User, discord.Guild | None], bool],
        staff_alert: Callable[[str, str], Awaitable[None]],
    ) -> None:
        self.bot = bot
        self.tracked_operators = tracked_operators
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

    async def handle_insight_message(self, message: discord.Message) -> bool:
        if message.guild is not None or message.author.bot:
            return False

        content = message.content.strip()
        reference = getattr(message, "reference", None)
        reference_id = getattr(reference, "message_id", None)
        referenced_message = getattr(message, "referenced_message", None)
        if referenced_message is None and reference is not None:
            referenced_message = getattr(reference, "resolved", None)
        prompt_match = _INSIGHT_PLAY_ID.search(getattr(referenced_message, "content", "") or "")
        prefixed_match = _INSIGHT_REPLY_PREFIX.match(content)
        if prefixed_match:
            play_id = int(prefixed_match.group(1))
            justification = prefixed_match.group(2)
        elif prompt_match:
            play_id = int(prompt_match.group(1))
            justification = content
        else:
            await message.channel.send(
                "To submit your justification, reply to the Playmaker DM for that pick, or send "
                "`#PLAY_ID: your reasoning` (for example, `#630: ...`)."
            )
            return True

        try:
            async with self.insight_prompt_lock:
                rows = await asyncio.to_thread(capper_insight_service.insight_context, play_id)
                if not rows or not rows[0].get("prompt_message_id"):
                    await message.channel.send(f"There is no active justification request for Play #{play_id}.")
                    return True
                context = rows[0]
                if prompt_match and not prefixed_match and str(reference_id) != str(context["prompt_message_id"]):
                    await message.channel.send("Reply to the original Playmaker request DM for that pick.")
                    return True
                if str(context["discord_user_id"]) != str(message.author.id):
                    await message.channel.send("Only the original author can submit this pick's justification.")
                    return True
                await asyncio.to_thread(
                    capper_insight_service.save_insight, play_id, message.author.id, justification,
                )
        except ValueError as exc:
            await message.channel.send(str(exc))
            return True
        except Exception:
            logger.exception("capper_insight_save_failed play=%s user=%s", play_id, message.author.id)
            await message.channel.send(
                f"Your justification for Play #{play_id} was not saved. Please try again later."
            )
            return True

        await message.channel.send(
            f"Your justification for Play #{play_id} was saved. Website members can reveal it after refreshing picks."
        )
        return True

    async def send_insight_request(self, play_id: int) -> bool:
        async with self.insight_prompt_lock:
            rows = await asyncio.to_thread(capper_insight_service.insight_context, play_id)
            if not rows or rows[0]["prompt_message_id"] or rows[0]["justification"]:
                return False
            owner_id = int(rows[0]["discord_user_id"])
            user = self.bot.get_user(owner_id)
            if user is None:
                user = await self.bot.fetch_user(owner_id)
            message = await user.send(
                f"Your written justification is required for Play #{play_id} "
                f"({rows[0]['selection'][:200]}). Reply directly to this DM with your reasoning. "
                f"If replying directly is unavailable, send `#{play_id}: your reasoning`. "
                "Your insight will be shown to authorized website members."
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
