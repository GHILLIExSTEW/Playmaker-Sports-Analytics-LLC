import asyncio
import logging
import re
import uuid
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from io import BytesIO
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands, tasks
from src.datetime_utils import parse_iso_datetime

from src.config import API_SPORTS_KEY, APPLICATION_ID, CONFIRMATION_CHANNEL_ID, DISCORD_TOKEN, GUILD_ID, IMAGE_INPUT_CHANNEL_ID, MEMBER_BET_CHANNEL_ID, LOSS_REACTION, OFFICIAL_CHANNEL_ID, OFFICIAL_ROLE_IDS, OPERATOR_ROLE_IDS, PARTIAL_REACTION, RESULT_CHANNEL_ID, TEAM_STATS_CHANNEL_ID, TEST_CHANNEL_ID, TESTING, TRACKER_START_DATE, VOID_REACTION, WIN_REACTION
from src.member_bet_vault import MemberBetVault
from src.config import PAID_MEMBER_ROLE_ID, WHOP_MEMBERSHIP_SYNC_ENABLED
from src.membership_access import MembershipRoleSync, WhopMembershipSync
from src.member_stats import MemberStats
from src.services.membership_service import MembershipService
from src.services.official_play_service import OfficialPlayService
from src.services.supabase_service import supabase_service
from src.services.team_ranking_service import TeamRankingService
from src.services.play_service import PlayService
from src.services.image_play_service import image_play_service
from src.services.diagnostic_service import diagnostic_service
from src.services.tracker_image_service import render_tracker_image
from src.services.api_sports_service import api_sports_service
from src.services.api_sports_multi_service import api_sports_multi_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("official_play_bot")
TRACKER_TIMEZONE = ZoneInfo("America/New_York")
TRACKER_UPDATE_TIMES = [datetime_time(hour=hour, minute=0, tzinfo=TRACKER_TIMEZONE) for hour in range(24)]
API_SPORTS_DAILY_SYNC_TIME = datetime_time(hour=6, minute=10, tzinfo=TRACKER_TIMEZONE)
API_SPORTS_MULTI_DAILY_SYNC_TIME = datetime_time(hour=6, minute=25, tzinfo=TRACKER_TIMEZONE)
nfl_initial_sync_started = False
multi_sport_initial_sync_started = False

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True

class OfficialBot(commands.Bot):
    async def setup_hook(self) -> None:
        await self.add_cog(MemberStats())
        if whop_membership_sync is not None:
            await asyncio.to_thread(whop_membership_sync.service.ready)
        if PAID_MEMBER_ROLE_ID:
            if not WHOP_MEMBERSHIP_SYNC_ENABLED:
                raise RuntimeError("Enable Whop membership sync before configuring paid-role synchronization.")
            if not GUILD_ID:
                raise RuntimeError("GUILD_ID is required for paid membership role synchronization.")
            if PAID_MEMBER_ROLE_ID in OFFICIAL_ROLE_IDS | OPERATOR_ROLE_IDS:
                raise RuntimeError("Use a dedicated paid-member role, not an official/operator role.")
            await asyncio.to_thread(membership_role_sync.service.ready)
        if member_bet_vault is not None:
            other_channels = {
                OFFICIAL_CHANNEL_ID, IMAGE_INPUT_CHANNEL_ID, CONFIRMATION_CHANNEL_ID,
                TEST_CHANNEL_ID, RESULT_CHANNEL_ID, TEAM_STATS_CHANNEL_ID,
            }
            if MEMBER_BET_CHANNEL_ID in other_channels:
                raise RuntimeError("MEMBER_BET_CHANNEL_ID must be a separate submission-only channel.")
            await asyncio.to_thread(member_bet_vault.service.ready)
            await asyncio.to_thread(member_bet_vault.membership.ready)
            self.add_view(member_bet_vault.view)
        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            print(f"Synced guild commands: {', '.join(command.name for command in synced)}")
        else:
            synced = await self.tree.sync()
            print(f"Synced global commands: {', '.join(command.name for command in synced)}")


bot = OfficialBot(command_prefix="!", intents=intents, application_id=APPLICATION_ID)
member_bet_vault = MemberBetVault(bot, MEMBER_BET_CHANNEL_ID) if MEMBER_BET_CHANNEL_ID else None
membership_role_sync = MembershipRoleSync(bot, GUILD_ID, PAID_MEMBER_ROLE_ID) if GUILD_ID and PAID_MEMBER_ROLE_ID else None
whop_membership_sync = WhopMembershipSync(bot, membership_role_sync) if WHOP_MEMBERSHIP_SYNC_ENABLED else None
official_play_service = OfficialPlayService()
team_ranking_service = TeamRankingService()
play_service = PlayService()
testing_enabled = TESTING
tracker_start_date = date.fromisoformat(TRACKER_START_DATE) if TRACKER_START_DATE else None
REACTION_RESULTS = {
    WIN_REACTION: "win",
    LOSS_REACTION: "loss",
    VOID_REACTION: "void",
    PARTIAL_REACTION: "partial",
}


def build_play_embed(payload: dict) -> discord.Embed:
    embed = discord.Embed(title=f"Play #{payload['play_id']} • Open", description=payload["summary"], color=discord.Color.blurple())
    embed.add_field(name="Units", value=f"{float(payload['units']):g}u", inline=True)
    embed.add_field(name="Odds", value=f"{int(payload['odds']):+d}", inline=True)
    embed.add_field(name="To win", value=f"{float(payload['to_win']):g}u", inline=True)
    if payload.get("play_text"):
        embed.add_field(name="Selections", value=payload["play_text"][:1024], inline=False)
    return embed


def build_settled_play_embed(message: discord.Message, play_id: int, result: str) -> discord.Embed:
    if message.embeds:
        embed = discord.Embed.from_dict(message.embeds[0].to_dict())
    else:
        embed = discord.Embed(description=f"Bet result: **{result.upper()}**")

    for index in reversed(range(len(embed.fields))):
        field = embed.fields[index]
        if field.name.casefold() == "team":
            embed.remove_field(index)
        elif field.name.casefold() == "notes":
            embed.set_field_at(index, name="Selections", value=field.value, inline=field.inline)

    status_colors = {
        "win": discord.Color.green(),
        "loss": discord.Color.red(),
        "void": discord.Color.dark_grey(),
        "partial": discord.Color.orange(),
        "regraded": discord.Color.blurple(),
    }
    embed.title = f"Play #{play_id} • {result.title()}"
    embed.color = status_colors.get(result, discord.Color.blurple())
    return embed


async def update_play_message(message: discord.Message | None, play_id: int, result: str) -> None:
    if message is None:
        return
    try:
        await message.edit(embed=build_settled_play_embed(message, play_id, result))
    except discord.HTTPException:
        # The play is already settled in the database; a card the bot cannot edit must not abort the caller.
        logger.warning("play_card_edit_failed play=%s message=%s", play_id, message.id)


async def fetch_guild_message(guild: discord.Guild | None, message_id: int) -> discord.Message | None:
    if guild is None:
        return None
    for channel in guild.text_channels:
        try:
            return await channel.fetch_message(message_id)
        except (discord.NotFound, discord.Forbidden):
            continue
    return None


def play_post_target() -> tuple[str, int | None]:
    if testing_enabled:
        return "TEST_CHANNEL_ID", TEST_CHANNEL_ID
    return "CONFIRMATION_CHANNEL_ID", CONFIRMATION_CHANNEL_ID


def official_post_target() -> tuple[str, int | None]:
    if testing_enabled:
        return "TEST_CHANNEL_ID", TEST_CHANNEL_ID
    return "OFFICIAL_CHANNEL_ID", OFFICIAL_CHANNEL_ID


async def publish_play_message(
    interaction: discord.Interaction,
    payload: dict,
    target: tuple[str, int | None] | None = None,
) -> discord.Message:
    setting_name, channel_id = target or play_post_target()
    channel = await resolve_channel(channel_id, setting_name, required=True)
    embed = build_play_embed(payload)
    embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
    if payload.get("image_url"):
        embed.set_image(url=payload["image_url"])
    return await channel.send(embed=embed)


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


def settlement_channel_settings() -> list[tuple[str, int | None]]:
    # Reactions only count where play cards are posted, plus the official channel for cards published there.
    settings = [play_post_target(), ("OFFICIAL_CHANNEL_ID", OFFICIAL_CHANNEL_ID)]
    return [(name, channel_id) for name, channel_id in dict.fromkeys(settings) if channel_id]


def user_can_settle(user, owner_id: str, guild: discord.Guild | None) -> bool:
    if str(user.id) == str(owner_id):
        return True
    member = user if isinstance(user, discord.Member) else (guild.get_member(user.id) if guild else None)
    if member is None:
        return False
    if any(role.id in OPERATOR_ROLE_IDS for role in getattr(member, "roles", [])):
        return True
    return bool(getattr(member.guild_permissions, "manage_guild", False))


async def reconcile_open_play_reactions(plays: list[dict], users: list[dict], channels: list) -> int:
    discord_user_ids = {str(user["id"]): str(user.get("discord_user_id")) for user in users}
    settled_count = 0

    for play in plays:
        if play.get("status") != "open" or not play.get("message_id"):
            continue
        owner_id = discord_user_ids.get(str(play["user_id"]))
        if not owner_id or owner_id == "None":
            continue

        message = None
        for channel in channels:
            try:
                message = await channel.fetch_message(int(play["message_id"]))
                break
            except (discord.NotFound, discord.Forbidden):
                continue
        if message is None:
            continue

        result = None
        for reaction in message.reactions:
            reaction_result = REACTION_RESULTS.get(str(reaction.emoji))
            if reaction_result is None or reaction_result == "partial":
                continue
            async for reaction_user in reaction.users():
                if user_can_settle(reaction_user, owner_id, message.guild):
                    result = reaction_result
                    break
            if result:
                break

        if result:
            await asyncio.to_thread(official_play_service.settle_play, int(play["id"]), result)
            await update_play_message(message, int(play["id"]), result)
            settled_count += 1

    return settled_count


def parse_tracker_time(value: str, timezone_name: str) -> datetime:
    parsed = parse_iso_datetime(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(ZoneInfo(timezone_name))


def build_official_tracker_embed(
    plays: list[dict],
    users: list[dict],
    now: datetime | None = None,
    cutoff: date | None = None,
) -> tuple[discord.Embed, list[str]]:
    timezone_name = TRACKER_TIMEZONE.key
    now = now or datetime.now(ZoneInfo(timezone_name))
    if now.tzinfo is None:
        now = now.replace(tzinfo=ZoneInfo(timezone_name))
    else:
        now = now.astimezone(ZoneInfo(timezone_name))
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
        return official_play_service.settlement_service.tally_for_result(play["status"], float(play["units"]), play.get("odds"))

    def settled_time(play: dict) -> datetime:
        return parse_tracker_time(play.get("settled_at") or play["created_at"], timezone_name)

    def in_period(play: dict, start: datetime) -> bool:
        return start <= settled_time(play) <= now

    cutoff = cutoff if cutoff is not None else tracker_start_date
    if cutoff is not None:
        cutoff_start = datetime.combine(cutoff, datetime_time.min, tzinfo=ZoneInfo(timezone_name))
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
        bucket = by_user.setdefault(user_id, {"wins": 0.0, "losses": 0.0, "win_count": 0, "loss_count": 0})
        units = float(play["units"])
        if play.get("status") == "win":
            bucket["wins"] += signed(play)
            bucket["win_count"] += 1
        elif play.get("status") == "loss":
            bucket["losses"] += units
            bucket["loss_count"] += 1
    ranked = sorted(
        (item for item in by_user.items() if item[1]["win_count"] + item[1]["loss_count"]),
        key=lambda item: item[1]["wins"] - item[1]["losses"],
        reverse=True,
    )
    top_lines = []
    medals = ["🥇", "🥈", "🥉"]
    for index, (user_id, data) in enumerate(ranked[:3]):
        total = data["win_count"] + data["loss_count"]
        name = names.get(user_id, user_id)
        rate = data["win_count"] / total * 100 if total else 0
        net_units = data["wins"] - data["losses"]
        top_lines.append(f"{medals[index]} **{name}** — **{net_units:+g} units**\n{data['win_count']}-{data['loss_count']} record | {rate:.0f}% win rate")

    report_date = f"{today.strftime('%B')} {today.day}, {today.year}"
    embed = discord.Embed(title="Playmaker Picks | Unit Summary", description=f"Results for **{report_date}**", color=discord.Color.green())
    pending_label = "bet" if len(pending) == 1 else "bets"
    embed.add_field(
        name="⏳ Pending Bets",
        value=f"{len(pending)} {pending_label}",
        inline=True,
    )
    embed.add_field(
        name="📅 Monthly Units",
        value=f"{net([play for play in settled if in_period(play, month_start)]):+g}u",
        inline=True,
    )
    embed.add_field(
        name="🗓️ Yearly Units",
        value=f"{net([play for play in settled if in_period(play, year_start)]):+g}u",
        inline=True,
    )
    embed.set_footer(text="Auto-updates hourly • Eastern Time")
    return embed, top_lines


async def update_or_post_tracker_embed(channel, embed: discord.Embed, image: BytesIO | None = None) -> None:
    image_embed = embed
    image_file = None
    if image is not None:
        image_file = discord.File(image, filename="unit-summary.png")
        image_embed = discord.Embed(title=embed.title, color=embed.color)
        image_embed.description = embed.description
        for field in embed.fields:
            image_embed.add_field(name=field.name, value=field.value, inline=field.inline)
        if embed.footer.text:
            image_embed.set_footer(text=embed.footer.text)
        image_embed.set_image(url="attachment://unit-summary.png")

    async for message in channel.history(limit=50):
        if message.author == bot.user and message.embeds and message.embeds[0].title == embed.title:
            if image_file:
                await message.edit(embed=image_embed, attachments=[image_file])
            else:
                await message.edit(embed=embed)
            return
    if image_file:
        await channel.send(embed=image_embed, file=image_file)
    else:
        await channel.send(embed=embed)


async def resolve_channel(channel_id: int | None, setting_name: str, required: bool = False):
    if not channel_id:
        if required:
            raise RuntimeError(f"{setting_name} is not configured.")
        return None
    channel = bot.get_channel(channel_id)
    if channel is not None:
        return channel
    try:
        return await bot.fetch_channel(channel_id)
    except (discord.NotFound, discord.Forbidden) as exc:
        if required:
            raise RuntimeError(
                f"{setting_name}={channel_id} is not reachable: the channel does not exist or the bot lacks access."
            ) from exc
        logger.warning("tracker_channel_unavailable setting=%s channel_id=%s", setting_name, channel_id)
        return None


async def refresh_tracker_embeds() -> int:
    if not RESULT_CHANNEL_ID:
        raise RuntimeError("RESULT_CHANNEL_ID is not configured.")

    plays, users = await asyncio.to_thread(fetch_official_tracker_rows)
    reaction_channels = []
    for setting_name, channel_id in dict.fromkeys(settlement_channel_settings()):
        channel = await resolve_channel(channel_id, setting_name)
        if channel is not None:
            reaction_channels.append(channel)
    reconciled = await reconcile_open_play_reactions(plays, users, reaction_channels)
    if reconciled:
        plays, users = await asyncio.to_thread(fetch_official_tracker_rows)

    tracker_embed, top_lines = build_official_tracker_embed(plays, users)
    tracker_channel = await resolve_channel(RESULT_CHANNEL_ID, "RESULT_CHANNEL_ID", required=True)
    tracker_image = render_tracker_image(
        tracker_embed.description or "",
        [(field.name, field.value) for field in tracker_embed.fields],
        tracker_embed.footer.text or "",
    )
    await update_or_post_tracker_embed(tracker_channel, tracker_embed, tracker_image)

    team_channel = await resolve_channel(TEAM_STATS_CHANNEL_ID, "TEAM_STATS_CHANNEL_ID")
    if team_channel is not None:
        top_embed = discord.Embed(title="Top Playmakers", color=discord.Color.gold())
        top_embed.description = "\n\n".join(top_lines) or "No settled results yet."
        await update_or_post_tracker_embed(team_channel, top_embed)

    return reconciled


@tasks.loop(time=TRACKER_UPDATE_TIMES)
async def hourly_tracker_update() -> None:
    try:
        reconciled = await refresh_tracker_embeds()
        logger.info("hourly_tracker_update_complete reconciled=%s", reconciled)
    except Exception:
        logger.exception("hourly_tracker_update_failed")


@hourly_tracker_update.before_loop
async def before_hourly_tracker_update() -> None:
    await bot.wait_until_ready()


@tasks.loop(time=API_SPORTS_DAILY_SYNC_TIME)
async def daily_nfl_data_sync() -> None:
    try:
        result = await asyncio.to_thread(api_sports_service.sync_daily)
        logger.info("api_sports_daily_sync_complete result=%s", result)
    except Exception:
        logger.exception("api_sports_daily_sync_failed")


@daily_nfl_data_sync.before_loop
async def before_daily_nfl_data_sync() -> None:
    await bot.wait_until_ready()


@tasks.loop(minutes=15)
async def live_nfl_score_sync() -> None:
    try:
        should_sync = await asyncio.to_thread(api_sports_service.should_sync_live_scores)
        if not should_sync:
            return
        result = await asyncio.to_thread(api_sports_service.sync_live_scores)
        logger.info("api_sports_live_score_sync_complete result=%s", result)
    except Exception:
        logger.exception("api_sports_live_score_sync_failed")


@live_nfl_score_sync.before_loop
async def before_live_nfl_score_sync() -> None:
    await bot.wait_until_ready()


@tasks.loop(time=API_SPORTS_MULTI_DAILY_SYNC_TIME)
async def daily_multi_sport_api_sync() -> None:
    try:
        result = await asyncio.to_thread(api_sports_multi_service.sync_daily)
        logger.info("api_sports_multi_daily_sync_complete result=%s", result)
    except Exception:
        logger.exception("api_sports_multi_daily_sync_failed")


@daily_multi_sport_api_sync.before_loop
async def before_daily_multi_sport_api_sync() -> None:
    await bot.wait_until_ready()


@tasks.loop(minutes=15)
async def live_multi_sport_api_sync() -> None:
    try:
        sport_slugs = await asyncio.to_thread(api_sports_multi_service.active_sports)
        if not sport_slugs:
            return
        result = await asyncio.to_thread(api_sports_multi_service.sync_live_scores, sport_slugs)
        logger.info("api_sports_multi_live_sync_complete result=%s", result)
    except Exception:
        logger.exception("api_sports_multi_live_sync_failed")


@live_multi_sport_api_sync.before_loop
async def before_live_multi_sport_api_sync() -> None:
    await bot.wait_until_ready()


async def initial_nfl_data_sync() -> None:
    try:
        result = await asyncio.to_thread(api_sports_service.sync_daily)
        logger.info("api_sports_initial_sync_complete result=%s", result)
    except Exception:
        logger.exception("api_sports_initial_sync_failed")


async def initial_multi_sport_api_sync() -> None:
    try:
        result = await asyncio.to_thread(api_sports_multi_service.sync_daily)
        logger.info("api_sports_multi_initial_sync_complete result=%s", result)
    except Exception:
        logger.exception("api_sports_multi_initial_sync_failed")


@bot.event
async def on_ready():
    global nfl_initial_sync_started
    global multi_sport_initial_sync_started
    print(f"Logged in as {bot.user}")
    if whop_membership_sync is not None and not whop_membership_sync.reconcile.is_running():
        whop_membership_sync.reconcile.start()
    if member_bet_vault is not None and not member_bet_vault.reconcile.is_running():
        member_bet_vault.reconcile.start()
    if not hourly_tracker_update.is_running():
        hourly_tracker_update.start()
    if API_SPORTS_KEY:
        if not daily_nfl_data_sync.is_running():
            daily_nfl_data_sync.start()
        if not live_nfl_score_sync.is_running():
            live_nfl_score_sync.start()
        if not daily_multi_sport_api_sync.is_running():
            daily_multi_sport_api_sync.start()
        if not live_multi_sport_api_sync.is_running():
            live_multi_sport_api_sync.start()
        if not nfl_initial_sync_started:
            nfl_initial_sync_started = True
            asyncio.create_task(initial_nfl_data_sync())
        if not multi_sport_initial_sync_started:
            multi_sport_initial_sync_started = True
            asyncio.create_task(initial_multi_sport_api_sync())


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.user_id == bot.user.id:
        return

    result = REACTION_RESULTS.get(str(payload.emoji))
    if result is None:
        return
    if payload.channel_id not in {channel_id for _, channel_id in settlement_channel_settings()}:
        return

    try:
        play = await asyncio.to_thread(official_play_service.get_play_for_message, payload.message_id)
        if not play:
            return
        if play.get("status") != "open":
            return
        if result == "partial":
            return

        channel = bot.get_channel(payload.channel_id) or await bot.fetch_channel(payload.channel_id)
        reactor = payload.member or bot.get_user(payload.user_id) or await bot.fetch_user(payload.user_id)
        if not user_can_settle(reactor, str(play.get("discord_user_id")), getattr(channel, "guild", None)):
            return

        await asyncio.to_thread(official_play_service.settle_play, int(play["id"]), result)
        message = await channel.fetch_message(payload.message_id)
        await update_play_message(message, int(play["id"]), result)
    except Exception:
        logger.exception("reaction_settlement_failed message=%s user=%s", payload.message_id, payload.user_id)


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    if member_bet_vault is not None and message.channel.id == MEMBER_BET_CHANNEL_ID:
        await member_bet_vault.handle_message(message)
        return

    is_test_channel = testing_enabled and TEST_CHANNEL_ID and message.channel.id == TEST_CHANNEL_ID
    if is_test_channel:
        image = next((attachment for attachment in message.attachments if (attachment.content_type or "").startswith("image/")), None)
        if image is None:
            return
        try:
            parsed = await asyncio.to_thread(image_play_service.extract_play, image.url, message.content)
            await message.channel.send(
                f"{message.author.mention}, test image parsed. Nothing will be recorded.",
                embed=build_image_test_embed(parsed),
                view=TestFlowView(parsed),
            )
        except Exception as exc:
            logger.exception("testing_image_extract_failed message=%s", message.id)
            await message.channel.send(f"{message.author.mention}, test image failed: {exc}", delete_after=30)
        return

    if IMAGE_INPUT_CHANNEL_ID and message.channel.id != IMAGE_INPUT_CHANNEL_ID:
        await bot.process_commands(message)
        return

    image = next((attachment for attachment in message.attachments if (attachment.content_type or "").startswith("image/")), None)
    if image is None:
        await bot.process_commands(message)
        return

    try:
        parsed = await asyncio.to_thread(image_play_service.extract_play, image.url, message.content)
        parsed["image_url"] = image.url
        await message.channel.send(
            "User Reviewing Bet",
            view=AutoImageView(message.author.id, parsed, message.id),
        )
    except Exception as exc:
        logger.exception("automatic_image_extract_failed message=%s", message.id)
        await message.channel.send(f"{message.author.mention}, I could not read that betting image: {exc}", delete_after=30)
    await bot.process_commands(message)


async def record_modal_play(
    interaction: discord.Interaction,
    units: float,
    legs: int,
    odds_values: list[int],
    team_name: str,
    play_text: str,
    leg_records: list[dict],
) -> None:
    logger.info("play_modal_deferred interaction=%s", interaction.id)
    try:
        logger.info("play_db_start interaction=%s", interaction.id)
        payload = await asyncio.to_thread(
            official_play_service.create_play_record,
            discord_user_id=str(interaction.user.id),
            username=interaction.user.display_name,
            units=units,
            legs=legs,
            odds=play_service.combine_american_odds(odds_values),
            team_name=team_name,
            play_text=play_text,
            leg_records=leg_records,
        )
        logger.info("play_db_complete interaction=%s play_id=%s error=%s", interaction.id, payload.get("play_id"), bool(payload.get("error")))
    except Exception as exc:
        logger.exception("play_db_failed interaction=%s", interaction.id)
        await interaction.followup.send(f"Could not record the play: {exc}", ephemeral=True)
        return

    if payload.get("error"):
        logger.warning("play_validation_failed interaction=%s error=%s", interaction.id, payload["error"])
        await interaction.followup.send(payload["error"], ephemeral=True)
        return

    logger.info("play_webhook_start interaction=%s", interaction.id)
    try:
        message = await publish_play_message(interaction, payload, official_post_target())
    except Exception as exc:
        await interaction.followup.send(f"Could not publish the play: {exc}", ephemeral=True)
        return

    logger.info("play_webhook_complete interaction=%s message_id=%s", interaction.id, message.id)
    await asyncio.to_thread(official_play_service.attach_message_id, payload["play_id"], message.id)
    await send_confirmation_message(interaction, payload)
    logger.info("play_complete interaction=%s play_id=%s", interaction.id, payload["play_id"])


class LegModal(discord.ui.Modal):
    leg_details = discord.ui.TextInput(
        label="Leg details",
        placeholder="Enter the pick or selection for this leg",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000,
    )
    leg_odds = discord.ui.TextInput(
        label="Leg odds",
        placeholder="Example: -110 or +150",
        required=True,
        max_length=20,
    )

    def __init__(self, draft_id: str, units: float, legs: int, odds_values: list[int], team_name: str, leg_number: int, collected: list[str], progress_message=None):
        super().__init__(title=f"Enter Leg {leg_number} of {legs}")
        self.draft_id = draft_id
        self.units = units
        self.legs = legs
        self.odds_values = odds_values
        self.team_name = team_name
        self.leg_number = leg_number
        self.collected = collected
        self.progress_message = progress_message

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            leg_odds = play_service.normalize_odds(self.leg_odds.value)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        self.collected.append(f"Leg {self.leg_number}: {self.leg_details.value.strip()} ({leg_odds:+d})")
        self.odds_values.append(leg_odds)
        await interaction.response.defer()
        await asyncio.to_thread(
            official_play_service.save_draft_leg,
            self.draft_id,
            str(interaction.user.id),
            self.units,
            self.legs,
            self.leg_number,
            self.leg_details.value.strip(),
            leg_odds,
            self.team_name,
        )
        logger.info("leg_modal_submit interaction=%s leg=%s/%s", interaction.id, self.leg_number, self.legs)
        if self.leg_number < self.legs:
            next_view = LegEntryView(
                    self.draft_id,
                    self.units,
                    self.legs,
                    self.odds_values,
                    self.team_name,
                    self.leg_number + 1,
                    self.collected,
                )
            if self.progress_message is not None:
                next_view.message = self.progress_message
                await self.progress_message.edit(
                    content=f"Leg {self.leg_number} saved. Continue with leg {self.leg_number + 1}.",
                    view=next_view,
                )
            else:
                await interaction.followup.send(
                    f"Leg {self.leg_number} saved. Continue with leg {self.leg_number + 1}.",
                    ephemeral=True,
                    view=next_view,
                )
            return

        draft_legs = await asyncio.to_thread(official_play_service.get_draft_legs, self.draft_id, str(interaction.user.id))
        combined = "\n".join(f"Leg {leg['leg_number']}: {leg['selection']} ({int(leg['odds']):+d})" for leg in draft_legs)
        await record_modal_play(interaction, self.units, self.legs, [int(leg["odds"]) for leg in draft_legs], self.team_name, combined, draft_legs)
        await asyncio.to_thread(official_play_service.clear_draft_legs, self.draft_id, str(interaction.user.id))


class LegEntryView(discord.ui.View):
    def __init__(self, draft_id: str, units: float, legs: int, odds_values: list[int], team_name: str, leg_number: int, collected: list[str]):
        super().__init__(timeout=900)
        self.draft_id = draft_id
        self.units = units
        self.legs = legs
        self.odds_values = odds_values
        self.team_name = team_name
        self.leg_number = leg_number
        self.collected = collected
        self.message = None

    @discord.ui.button(label="Enter next leg", style=discord.ButtonStyle.primary)
    async def enter_next_leg(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.message = interaction.message
        await interaction.response.send_modal(LegModal(
            self.draft_id,
                self.units,
                self.legs,
                self.odds_values,
                self.team_name,
                self.leg_number,
                self.collected,
                self.message,
            ))


class PlayModal(discord.ui.Modal, title="Record Official Play"):
    units = discord.ui.TextInput(label="Units risked", placeholder="Example: 2", required=True, max_length=20)
    legs = discord.ui.TextInput(label="Number of legs", placeholder="Example: 3", required=True, max_length=10)
    leg_one = discord.ui.TextInput(label="Leg 1 selection", placeholder="Enter the first pick or selection", required=True, max_length=1000)
    leg_one_odds = discord.ui.TextInput(label="Leg 1 odds", placeholder="Example: -110 or +150", required=True, max_length=20)
    team_name = discord.ui.TextInput(label="Team (optional)", placeholder="Enter a team, including an untracked team", required=False, max_length=100)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        logger.info("play_modal_submit interaction=%s user=%s channel=%s", interaction.id, interaction.user.id, interaction.channel_id)
        try:
            units = float(self.units.value)
            legs = int(self.legs.value)
        except ValueError:
            logger.warning("play_modal_invalid_numbers interaction=%s", interaction.id)
            await interaction.response.send_message("Units must be a number and legs must be a whole number.", ephemeral=True)
            return

        if legs < 1 or legs > 10:
            await interaction.response.send_message("Legs must be between 1 and 10.", ephemeral=True)
            return

        try:
            first_odds = play_service.normalize_odds(self.leg_one_odds.value)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        collected = [f"Leg 1: {self.leg_one.value.strip()}"]
        draft_id = str(uuid.uuid4())
        await interaction.response.defer()
        await asyncio.to_thread(
            official_play_service.save_draft_leg,
            draft_id,
            str(interaction.user.id),
            units,
            legs,
            1,
            self.leg_one.value.strip(),
            first_odds,
            self.team_name.value,
        )
        if legs > 1:
            await interaction.followup.send(
                "Start entering the legs one at a time.",
                ephemeral=True,
                view=LegEntryView(
                    draft_id,
                    units,
                    legs,
                    [first_odds],
                    self.team_name.value,
                    2,
                    collected,
                ),
            )
            return

        draft_legs = await asyncio.to_thread(official_play_service.get_draft_legs, draft_id, str(interaction.user.id))
        await record_modal_play(
            interaction,
            units,
            legs,
            [first_odds],
            self.team_name.value,
            "\n".join(collected),
            draft_legs,
        )
        await asyncio.to_thread(official_play_service.clear_draft_legs, draft_id, str(interaction.user.id))


class ConfirmImageView(discord.ui.View):
    def __init__(self, parsed: dict, source_message_id: int | None = None):
        super().__init__(timeout=900)
        self.parsed = parsed
        self.source_message_id = source_message_id
        self.recording = False

    @discord.ui.button(label="Confirm and record", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        if self.recording:
            await interaction.followup.send("This image is already being recorded.", ephemeral=True)
            return
        self.recording = True
        legs = self.parsed["legs"]
        odds_values = [int(leg["odds"]) for leg in legs]
        try:
            if self.source_message_id and await asyncio.to_thread(official_play_service.get_play_for_message, self.source_message_id):
                await interaction.followup.send("This image has already been recorded.", ephemeral=True)
                return
            payload = await asyncio.to_thread(
                official_play_service.create_play_record,
                discord_user_id=str(interaction.user.id),
                username=interaction.user.display_name,
                units=float(self.parsed["units"]),
                legs=len(legs),
                odds=play_service.combine_american_odds(odds_values),
                team_name=self.parsed.get("team_name") or "",
                play_text="\n".join(f"Leg {index}: {leg['selection']} ({int(leg['odds']):+d})" for index, leg in enumerate(legs, start=1)),
                leg_records=legs,
            )
            if payload.get("error"):
                await interaction.followup.send(payload["error"], ephemeral=True)
                return
            payload["image_url"] = self.parsed.get("image_url")
            message = await publish_play_message(interaction, payload)
            # Reactions are tracked on the player's original post in the official channel.
            tracked_message_id = self.source_message_id or message.id
            await asyncio.to_thread(official_play_service.attach_message_id, payload["play_id"], tracked_message_id)
            await send_confirmation_message(interaction, payload)
            if interaction.message is not None:
                await interaction.message.delete()
            self.stop()
        except Exception as exc:
            logger.exception("image_play_confirm_failed interaction=%s", interaction.id)
            await interaction.followup.send(f"Could not record the play: {exc}", ephemeral=True)
        finally:
            self.recording = False

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Image import cancelled.", embed=None, view=None)
        self.stop()


async def send_confirmation_message(interaction: discord.Interaction, payload: dict) -> None:
    payload["leg_records"] = await asyncio.to_thread(official_play_service.get_play_legs, payload["play_id"])
    confirmation = f"Bet recorded: Play #{payload['play_id']}"
    view = EditBetView(payload["play_id"], interaction.user.id, payload)
    await interaction.followup.send(confirmation, ephemeral=True, view=view)


class EditBetModal(discord.ui.Modal, title="Edit Recorded Bet"):
    units = discord.ui.TextInput(label="Units", required=True, max_length=20)
    team = discord.ui.TextInput(label="Team", required=False, max_length=100)
    selections = discord.ui.TextInput(label="Selections, one per line", style=discord.TextStyle.paragraph, required=True, max_length=2000)
    odds = discord.ui.TextInput(label="Odds, one per line", style=discord.TextStyle.paragraph, required=True, max_length=500)
    notes = discord.ui.TextInput(label="Notes", style=discord.TextStyle.paragraph, required=False, max_length=1000)

    def __init__(self, play_id: int, owner_id: int, payload: dict):
        super().__init__()
        self.play_id = play_id
        self.owner_id = owner_id
        self.payload = payload
        self.units.default = str(payload.get("units", ""))
        self.team.default = payload.get("team_name") or ""
        leg_records = payload.get("leg_records") or []
        self.selections.default = "\n".join(leg["selection"] for leg in leg_records)
        self.odds.default = "\n".join(str(leg["odds"]) for leg in leg_records)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the original user can edit this bet.", ephemeral=True)
            return
        selections = [line.strip() for line in self.selections.value.splitlines() if line.strip()]
        try:
            odds = [play_service.normalize_odds(line) for line in self.odds.value.splitlines() if line.strip()]
            units = float(self.units.value)
            updated = await asyncio.to_thread(
                official_play_service.edit_play_record,
                self.play_id,
                units,
                self.team.value,
                selections,
                odds,
                self.notes.value,
            )
            await interaction.response.edit_message(content=f"Bet {self.play_id} updated.", embed=None, view=None)
            self.stop()
        except (ValueError, TypeError) as exc:
            await interaction.response.send_message(f"Could not update bet: {exc}", ephemeral=True)


class EditBetView(discord.ui.View):
    def __init__(self, play_id: int, owner_id: int, payload: dict):
        super().__init__(timeout=900)
        self.play_id = play_id
        self.owner_id = owner_id
        self.payload = payload

    @discord.ui.button(label="Edit bet", style=discord.ButtonStyle.secondary)
    async def edit_bet(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the original user can edit this bet.", ephemeral=True)
            return
        await interaction.response.send_modal(EditBetModal(self.play_id, self.owner_id, self.payload))


class TestUnitsModal(discord.ui.Modal, title="Enter Units for Image Test"):
    units = discord.ui.TextInput(label="Units", placeholder="Example: 2", required=True, max_length=20)

    def __init__(self, parsed: dict):
        super().__init__()
        self.parsed = parsed

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            units = float(self.units.value)
            if units <= 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("Units must be a number greater than zero.", ephemeral=True)
            return
        self.parsed["units"] = units
        await interaction.response.edit_message(embed=build_image_test_embed(self.parsed), view=TestFlowView(self.parsed))


class TestFlowView(discord.ui.View):
    def __init__(self, parsed: dict):
        super().__init__(timeout=900)
        self.parsed = parsed

    @discord.ui.button(label="Enter units", style=discord.ButtonStyle.primary)
    async def enter_units(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.parsed.get("units") is not None:
            await interaction.response.send_message("Units are already available.", ephemeral=True)
            return
        await interaction.response.send_modal(TestUnitsModal(self.parsed))

    @discord.ui.button(label="Complete test", style=discord.ButtonStyle.success)
    async def complete_test(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.parsed.get("units") is None:
            await interaction.response.send_message("Enter units before completing the test.", ephemeral=True)
            return
        await interaction.response.edit_message(
            content="Test complete. Nothing was recorded.",
            embed=None,
            view=None,
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_test(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Test cancelled.", embed=None, view=None)


class TestImageView(discord.ui.View):
    def __init__(self, parsed: dict):
        super().__init__(timeout=900)
        self.parsed = parsed
        if parsed.get("units") is not None:
            self.clear_items()

    @discord.ui.button(label="Enter units", style=discord.ButtonStyle.primary)
    async def enter_units(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(TestUnitsModal(self.parsed))


def build_image_review_embed(parsed: dict) -> discord.Embed:
    legs = parsed["legs"]
    odds = play_service.combine_american_odds([int(leg["odds"]) for leg in legs])
    embed = discord.Embed(title="Image play detected", color=discord.Color.orange())
    embed.add_field(name="Units", value=str(parsed.get("units") or "Not visible"), inline=True)
    embed.add_field(name="Legs", value=str(len(legs)), inline=True)
    embed.add_field(name="Combined odds", value=f"{odds:+d}", inline=True)
    embed.description = "\n".join(
        f"{index}. {leg['selection']} ({int(leg['odds']):+d})"
        for index, leg in enumerate(legs, start=1)
    )[:4096]
    return embed


class AutoUnitsModal(discord.ui.Modal, title="Enter Units"):
    units = discord.ui.TextInput(label="Units risked", placeholder="Example: 2", required=True, max_length=20)

    def __init__(self, owner_id: int, parsed: dict, source_message_id: int, review_message: discord.Message):
        super().__init__()
        self.owner_id = owner_id
        self.parsed = parsed
        self.source_message_id = source_message_id
        self.review_message = review_message

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the original uploader can continue.", ephemeral=True)
            return
        try:
            units = float(self.units.value)
            if units <= 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("Units must be greater than zero.", ephemeral=True)
            return
        self.parsed["units"] = units
        await interaction.response.defer()
        await interaction.followup.edit_message(
            self.review_message.id,
            content="User Reviewing Bet",
            embed=build_image_review_embed(self.parsed),
            view=ConfirmImageView(self.parsed, self.source_message_id),
        )


class AutoImageView(discord.ui.View):
    def __init__(self, owner_id: int, parsed: dict, source_message_id: int):
        super().__init__(timeout=900)
        self.owner_id = owner_id
        self.parsed = parsed
        self.source_message_id = source_message_id

    @discord.ui.button(label="Review image", style=discord.ButtonStyle.primary)
    async def review_image(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the original uploader can review this image.", ephemeral=True)
            return
        if self.parsed.get("units") is None:
            await interaction.response.send_modal(AutoUnitsModal(self.owner_id, self.parsed, self.source_message_id, interaction.message))
        else:
            await interaction.response.edit_message(
                content="User Reviewing Bet",
                embed=build_image_review_embed(self.parsed),
                view=ConfirmImageView(self.parsed, self.source_message_id),
            )


def build_image_test_embed(parsed: dict) -> discord.Embed:
    legs = parsed["legs"]
    odds = play_service.combine_american_odds([int(leg["odds"]) for leg in legs])
    embed = discord.Embed(
        title="Image Test Result",
        description="This image was parsed successfully. Nothing was recorded.",
        color=discord.Color.blue(),
    )
    embed.add_field(name="Units", value=str(parsed.get("units") or "Not visible"), inline=True)
    embed.add_field(name="Legs", value=str(len(legs)), inline=True)
    embed.add_field(name="Combined odds", value=f"{odds:+d}", inline=True)
    embed.add_field(
        name="Selections",
        value="\n".join(f"{index}. {leg['selection']} ({int(leg['odds']):+d})" for index, leg in enumerate(legs, start=1))[:1024],
        inline=False,
    )
    return embed


async def import_image_command(interaction: discord.Interaction, image: discord.Attachment):
    if not interaction.guild:
        await interaction.response.send_message("This command can only be used in a guild.", ephemeral=True)
        return
    if OFFICIAL_ROLE_IDS and not any(role.id in OFFICIAL_ROLE_IDS for role in interaction.user.roles):
        await interaction.response.send_message("You do not have permission to import plays.", ephemeral=True)
        return
    if OFFICIAL_CHANNEL_ID and interaction.channel_id != OFFICIAL_CHANNEL_ID:
        await interaction.response.send_message(f"Use this command in the official channel: <#{OFFICIAL_CHANNEL_ID}>", ephemeral=True)
        return
    if not image.content_type or not image.content_type.startswith("image/"):
        await interaction.response.send_message("Attach an image file.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    try:
        parsed = await asyncio.to_thread(image_play_service.extract_play, image.url)
        parsed["image_url"] = image.url
    except Exception as exc:
        logger.exception("image_play_extract_failed interaction=%s", interaction.id)
        await interaction.followup.send(f"Could not read that image: {exc}", ephemeral=True)
        return

    legs = parsed["legs"]
    odds = play_service.combine_american_odds([int(leg["odds"]) for leg in legs])
    embed = discord.Embed(title="Image play detected", color=discord.Color.orange())
    embed.add_field(name="Units", value=str(parsed["units"]), inline=True)
    embed.add_field(name="Legs", value=str(len(legs)), inline=True)
    embed.add_field(name="Combined odds", value=f"{odds:+d}", inline=True)
    embed.description = "\n".join(f"{index}. {leg['selection']} ({int(leg['odds']):+d})" for index, leg in enumerate(legs, start=1))
    await interaction.followup.send(embed=embed, content="Review the detected play before recording it:", view=ConfirmImageView(parsed), ephemeral=True)


@bot.tree.command(name="play", description="Record a play manually without an image")
async def play_command(interaction: discord.Interaction):
    logger.info("play_command_received interaction=%s user=%s channel=%s", interaction.id, interaction.user.id, interaction.channel_id)
    if not interaction.guild:
        logger.warning("play_rejected_no_guild interaction=%s", interaction.id)
        await interaction.response.send_message("This command can only be used in a guild.", ephemeral=True)
        return

    if OFFICIAL_ROLE_IDS and not any(role.id in OFFICIAL_ROLE_IDS for role in interaction.user.roles):
        logger.warning("play_rejected_role interaction=%s user=%s", interaction.id, interaction.user.id)
        await interaction.response.send_message("You do not have permission to log official plays.", ephemeral=True)
        return

    if OFFICIAL_CHANNEL_ID and interaction.channel_id != OFFICIAL_CHANNEL_ID:
        logger.warning("play_rejected_channel interaction=%s channel=%s expected=%s", interaction.id, interaction.channel_id, OFFICIAL_CHANNEL_ID)
        await interaction.response.send_message(f"Use this command in the official channel: <#{OFFICIAL_CHANNEL_ID}>", ephemeral=True)
        return

    logger.info("play_modal_open_start interaction=%s", interaction.id)
    await interaction.response.send_modal(PlayModal())


@bot.tree.command(name="membership_status", description="Privately check paid or trial Member Vault eligibility")
async def membership_status_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    if not WHOP_MEMBERSHIP_SYNC_ENABLED:
        await interaction.followup.send("Whop membership verification is not enabled yet. Checkout remains closed.", ephemeral=True)
        return
    try:
        eligible = await asyncio.to_thread(MembershipService().has_vault_access, interaction.user.id)
        await interaction.followup.send(
            "Your Member Vault access is verified through a current paid membership, an eligible seven-day trial, or an explicit owner grant. You can submit tickets."
            if eligible else
            "No current eligible membership or owner grant was found for this Discord account. "
            "Connect this account in Whop and allow up to five minutes for synchronization. "
            "ROOKIE community access alone does not qualify. Contact support if your paid pass or first-time trial is missing.",
            ephemeral=True,
        )
    except Exception:
        logger.exception("membership_status_failed user=%s", interaction.user.id)
        await interaction.followup.send("Membership verification is temporarily unavailable. Please retry later.", ephemeral=True)


@bot.tree.command(name="test", description="Test an image or run play-system diagnostics")
@discord.app_commands.describe(image="Optional betting-slip image to parse without recording")
async def test_command(interaction: discord.Interaction, image: discord.Attachment | None = None):
    if OFFICIAL_ROLE_IDS and not any(role.id in OFFICIAL_ROLE_IDS for role in interaction.user.roles):
        await interaction.response.send_message("Only officials can run diagnostics.", ephemeral=True)
        return

    if image is not None:
        if not image.content_type or not image.content_type.startswith("image/"):
            await interaction.response.send_message("Attach an image file.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            parsed = await asyncio.to_thread(image_play_service.extract_play, image.url)
            await interaction.followup.send(embed=build_image_test_embed(parsed), view=TestImageView(parsed), ephemeral=True)
        except Exception as exc:
            logger.exception("test_image_failed interaction=%s", interaction.id)
            await interaction.followup.send(f"Image test failed: {exc}", ephemeral=True)
        return

    checks = diagnostic_service.run_checks()
    passed = sum(check["passed"] for check in checks)
    embed = discord.Embed(
        title="Play System Diagnostics",
        description=f"{passed}/{len(checks)} checks passed. No database records were changed.",
        color=discord.Color.green() if passed == len(checks) else discord.Color.red(),
    )
    for check in checks:
        marker = "PASS" if check["passed"] else "FAIL"
        embed.add_field(name=f"{marker} • {check['name']}", value=check["detail"][:1024], inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="testing", description="Enable or disable the image test channel")
@discord.app_commands.describe(enabled="True enables test mode; false disables it")
async def testing_command(interaction: discord.Interaction, enabled: bool):
    global testing_enabled
    is_operator = any(role.id in OPERATOR_ROLE_IDS for role in getattr(interaction.user, "roles", []))
    is_manager = bool(interaction.guild and interaction.user.guild_permissions.manage_guild)
    if not (is_operator or is_manager):
        await interaction.response.send_message("Only operators or server managers can change testing mode.", ephemeral=True)
        return
    testing_enabled = enabled
    state = "enabled" if enabled else "disabled"
    await interaction.response.send_message(f"Image testing is now **{state}**.", ephemeral=True)
    logger.info("play_modal_open_complete interaction=%s", interaction.id)


@bot.tree.command(name="tracker_start", description="Only count plays settled on or after a date")
@discord.app_commands.describe(date_value="YYYY-MM-DD, or 'clear' to count every play, or 'show' to view the current setting")
async def tracker_start_command(interaction: discord.Interaction, date_value: str):
    global tracker_start_date
    requested = date_value.strip().lower()

    if requested == "show":
        current = tracker_start_date.isoformat() if tracker_start_date else "none (counting all plays)"
        await interaction.response.send_message(f"Tracker start date: **{current}**", ephemeral=True)
        return

    is_operator = any(role.id in OPERATOR_ROLE_IDS for role in getattr(interaction.user, "roles", []))
    is_manager = bool(interaction.guild and interaction.user.guild_permissions.manage_guild)
    if not (is_operator or is_manager):
        await interaction.response.send_message("Only operators or server managers can change the tracker start date.", ephemeral=True)
        return

    if requested in {"clear", "none", "off"}:
        tracker_start_date = None
        await interaction.response.send_message("Tracker start date cleared. All plays now count.", ephemeral=True)
        logger.info("tracker_start_date_cleared user=%s", interaction.user.id)
        return

    try:
        parsed = date.fromisoformat(requested)
    except ValueError:
        await interaction.response.send_message("Use the format YYYY-MM-DD, or 'clear' / 'show'.", ephemeral=True)
        return

    tracker_start_date = parsed
    await interaction.response.send_message(
        f"Tracker now counts plays settled on or after **{parsed.isoformat()}** (Eastern). Run /update_tracker to refresh.",
        ephemeral=True,
    )
    logger.info("tracker_start_date_set value=%s user=%s", parsed.isoformat(), interaction.user.id)


def is_official(user) -> bool:
    return not OFFICIAL_ROLE_IDS or any(role.id in OFFICIAL_ROLE_IDS for role in getattr(user, "roles", []))


PLAY_PICKER_PAGE_SIZE = 10
REGRADE_LOOKBACK = timedelta(days=2)
SETTLE_RESULTS = (("win", "Win", discord.ButtonStyle.success), ("loss", "Loss", discord.ButtonStyle.danger),
                  ("void", "Void", discord.ButtonStyle.secondary), ("partial", "Partial", discord.ButtonStyle.primary))


def load_play_page(mode: str, page: int) -> tuple[list[dict], bool]:
    if mode == "settle":
        return official_play_service.list_plays(page, PLAY_PICKER_PAGE_SIZE, statuses=["open"])
    return official_play_service.list_plays(page, PLAY_PICKER_PAGE_SIZE, since=datetime.now(timezone.utc) - REGRADE_LOOKBACK)


def play_picker_content(mode: str, page: int) -> str:
    if mode == "settle":
        return f"Select an open play to settle (page {page + 1}):"
    return f"Select a play from the last 2 days to regrade (page {page + 1}):"


class OwnerOnlyView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=600)
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This menu belongs to someone else.", ephemeral=True)
            return False
        return True


class PlayPickerView(OwnerOnlyView):
    def __init__(self, owner_id: int, mode: str, plays: list[dict], page: int, has_more: bool):
        super().__init__(owner_id)
        self.mode = mode
        self.page = page
        self.plays = {str(play["id"]): play for play in plays}

        select = discord.ui.Select(
            placeholder="Choose a play to settle" if mode == "settle" else "Choose a play to regrade",
            options=[discord.SelectOption(label=official_play_service.open_play_label(play), value=str(play["id"])) for play in plays],
        )
        select.callback = self.on_select
        self.select = select
        self.add_item(select)

        previous_button = discord.ui.Button(label="◀ Newer", style=discord.ButtonStyle.secondary, disabled=page == 0)
        previous_button.callback = lambda interaction: self.change_page(interaction, page - 1)
        self.add_item(previous_button)
        next_button = discord.ui.Button(label="Older ▶", style=discord.ButtonStyle.secondary, disabled=not has_more)
        next_button.callback = lambda interaction: self.change_page(interaction, page + 1)
        self.add_item(next_button)

    async def change_page(self, interaction: discord.Interaction, page: int) -> None:
        await interaction.response.defer()
        await show_play_picker(interaction, self.mode, max(0, page), edit=True)

    async def on_select(self, interaction: discord.Interaction) -> None:
        play = self.plays[self.select.values[0]]
        if self.mode == "regrade":
            await interaction.response.send_modal(RegradeModal(play))
            return
        await interaction.response.edit_message(
            content=f"Settle **{official_play_service.open_play_label(play)}** as:",
            view=SettleResultView(self.owner_id, play, self.page),
        )


class SettleResultView(OwnerOnlyView):
    def __init__(self, owner_id: int, play: dict, page: int):
        super().__init__(owner_id)
        self.play = play
        self.page = page
        for value, label, style in SETTLE_RESULTS:
            button = discord.ui.Button(label=label, style=style)
            button.callback = lambda interaction, result=value: self.settle(interaction, result)
            self.add_item(button)
        back_button = discord.ui.Button(label="Back", style=discord.ButtonStyle.secondary)
        back_button.callback = self.back
        self.add_item(back_button)

    async def back(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await show_play_picker(interaction, "settle", self.page, edit=True)

    async def settle(self, interaction: discord.Interaction, result: str) -> None:
        await interaction.response.defer()
        play_id = int(self.play["id"])
        try:
            current = await asyncio.to_thread(official_play_service._fetch_play, play_id)
            if current.get("status") != "open":
                await interaction.edit_original_response(content=f"Play #{play_id} is already settled as **{current.get('status')}**.", view=None)
                return
            outcome = await asyncio.to_thread(official_play_service.settle_play, play_id, result)
            message = await fetch_guild_message(interaction.guild, int(current["message_id"])) if current.get("message_id") else None
            await update_play_message(message, play_id, outcome["result"])
        except Exception as exc:
            logger.exception("settle_picker_failed play=%s user=%s", play_id, interaction.user.id)
            await interaction.edit_original_response(content=f"Could not settle play #{play_id}: {exc}", view=None)
            return
        await interaction.edit_original_response(content=f"Play #{play_id} settled as **{result.upper()}**.", view=None)


class RegradeModal(discord.ui.Modal):
    def __init__(self, play: dict):
        super().__init__(title=f"Regrade Play #{play['id']}")
        self.play = play
        try:
            odds_default = f"{int(play.get('odds')):+d}"
        except (TypeError, ValueError):
            odds_default = ""
        self.legs_left = discord.ui.TextInput(label="Legs left", default=str(play.get("legs") or ""), max_length=3)
        self.odds = discord.ui.TextInput(label="New American odds", default=odds_default, max_length=8)
        self.note = discord.ui.TextInput(label="Note (optional)", style=discord.TextStyle.paragraph, required=False, max_length=500)
        for item in (self.legs_left, self.odds, self.note):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        play_id = int(self.play["id"])
        try:
            outcome = await asyncio.to_thread(
                official_play_service.regrade_play, play_id, self.legs_left.value.strip(), self.odds.value.strip(), self.note.value.strip()
            )
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.edit_message(
            content=f"Play #{play_id} regraded to {outcome['legs_left']}-leg at {outcome['odds']:+d}.",
            view=None,
        )


async def show_play_picker(interaction: discord.Interaction, mode: str, page: int, edit: bool = False) -> None:
    try:
        plays, has_more = await asyncio.to_thread(load_play_page, mode, page)
    except Exception:
        logger.exception("play_picker_load_failed mode=%s user=%s", mode, interaction.user.id)
        plays, has_more = None, False

    if plays is None:
        content, view = "Could not load plays right now. Please retry.", None
    elif not plays and page == 0:
        content = "There are no open plays to settle." if mode == "settle" else "There are no plays from the last 2 days to regrade."
        view = None
    elif not plays:
        # The page emptied out (e.g. plays were settled meanwhile); fall back to the first page.
        await show_play_picker(interaction, mode, 0, edit=edit)
        return
    else:
        content, view = play_picker_content(mode, page), PlayPickerView(interaction.user.id, mode, plays, page, has_more)

    if edit:
        await interaction.edit_original_response(content=content, view=view)
    elif view is not None:
        await interaction.followup.send(content, view=view, ephemeral=True)
    else:
        await interaction.followup.send(content, ephemeral=True)


@bot.tree.command(name="settle", description="Settle an official play from a list of open plays")
async def settle_command(interaction: discord.Interaction):
    if not is_official(interaction.user):
        await interaction.response.send_message("Only officials can settle plays.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    await show_play_picker(interaction, "settle", 0)


@bot.tree.command(name="regrade", description="Regrade an official play from the last 2 days")
async def regrade_command(interaction: discord.Interaction):
    if not is_official(interaction.user):
        await interaction.response.send_message("Only officials can regrade plays.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    await show_play_picker(interaction, "regrade", 0)

@bot.tree.command(name="rankings", description="Show the current team ranking summary")
async def rankings_command(interaction: discord.Interaction):
    if TEAM_STATS_CHANNEL_ID and interaction.channel_id != TEAM_STATS_CHANNEL_ID:
        await interaction.response.send_message(f"Use this command in <#{TEAM_STATS_CHANNEL_ID}>", ephemeral=True)
        return

    try:
        rankings = team_ranking_service.fetch_rankings_from_supabase()
    except RuntimeError:
        rankings = []

    if not rankings:
        rankings = [
            {"team_id": 1, "wins": 0, "losses": 0, "voids": 0, "partials": 0, "net_units": 0.0},
        ]

    embed = discord.Embed(title="Team Rankings", color=discord.Color.gold())
    if not rankings or all(item.get("net_units") == 0 and item.get("wins") == 0 and item.get("losses") == 0 and item.get("voids") == 0 and item.get("partials") == 0 for item in rankings):
        embed.description = "No settled results yet."
    else:
        lines = []
        for index, item in enumerate(rankings[:10], start=1):
            label = item.get("team_name") or f"Team {item['team_id']}"
            lines.append(f"{index}. {label} — {item['net_units']:+.2f}u | W{item['wins']} L{item['losses']} V{item['voids']} P{item['partials']}")
        embed.description = "\n".join(lines)

    await interaction.response.send_message(embed=embed, ephemeral=False)


@bot.tree.command(name="summary", description="Build the current daily team summary")
async def summary_command(interaction: discord.Interaction):
    if TEAM_STATS_CHANNEL_ID and interaction.channel_id != TEAM_STATS_CHANNEL_ID:
        await interaction.response.send_message(f"Use this command in <#{TEAM_STATS_CHANNEL_ID}>", ephemeral=True)
        return

    try:
        report = team_summary_service.build_playmaker_report()
    except RuntimeError:
        await interaction.response.send_message("Supabase is not configured for summary generation.", ephemeral=True)
        return

    periods = report["periods"]
    daily = periods["daily"]
    all_time = periods["all_time"]
    embed = discord.Embed(
        title="Playmaker Picks | Unit Summary",
        description=f"Results for **{report['date']}**",
        color=discord.Color.green(),
    )
    embed.add_field(name="📉 Daily Results", value=f"Net\n**{daily['net']:+g} units**", inline=True)
    embed.add_field(name="✅ Wins", value=f"+{daily['win_units']:g} units", inline=True)
    embed.add_field(name="❌ Losses", value=f"-{daily['loss_units']:g} units", inline=True)
    embed.add_field(name="📋 Results", value=str(daily["results"]), inline=True)
    embed.add_field(name="⏳ Pending", value=f"{report['pending']} bets", inline=True)
    embed.add_field(name="📊 Period Totals", value=(
        f"📈 **7-Day**\n**{periods['seven_day']['net']:+g} units**\n\n"
        f"🔄 **Month-to-Date**\n**{periods['month']['net']:+g} units**\n\n"
        f"🏆 **Year-to-Date**\n**{periods['year']['net']:+g} units**"
    ), inline=False)
    embed.add_field(name="🏆 All-Time Summary", value=(
        f"Net\n**{all_time['net']:+g} units**\n\n"
        f"✅ Wins\n+{all_time['win_units']:g} units\n\n❌ Losses\n-{all_time['loss_units']:g} units\n\n📋 Results\n{all_time['results']}"
    ), inline=False)
    top_lines = []
    breakdown = []
    medals = ["🥇", "🥈", "🥉"]
    for index, (name, data) in enumerate(report["playmakers"][:3]):
        medal = medals[index] if index < len(medals) else "🏅"
        top_lines.append(f"{medal} **{name}** — **{data['net']:+g} units**\n{data['wins']}-{data['losses']} record | {data['rate']}% win rate")
    for name, data in report["playmakers"]:
        breakdown.append(f"**{name}**\nRecord: {data['wins']}-{data['losses']} ({data['rate']}% win rate)")
    embed.add_field(name="Playmaker Breakdown", value="\n".join(breakdown)[:1024] or "No settled plays yet.", inline=False)
    embed.set_footer(text="Updated on request")
    await interaction.response.send_message(embed=embed, ephemeral=False)

    top_embed = discord.Embed(title="Top Playmakers", color=discord.Color.gold())
    top_embed.description = "\n\n".join(top_lines) or "No settled plays yet."
    await interaction.followup.send(embed=top_embed, ephemeral=False)


@bot.tree.command(name="update_tracker", description="Refresh the Unit Summary and Top Playmakers embeds")
async def update_tracker_command(interaction: discord.Interaction):
    try:
        await interaction.response.defer(ephemeral=True)
        if not RESULT_CHANNEL_ID:
            await interaction.followup.send("RESULT_CHANNEL_ID is not configured.", ephemeral=True)
            return
        reconciled = await refresh_tracker_embeds()
        await interaction.followup.send(f"Tracker updated. Reconciled {reconciled} result(s).", ephemeral=True)
    except Exception as exc:
        logger.exception("official_tracker_update_failed")
        if interaction.response.is_done():
            await interaction.followup.send(f"Tracker update failed: {exc}", ephemeral=True)
        else:
            await interaction.response.send_message(f"Tracker update failed: {exc}", ephemeral=True)


async def main() -> None:
    if not DISCORD_TOKEN:
        raise RuntimeError("DISCORD_TOKEN is not configured.")
    await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
