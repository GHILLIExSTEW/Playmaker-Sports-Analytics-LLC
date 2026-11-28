from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time

import discord
from discord.ext import commands, tasks

from src.config import ERROR_LOG_CHANNEL_ID, GUILD_ID
from src.reporting import TRACKER_TIMEZONE

logger = logging.getLogger("official_play_bot")


class ErrorAlerts(commands.Cog):
    def __init__(self, bot: commands.Bot, resolve_channel) -> None:
        self.bot = bot
        self.resolve_channel = resolve_channel
        self.lock = asyncio.Lock()
        self.thread: discord.Thread | None = None
        self.thread_date: str | None = None

    def cog_unload(self) -> None:
        self.daily_thread.cancel()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if not self.daily_thread.is_running():
            self.daily_thread.start()
        try:
            await self.ensure_thread()
        except Exception:
            logger.exception("error_log_thread_startup_failed")

    @tasks.loop(time=time(hour=0, tzinfo=TRACKER_TIMEZONE))
    async def daily_thread(self) -> None:
        try:
            await self.ensure_thread()
        except Exception:
            logger.exception("error_log_daily_thread_failed")

    @daily_thread.before_loop
    async def before_daily_thread(self) -> None:
        await self.bot.wait_until_ready()

    async def ensure_thread(self) -> discord.Thread:
        async with self.lock:
            day = datetime.now(TRACKER_TIMEZONE).date().isoformat()
            if self.thread is not None and self.thread_date == day:
                if self.thread.locked:
                    raise RuntimeError("The daily error thread is locked.")
                if self.thread.archived:
                    self.thread = await self.thread.edit(archived=False, reason="Record staff alerts")
                return self.thread

            parent = await self.resolve_channel(ERROR_LOG_CHANNEL_ID, "ERROR_LOG_CHANNEL_ID", required=True)
            if isinstance(parent, discord.Thread):
                parent = await self.resolve_channel(parent.parent_id, "error thread parent", required=True)
            if not isinstance(parent, (discord.ForumChannel, discord.TextChannel)):
                raise RuntimeError("ERROR_LOG_CHANNEL_ID must be a forum, text channel, or their thread.")
            if parent.guild.id != GUILD_ID:
                raise RuntimeError("The error log channel must belong to the configured guild.")
            if self.bot.user is None:
                raise RuntimeError("The bot user is unavailable; cannot identify daily error threads.")
            name = f"Errors {day}"
            active = await parent.guild.active_threads()
            matches = {
                thread.id: thread for thread in active
                if thread.parent_id == parent.id and thread.name == name and thread.owner_id == self.bot.user.id
            }
            async for thread in parent.archived_threads(limit=None):
                if thread.name == name and thread.owner_id == self.bot.user.id:
                    matches[thread.id] = thread
            if len(matches) > 1:
                raise RuntimeError(f"Multiple daily error threads exist for {day}; reconcile before posting.")
            if matches:
                thread = next(iter(matches.values()))
                if thread.locked:
                    raise RuntimeError("The daily error thread is locked.")
                if thread.archived:
                    thread = await thread.edit(archived=False, reason="Record staff alerts")
            elif isinstance(parent, discord.ForumChannel):
                created = await parent.create_thread(
                    name=name, content=f"Staff alerts for {day} (Eastern time).",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                thread = created.thread
            else:
                message = await parent.send(
                    f"Staff alerts for {day} (Eastern time).",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                thread = await message.create_thread(name=name)
            self.thread, self.thread_date = thread, day
            return thread

    async def send(self, text: str) -> None:
        thread = await self.ensure_thread()
        try:
            await thread.send(f"⚠️ {text}"[:2000], allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            async with self.lock:
                if self.thread is thread:
                    self.thread, self.thread_date = None, None
            raise
