from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, time, timedelta, timezone

from discord.ext import commands, tasks

from src.reporting import TRACKER_TIMEZONE
from src.services.data_model_service import COMPETITIONS
from src.services.model_projection_service import ModelProjectionService

logger = logging.getLogger("official_play_bot")
BACKTEST_TIME = time(hour=6, minute=45, tzinfo=TRACKER_TIMEZONE)
FOOTBALL = {"nfl", "ncaa:2"}
# Football odds are one provider request per game, so they refresh less often than league-wide odds.
INTERVALS = {
    "odds": timedelta(hours=1),
    "football_odds": timedelta(hours=6),
    "injuries": timedelta(hours=6),
    "weather": timedelta(hours=3),
}
BACKTEST_RETRY = timedelta(hours=12)
ALERT_INTERVAL = timedelta(hours=6)


class ModelProjections(commands.Cog):
    """Keeps stored engine projections, inputs and backtests current for the site and Discord."""

    def __init__(self, bot: commands.Bot, *, service: ModelProjectionService, enabled: bool,
                 staff_alert: Callable[[str, str], Awaitable[None]], clock=None) -> None:
        self.bot = bot
        self.service = service
        self.enabled = enabled
        self.staff_alert = staff_alert
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.last: dict[tuple[str, str], datetime] = {}
        self.alerted: dict[str, datetime] = {}

    async def cog_load(self) -> None:
        if self.bot.is_ready():
            self.start_jobs()

    def cog_unload(self) -> None:
        self.refresh_projections.cancel()
        self.daily_backtests.cancel()

    def start_jobs(self) -> None:
        if not self.enabled:
            return
        for loop in (self.refresh_projections, self.daily_backtests):
            if not loop.is_running():
                loop.start()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        self.start_jobs()

    async def report_failure(self, job: str) -> None:
        logger.exception("%s_failed", job)
        now = self.clock()
        if job in self.alerted and now - self.alerted[job] < ALERT_INTERVAL:
            return
        self.alerted[job] = now
        try:
            await self.staff_alert(job, f"Model job `{job}` failed. Check bot logs before retrying.")
        except Exception:
            logger.exception("model_projection_staff_alert_failed job=%s", job)

    def _due(self, competition: str, name: str, now: datetime) -> bool:
        interval = INTERVALS["football_odds" if name == "odds" and competition in FOOTBALL else name]
        last = self.last.get((competition, name))
        return last is None or now - last >= interval

    async def refresh_competition(self, competition: str) -> dict:
        now = self.clock()
        attempted = self.last.get((competition, "backtest"))
        if (attempted is None or now - attempted >= BACKTEST_RETRY) and \
                await asyncio.to_thread(self.service.stored_backtest, competition) is None:
            self.last[(competition, "backtest")] = now
            try:
                await asyncio.to_thread(self.service.run_backtest, competition)
            except Exception:
                await self.report_failure(f"model_backtest:{competition}")
        due = {name: self._due(competition, name, now) for name in ("odds", "injuries", "weather")}
        result = await asyncio.to_thread(self.service.refresh, competition, **due)
        for name, ran in due.items():
            if ran and not str(result.get(name, "")).startswith("failed"):
                self.last[(competition, name)] = now
        return result

    @tasks.loop(minutes=30)
    async def refresh_projections(self) -> None:
        for competition in COMPETITIONS:
            try:
                result = await self.refresh_competition(competition)
                logger.info("model_projections_refreshed competition=%s result=%s", competition, result)
            except Exception:
                await self.report_failure(f"model_projections:{competition}")

    @tasks.loop(time=BACKTEST_TIME)
    async def daily_backtests(self) -> None:
        for competition in COMPETITIONS:
            try:
                report = await asyncio.to_thread(self.service.run_backtest, competition)
                logger.info("model_backtest competition=%s status=%s games=%s", competition,
                            report.get("status"), report.get("games"))
            except Exception:
                await self.report_failure(f"model_backtest:{competition}")

    @refresh_projections.before_loop
    @daily_backtests.before_loop
    async def before_jobs(self) -> None:
        await self.bot.wait_until_ready()
