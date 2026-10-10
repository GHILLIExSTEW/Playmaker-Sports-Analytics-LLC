from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timedelta, timezone
from io import BytesIO
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

from src.config import GUILD_ID, RESULT_CHANNEL_ID, TEAM_STATS_CHANNEL_ID
from src.results import ResultsView, filter_results, parse_results_dates
from src.datetime_utils import parse_iso_datetime
from src.services.settlement_service import SettlementService
from src.services.supabase_service import supabase_service
from src.services.tracker_image_service import render_tracker_image
from src.services.play_features_service import (
    PlayFeaturesService,
    build_recap,
    format_record,
    previous_month,
    previous_week,
    vault_leaderboard,
    SPORT_NAMES,
)
from src.services.team_ranking_service import TeamRankingService

logger = logging.getLogger("official_play_bot")
TRACKER_TIMEZONE = ZoneInfo("America/New_York")
TRACKER_UPDATE_TIMES = [time(hour=hour, tzinfo=TRACKER_TIMEZONE) for hour in range(24)]
RECAP_TIME = time(hour=10, tzinfo=TRACKER_TIMEZONE)
MEDALS = ["🥇", "🥈", "🥉"]


def fetch_official_tracker_rows() -> tuple[list[dict], list[dict]]:
    client = supabase_service._ensure_client()

    def fetch(table: str, columns: str) -> list[dict]:
        rows = []
        offset = 0
        while True:
            batch = client.table(table).select(columns).range(offset, offset + 999).execute().data or []
            rows.extend(batch)
            if len(batch) < 1000:
                return rows
            offset += 1000

    return (
        fetch("plays", "id,user_id,units,odds,status,message_id,created_at,settled_at"),
        fetch("users", "id,discord_user_id,display_name,username"),
    )


def parse_tracker_time(value: str, timezone_name: str) -> datetime:
    parsed = parse_iso_datetime(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(ZoneInfo(timezone_name))


def build_official_tracker_embed(
    plays: list[dict], users: list[dict], now: datetime | None = None, cutoff: date | None = None,
) -> tuple[discord.Embed, list[str], str]:
    timezone_name = TRACKER_TIMEZONE.key
    now = now or datetime.now(TRACKER_TIMEZONE)
    now = now.replace(tzinfo=TRACKER_TIMEZONE) if now.tzinfo is None else now.astimezone(TRACKER_TIMEZONE)
    today = now.date()
    linked_plays = {}
    for play in sorted(plays, key=lambda row: int(row["id"])):
        if play.get("message_id"):
            linked_plays.setdefault(str(play["message_id"]), play)
    plays = list(linked_plays.values())
    settled_statuses = {"win", "loss", "void", "partial"}
    settled = [play for play in plays if play.get("status") in settled_statuses]
    pending = [play for play in plays if play.get("status") not in settled_statuses]

    def signed(play: dict) -> float:
        return SettlementService.tally_for_result(play["status"], float(play["units"]), play.get("odds"))

    def settled_time(play: dict) -> datetime:
        return parse_tracker_time(play.get("settled_at") or play["created_at"], timezone_name)

    def in_period(play: dict, start: datetime) -> bool:
        return start <= settled_time(play) <= now

    if cutoff is not None:
        cutoff_start = datetime.combine(cutoff, time.min, tzinfo=TRACKER_TIMEZONE)
        settled = [play for play in settled if settled_time(play) >= cutoff_start]
        pending = [play for play in pending if settled_time(play) >= cutoff_start]

    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    year_start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)

    def net(rows: list[dict]) -> float:
        return sum(signed(play) for play in rows)

    by_user = {}
    names = {str(user["id"]): user.get("display_name") or user.get("username") for user in users}
    for play in settled:
        if not in_period(play, month_start):
            continue
        user_id = str(play["user_id"])
        bucket = by_user.setdefault(user_id, {"wins": 0.0, "losses": 0.0, "win_count": 0, "loss_count": 0, "risked": 0.0})
        units = float(play["units"])
        if play.get("status") == "win":
            bucket["wins"] += signed(play)
            bucket["win_count"] += 1
            bucket["risked"] += units
        elif play.get("status") == "loss":
            bucket["losses"] += units
            bucket["loss_count"] += 1
            bucket["risked"] += units
    ranked = sorted(
        (item for item in by_user.items() if item[1]["win_count"] + item[1]["loss_count"]),
        key=lambda item: item[1]["wins"] - item[1]["losses"], reverse=True,
    )
    top_lines, breakdown = [], []
    for index, (user_id, data) in enumerate(ranked):
        total = data["win_count"] + data["loss_count"]
        name = names.get(user_id, user_id)
        rate = data["win_count"] / total * 100 if total else 0
        net_units = data["wins"] - data["losses"]
        breakdown.append(f"**{name}** · {data['win_count']}-{data['loss_count']} · {net_units:+.2f}u")
        if index < 3:
            top_lines.append(f"{MEDALS[index]} **{name}** — **{net_units:+g} units**\n{data['win_count']}-{data['loss_count']} record | {rate:.0f}% win rate")

    report_date = f"{today.strftime('%B')} {today.day}, {today.year}"
    embed = discord.Embed(title="Playmaker Picks | Unit Summary", description=f"Results for **{report_date}**", color=discord.Color.green())
    embed.add_field(name="⏳ Pending Bets", value=f"{len(pending)} {'bet' if len(pending) == 1 else 'bets'}", inline=True)
    embed.add_field(name="📅 Monthly Units", value=f"{net([play for play in settled if in_period(play, month_start)]):+g}u", inline=True)
    embed.add_field(name="🗓️ Yearly Units", value=f"{net([play for play in settled if in_period(play, year_start)]):+g}u", inline=True)
    embed.set_footer(text="Auto-updates hourly • Eastern Time")
    return embed, top_lines, "\n\n".join(breakdown)


def operator_plays(plays: list[dict], users: list[dict], operator_ids: set[int] | None) -> list[dict]:
    if operator_ids is None:
        return plays
    allowed = {
        str(user["id"]) for user in users
        if str(user.get("discord_user_id") or "").isdigit() and int(user["discord_user_id"]) in operator_ids
    }
    return [play for play in plays if str(play.get("user_id")) in allowed]


def record_line(summary: dict) -> str:
    return f"{format_record(summary)} · **{summary['net']:+.2f}u** · {summary['roi']:+.1f}% ROI"


def clip_lines(lines: list[str], limit: int = 1024) -> str:
    text = ""
    for line in lines:
        if len(text) + len(line) + 1 > limit:
            break
        text += line + "\n"
    return text.strip() or "—"


def build_recap_embed(
    service: PlayFeaturesService,
    period: str,
    now: datetime | None = None,
    operator_ids: set[int] | None = None,
) -> discord.Embed:
    """Build the last completed period's recap; database access is blocking."""
    now = now or datetime.now(TRACKER_TIMEZONE)
    start, end = previous_week(now) if period == "weekly" else previous_month(now)
    plays, users, sports = service.history()
    recap = build_recap(operator_plays(plays, users, operator_ids), users, sports, start, end)
    last_day = end - timedelta(days=1)
    label = (
        f"{start.strftime('%b')} {start.day} – {last_day.strftime('%b')} {last_day.day}, {last_day.year}"
        if period == "weekly" else f"{start.strftime('%B')} {start.year}"
    )
    embed = discord.Embed(title=f"📊 {period.title()} Recap", color=discord.Color.green())
    if not recap["count"]:
        embed.description = f"**{label}**\nNo settled plays this period."
    else:
        embed.description = f"**{label}**\n{recap['count']} settled plays · {record_line(recap['total'])}"
        embed.add_field(name="🏆 Cappers", value=clip_lines([
            f"{MEDALS[index] if index < 3 else f'{index + 1}.'} **{row['name']}** — {record_line(row)} · Streak {row['streak']}"
            for index, row in enumerate(recap["cappers"])
        ]), inline=False)
        embed.add_field(name="🏟️ By Sport", value=clip_lines([
            f"**{row['sport']}** — {record_line(row)}" for row in recap["sports"]
        ]), inline=False)
        best = recap["best_play"]
        if best:
            embed.add_field(
                name="⭐ Best Play",
                value=f"Play #{best['id']} by **{best['name']}** — {best['units']:+.2f}u at {int(best['odds']):+d}",
                inline=False,
            )
    if period == "monthly":
        board = vault_leaderboard(service.vault_bets(), start, end, limit=5)
        if board:
            embed.add_field(name="🏦 Vault Leaders", value=clip_lines([
                f"{MEDALS[index] if index < 3 else f'{index + 1}.'} **{row['name']}** — {record_line(row)}"
                for index, row in enumerate(board)
            ]), inline=False)
    embed.set_footer(text="Eastern Time • Official plays count once per tracked post")
    return embed


class Reporting(commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
        *,
        features: PlayFeaturesService,
        rankings: TeamRankingService,
        get_tracker_start: Callable[[], date | None],
        settlement_channels: Callable[[], list[tuple[str, int | None]]],
        reconcile_reactions: Callable[[list[dict], list[dict], list], Awaitable[int]],
        tracked_operators: Callable[[], Awaitable[set[int] | None]],
        resolve_channel: Callable[..., Awaitable],
        can_manage_plays: Callable[[discord.abc.User, discord.Guild | None], bool],
        staff_alert: Callable[[str, str], Awaitable[None]],
    ) -> None:
        self.bot = bot
        self.features = features
        self.rankings = rankings
        self.get_tracker_start = get_tracker_start
        self.settlement_channels = settlement_channels
        self.reconcile_reactions = reconcile_reactions
        self.tracked_operators = tracked_operators
        self.resolve_channel = resolve_channel
        self.can_manage_plays = can_manage_plays
        self.staff_alert = staff_alert

    def tracker_rows(self) -> tuple[list[dict], list[dict]]:
        return fetch_official_tracker_rows()

    def tracker_embed(self, plays: list[dict], users: list[dict]) -> tuple[discord.Embed, list[str], str]:
        return build_official_tracker_embed(plays, users, cutoff=self.get_tracker_start())

    async def update_or_post_tracker_embed(self, channel, embed: discord.Embed, image: BytesIO | None = None) -> None:
        image_embed = embed
        image_file = None
        try:
            if image is not None:
                image_file = discord.File(image, filename="unit-summary.png")
                image_embed = discord.Embed(title=embed.title, color=embed.color, description=embed.description)
                for field in embed.fields:
                    image_embed.add_field(name=field.name, value=field.value, inline=field.inline)
                if embed.footer.text:
                    image_embed.set_footer(text=embed.footer.text)
                image_embed.set_image(url="attachment://unit-summary.png")
            async for message in channel.history(limit=50):
                if message.author == self.bot.user and message.embeds and message.embeds[0].title == embed.title:
                    if image_file:
                        await message.edit(embed=image_embed, attachments=[image_file])
                    else:
                        await message.edit(embed=embed)
                    return
            if image_file:
                await channel.send(embed=image_embed, file=image_file)
            else:
                await channel.send(embed=embed)
        finally:
            if image_file is not None:
                image_file.close()

    async def refresh_tracker(self) -> int:
        if not RESULT_CHANNEL_ID:
            raise RuntimeError("RESULT_CHANNEL_ID is not configured.")
        plays, users = await asyncio.to_thread(self.tracker_rows)
        reaction_channels = []
        for setting_name, channel_id in dict.fromkeys(self.settlement_channels()):
            channel = await self.resolve_channel(channel_id, setting_name)
            if channel is not None:
                reaction_channels.append(channel)
        reconciled = await self.reconcile_reactions(plays, users, reaction_channels)
        if reconciled:
            plays, users = await asyncio.to_thread(self.tracker_rows)
        tracker_embed, top_lines, breakdown = self.tracker_embed(
            operator_plays(plays, users, await self.tracked_operators()), users,
        )
        tracker_channel = await self.resolve_channel(RESULT_CHANNEL_ID, "RESULT_CHANNEL_ID", required=True)
        tracker_image = await asyncio.to_thread(
            render_tracker_image, tracker_embed.description or "",
            [(field.name, field.value) for field in tracker_embed.fields]
            + [("🏆 Monthly Playmaker Breakdown", breakdown or "No settled plays yet.")],
            tracker_embed.footer.text or "",
        )
        await self.update_or_post_tracker_embed(tracker_channel, tracker_embed, tracker_image)
        team_channel = await self.resolve_channel(TEAM_STATS_CHANNEL_ID, "TEAM_STATS_CHANNEL_ID")
        if team_channel is not None:
            top_embed = discord.Embed(title="Top Playmakers", color=discord.Color.gold())
            top_embed.description = "\n\n".join(top_lines) or "No settled results yet."
            await self.update_or_post_tracker_embed(team_channel, top_embed)
        return reconciled

    async def cog_load(self) -> None:
        if self.bot.is_ready():
            self.start_jobs()

    def cog_unload(self) -> None:
        self.hourly_tracker_update.cancel()
        self.scheduled_recaps.cancel()

    def start_jobs(self) -> None:
        for job in (self.hourly_tracker_update, self.scheduled_recaps):
            if not job.is_running():
                job.start()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        self.start_jobs()

    @tasks.loop(time=TRACKER_UPDATE_TIMES)
    async def hourly_tracker_update(self) -> None:
        try:
            reconciled = await self.refresh_tracker()
            logger.info("hourly_tracker_update_complete reconciled=%s", reconciled)
        except Exception as exc:
            logger.exception("hourly_tracker_update_failed")
            await self.staff_alert("tracker_update", f"Hourly tracker update failed: `{str(exc)[:1500]}`")

    @hourly_tracker_update.before_loop
    async def before_hourly_tracker_update(self) -> None:
        await self.bot.wait_until_ready()

    async def post_recap(self, period: str) -> None:
        channel = await self.resolve_channel(RESULT_CHANNEL_ID, "RESULT_CHANNEL_ID", required=True)
        embed = await asyncio.to_thread(
            build_recap_embed, self.features, period, None, await self.tracked_operators(),
        )
        await channel.send(embed=embed)
        logger.info("recap_posted period=%s", period)

    @tasks.loop(time=RECAP_TIME)
    async def scheduled_recaps(self) -> None:
        now = datetime.now(TRACKER_TIMEZONE)
        for period, due in (("weekly", now.weekday() == 0), ("monthly", now.day == 1)):
            if not due:
                continue
            try:
                await self.post_recap(period)
            except Exception as exc:
                logger.exception("recap_failed period=%s", period)
                await self.staff_alert(f"recap_{period}", f"The {period} recap failed: `{str(exc)[:1500]}`")

    @scheduled_recaps.before_loop
    async def before_scheduled_recaps(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(name="recap", description="Preview or post the last weekly/monthly recap")
    @app_commands.describe(period="Which recap to build", post="Post it to the results channel instead of previewing")
    @app_commands.choices(period=[
        app_commands.Choice(name="Weekly (last Mon–Sun)", value="weekly"),
        app_commands.Choice(name="Monthly (last month)", value="monthly"),
    ])
    async def recap_command(
        self, interaction: discord.Interaction, period: app_commands.Choice[str], post: bool = False,
    ) -> None:
        if not self.can_manage_plays(interaction.user, interaction.guild):
            await interaction.response.send_message("Only officials or moderators can run recaps.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            if post:
                await self.post_recap(period.value)
                await interaction.followup.send(f"{period.value.title()} recap posted in <#{RESULT_CHANNEL_ID}>.", ephemeral=True)
            else:
                embed = await asyncio.to_thread(
                    build_recap_embed, self.features, period.value, None, await self.tracked_operators(),
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as exc:
            logger.exception("recap_command_failed period=%s", period.value)
            await interaction.followup.send(f"Could not build the recap: {exc}", ephemeral=True)

    @app_commands.command(name="rankings", description="Show the current team ranking summary")
    async def rankings_command(self, interaction: discord.Interaction) -> None:
        if TEAM_STATS_CHANNEL_ID and interaction.channel_id != TEAM_STATS_CHANNEL_ID:
            await interaction.response.send_message(f"Use this command in <#{TEAM_STATS_CHANNEL_ID}>", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            rankings = await asyncio.to_thread(self.rankings.fetch_rankings_from_supabase)
        except Exception as exc:
            logger.exception("rankings_failed")
            await interaction.followup.send(f"Could not load team rankings: {exc}", ephemeral=True)
            return
        embed = discord.Embed(title="Team Rankings", color=discord.Color.gold())
        if not rankings or all(
            item.get("net_units") == 0 and item.get("wins") == 0 and item.get("losses") == 0
            and item.get("voids") == 0 and item.get("partials") == 0 for item in rankings
        ):
            embed.description = "No settled results yet."
        else:
            lines = []
            for index, item in enumerate(rankings[:10], start=1):
                label = item.get("team_name") or f"Team {item['team_id']}"
                lines.append(f"{index}. {label} — {item['net_units']:+.2f}u | W{item['wins']} L{item['losses']} V{item['voids']} P{item['partials']}")
            embed.description = "\n".join(lines)
        await interaction.followup.send(embed=embed, ephemeral=False)

    @app_commands.command(name="summary", description="Build the current daily team summary")
    async def summary_command(self, interaction: discord.Interaction) -> None:
        if TEAM_STATS_CHANNEL_ID and interaction.channel_id != TEAM_STATS_CHANNEL_ID:
            await interaction.response.send_message(f"Use this command in <#{TEAM_STATS_CHANNEL_ID}>", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            plays, users = await asyncio.to_thread(self.tracker_rows)
            operators = await self.tracked_operators()
            embed, top_lines, breakdown = await asyncio.to_thread(
                self.tracker_embed, operator_plays(plays, users, operators), users,
            )
            embed.set_footer(text="Updated on request • Eastern Time")
            embed.add_field(
                name="Playmaker Breakdown", value=breakdown[:1024] or "No settled plays yet.", inline=False,
            )
            await interaction.followup.send(embed=embed, ephemeral=False)
            top_embed = discord.Embed(title="Top Playmakers", color=discord.Color.gold())
            top_embed.description = "\n\n".join(top_lines) or "No settled plays yet."
            await interaction.followup.send(embed=top_embed, ephemeral=False)
        except Exception as exc:
            logger.exception("summary_failed")
            await interaction.followup.send(f"Could not build the summary: {exc}", ephemeral=True)

    @app_commands.command(name="play_results", description="Search settled official plays by date, sport, capper and result")
    @app_commands.guild_only()
    @app_commands.describe(
        start="First settlement date, YYYY-MM-DD (Eastern)",
        end="Last settlement date, YYYY-MM-DD (Eastern, inclusive)",
        sport="Recorded sport; older plays may be Unspecified",
        capper="Filter by an official play author",
        result="Filter by settled result",
    )
    @app_commands.choices(
        sport=[app_commands.Choice(name=name, value=slug) for slug, name in SPORT_NAMES.items()],
        result=[app_commands.Choice(name=name.title(), value=name) for name in ("win", "loss", "void", "partial")],
    )
    async def results_command(
        self, interaction: discord.Interaction, start: str | None = None, end: str | None = None,
        sport: app_commands.Choice[str] | None = None, capper: discord.Member | None = None,
        result: app_commands.Choice[str] | None = None,
    ) -> None:
        if interaction.guild is None or not GUILD_ID or interaction.guild.id != GUILD_ID:
            await interaction.response.send_message("Use /play_results in the configured Playmaker server.", ephemeral=True)
            return
        try:
            first, last = parse_results_dates(start, end)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            operators = await self.tracked_operators()
            if operators is None:
                raise RuntimeError("The official operator roster is unavailable. Please retry.")
            plays, users, sports = await asyncio.to_thread(self.features.history)
            rows = await asyncio.to_thread(
                filter_results, plays, users, sports, operator_ids=operators, start=first, end=last,
                sport=sport.value if sport else None, capper_id=capper.id if capper else None,
                result=result.value if result else None,
            )
            labels = [f"{first.isoformat() if first else 'Beginning'} – {last.isoformat() if last else 'Now'}"]
            if sport:
                labels.append(sport.name)
            if capper:
                labels.append(discord.utils.escape_markdown(capper.display_name)[:70])
            if result:
                labels.append(result.name)
            channels = []
            for name, channel_id in dict.fromkeys(self.settlement_channels()):
                channel = await self.resolve_channel(channel_id, name)
                if isinstance(channel, discord.TextChannel) and channel.guild.id == interaction.guild.id:
                    channels.append(channel)
            view = ResultsView(interaction.user.id, rows, users, sports, " · ".join(labels), channels)
            await interaction.followup.send(embed=await view.embed(interaction), view=view if rows else None, ephemeral=True)
        except Exception:
            logger.exception("results_failed user=%s", interaction.user.id)
            await interaction.followup.send("Could not load official results. Check bot logs or retry shortly.", ephemeral=True)

    @app_commands.command(name="update_tracker", description="Refresh the Unit Summary and Top Playmakers embeds")
    async def update_tracker_command(self, interaction: discord.Interaction) -> None:
        if not self.can_manage_plays(interaction.user, interaction.guild):
            await interaction.response.send_message("Only officials or moderators can refresh the tracker.", ephemeral=True)
            return
        try:
            await interaction.response.defer(ephemeral=True)
            if not RESULT_CHANNEL_ID:
                await interaction.followup.send("RESULT_CHANNEL_ID is not configured.", ephemeral=True)
                return
            reconciled = await self.refresh_tracker()
            await interaction.followup.send(f"Tracker updated. Reconciled {reconciled} result(s).", ephemeral=True)
        except Exception as exc:
            logger.exception("official_tracker_update_failed")
            if interaction.response.is_done():
                await interaction.followup.send(f"Tracker update failed: {exc}", ephemeral=True)
            else:
                await interaction.response.send_message(f"Tracker update failed: {exc}", ephemeral=True)
