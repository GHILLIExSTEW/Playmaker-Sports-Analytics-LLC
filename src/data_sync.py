from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import time

from discord.ext import commands, tasks

from src.reporting import TRACKER_TIMEZONE
from src.services.api_sports_multi_service import ApiSportsMultiService
from src.services.api_sports_service import ApiSportsService

logger = logging.getLogger("official_play_bot")
API_SPORTS_DAILY_SYNC_TIME = time(hour=6, minute=10, tzinfo=TRACKER_TIMEZONE)
API_SPORTS_MULTI_DAILY_SYNC_TIME = time(hour=6, minute=25, tzinfo=TRACKER_TIMEZONE)


class DataSync(commands.Cog):
    def __init__(
        self, bot: commands.Bot, *, nfl: ApiSportsService, multi: ApiSportsMultiService,
        enabled: bool, staff_alert: Callable[[str, str], Awaitable[None]],
    ) -> None:
        self.bot = bot
        self.nfl, self.multi = nfl, multi
        self.enabled = enabled
        self.staff_alert = staff_alert
        self.initial_tasks: dict[str, asyncio.Task[None]] = {}

    async def cog_load(self) -> None:
        if self.bot.is_ready():
            self.start_jobs()

    def cog_unload(self) -> None:
        for loop in (
            self.daily_nfl_data_sync, self.live_nfl_score_sync,
            self.daily_multi_sport_api_sync, self.live_multi_sport_api_sync,
        ):
            loop.cancel()
        for task in self.initial_tasks.values():
            task.cancel()

    def start_jobs(self) -> None:
        if not self.enabled:
            return
        for loop in (
            self.daily_nfl_data_sync, self.live_nfl_score_sync,
            self.daily_multi_sport_api_sync, self.live_multi_sport_api_sync,
        ):
            if not loop.is_running():
                loop.start()
        if "nfl" not in self.initial_tasks:
            self.initial_tasks["nfl"] = asyncio.create_task(self.initial_nfl_data_sync(), name="initial_nfl_data_sync")
        if "multi" not in self.initial_tasks:
            self.initial_tasks["multi"] = asyncio.create_task(self.initial_multi_sport_api_sync(), name="initial_multi_sport_api_sync")

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        self.start_jobs()

    async def report_failure(self, job: str) -> None:
        logger.exception("%s_failed", job)
        try:
            await self.staff_alert(job, f"Sports-data job `{job}` failed. Check bot logs before retrying.")
        except Exception:
            logger.exception("data_sync_staff_alert_failed job=%s", job)

    @tasks.loop(time=API_SPORTS_DAILY_SYNC_TIME)
    async def daily_nfl_data_sync(self) -> None:
        try:
            result = await asyncio.to_thread(self.nfl.sync_daily)
            logger.info("api_sports_daily_sync_complete result=%s", result)
        except Exception:
            await self.report_failure("api_sports_daily_sync")

    @tasks.loop(minutes=15)
    async def live_nfl_score_sync(self) -> None:
        try:
            if not await asyncio.to_thread(self.nfl.should_sync_live_scores):
                return
            result = await asyncio.to_thread(self.nfl.sync_live_scores)
            logger.info("api_sports_live_score_sync_complete result=%s", result)
        except Exception:
            await self.report_failure("api_sports_live_score_sync")

    @tasks.loop(time=API_SPORTS_MULTI_DAILY_SYNC_TIME)
    async def daily_multi_sport_api_sync(self) -> None:
        try:
            result = await asyncio.to_thread(self.multi.sync_daily)
            logger.info("api_sports_multi_daily_sync_complete result=%s", result)
        except Exception:
            await self.report_failure("api_sports_multi_daily_sync")

    @tasks.loop(minutes=15)
    async def live_multi_sport_api_sync(self) -> None:
        try:
            sports = await asyncio.to_thread(self.multi.active_sports)
            if not sports:
                return
            result = await asyncio.to_thread(self.multi.sync_live_scores, sports)
            logger.info("api_sports_multi_live_sync_complete result=%s", result)
        except Exception:
            await self.report_failure("api_sports_multi_live_sync")

    @daily_nfl_data_sync.before_loop
    @live_nfl_score_sync.before_loop
    @daily_multi_sport_api_sync.before_loop
    @live_multi_sport_api_sync.before_loop
    async def before_data_sync(self) -> None:
        await self.bot.wait_until_ready()

    async def initial_nfl_data_sync(self) -> None:
        try:
            result = await asyncio.to_thread(self.nfl.sync_daily)
            logger.info("api_sports_initial_sync_complete result=%s", result)
        except Exception:
            await self.report_failure("api_sports_initial_sync")

    async def initial_multi_sport_api_sync(self) -> None:
        try:
            result = await asyncio.to_thread(self.multi.sync_daily)
            logger.info("api_sports_multi_initial_sync_complete result=%s", result)
        except Exception:
            await self.report_failure("api_sports_multi_initial_sync")
