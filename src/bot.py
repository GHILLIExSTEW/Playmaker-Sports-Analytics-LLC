import asyncio
import logging
import re
import uuid
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from io import BytesIO
from zoneinfo import ZoneInfo

import discord
import requests
from discord.ext import commands, tasks

from src.config import APPLICATION_ID, CONFIRMATION_CHANNEL_ID, DISCORD_TOKEN, GUILD_ID, IMAGE_INPUT_CHANNEL_ID, LOSS_REACTION, OFFICIAL_CHANNEL_ID, OFFICIAL_ROLE_IDS, OPERATOR_ROLE_IDS, PARTIAL_REACTION, RESULT_CHANNEL_ID, TEAM_STATS_CHANNEL_ID, TEST_CHANNEL_ID, TESTING, TRACKER_START_DATE, VOID_REACTION, WIN_REACTION
from src.services.official_play_service import OfficialPlayService
from src.services.supabase_service import supabase_service
from src.services.team_ranking_service import TeamRankingService
from src.services.api_sports_player_service import api_sports_player_service
from src.services.play_service import PlayService
from src.services.image_play_service import image_play_service
from src.services.diagnostic_service import diagnostic_service
from src.services.capper_roster_service import capper_roster_service
from src.services.tracker_image_service import render_tracker_image

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("official_play_bot")
TRACKER_TIMEZONE = ZoneInfo("America/New_York")
TRACKER_UPDATE_TIMES = [datetime_time(hour=hour, minute=0, tzinfo=TRACKER_TIMEZONE) for hour in range(24)]

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True

class OfficialBot(commands.Bot):
    async def setup_hook(self) -> None:
        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            print(f"Synced guild commands: {', '.join(command.name for command in synced)}")
        else:
            synced = await self.tree.sync()
            print(f"Synced global commands: {', '.join(command.name for command in synced)}")


bot = OfficialBot(command_prefix="!", intents=intents, application_id=APPLICATION_ID)
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
    text = str(value).replace("Z", "+00:00")
    text = re.sub(r"\.(\d{1,6})\d*(?=[+-]\d{2}:\d{2}$)", lambda match: "." + match.group(1).ljust(6, "0"), text)
    parsed = datetime.fromisoformat(text)
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
    breakdown = []
    medals = ["🥇", "🥈", "🥉"]
    for index, (user_id, data) in enumerate(ranked):
        total = data["win_count"] + data["loss_count"]
        name = names.get(user_id, user_id)
        rate = data["win_count"] / total * 100 if total else 0
        net_units = data["wins"] - data["losses"]
        breakdown.append(f"**{name}** · {data['win_count']}-{data['loss_count']}")
        if index < 3:
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
    embed.add_field(
        name="🏆 Monthly Playmaker Breakdown",
        value="\n\n".join(breakdown)[:1024] or "No settled plays yet.",
        inline=False,
    )
    embed.set_footer(text="Auto-updates hourly • Eastern Time")
    return embed, top_lines


async def update_or_post_tracker_embed(channel, embed: discord.Embed, image: BytesIO | None = None) -> None:
    image_embed = embed
    image_file = None
    if image is not None:
        image_file = discord.File(image, filename="unit-summary.png")
        image_embed = discord.Embed(title=embed.title, color=embed.color)
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


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    guilds = [bot.get_guild(GUILD_ID)] if GUILD_ID else bot.guilds
    for guild in filter(None, guilds):
        try:
            members = [member async for member in guild.fetch_members(limit=None)]
            roster = [{
                "discord_user_id": str(member.id),
                "display_name": member.display_name,
                "role_ids": {role.id for role in member.roles},
            } for member in members]
            count = await asyncio.to_thread(capper_roster_service.sync_guild_members, roster)
            logger.info("capper_roster_synced guild=%s authorized_members=%s", guild.id, count)
        except Exception:
            logger.exception("capper_roster_sync_failed guild=%s", guild.id)
    if not hourly_tracker_update.is_running():
        hourly_tracker_update.start()


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    if before.roles == after.roles and before.display_name == after.display_name:
        return
    try:
        is_authorized = await asyncio.to_thread(
            capper_roster_service.sync_member,
            str(after.id),
            after.display_name,
            {role.id for role in after.roles},
        )
        logger.info("capper_roster_member_updated user=%s authorized=%s", after.id, is_authorized)
    except Exception:
        logger.exception("capper_roster_member_update_failed user=%s", after.id)


@bot.event
async def on_member_remove(member: discord.Member):
    try:
        await asyncio.to_thread(
            capper_roster_service.sync_member,
            str(member.id),
            member.display_name,
            set(),
        )
        logger.info("capper_roster_member_removed user=%s", member.id)
    except Exception:
        logger.exception("capper_roster_member_remove_failed user=%s", member.id)


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


@bot.tree.command(name="settle", description="Settle an official play")
@discord.app_commands.describe(play_id="The play record ID", result="win, loss, void, partial, or regraded")
async def settle_command(interaction: discord.Interaction, play_id: str, result: str):
    if OFFICIAL_ROLE_IDS and not any(role.id in OFFICIAL_ROLE_IDS for role in interaction.user.roles):
        await interaction.response.send_message("Only officials can settle plays.", ephemeral=True)
        return

    try:
        outcome = official_play_service.settle_play(int(play_id), result)
    except ValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    play_record = await asyncio.to_thread(official_play_service._fetch_play, int(play_id))
    message = await fetch_guild_message(interaction.guild, int(play_record["message_id"])) if play_record.get("message_id") else None
    await update_play_message(message, int(play_id), outcome["result"])
    await interaction.response.send_message(f"Play #{play_id} updated.", ephemeral=True)


@bot.tree.command(name="regrade", description="Regrade an official play")
@discord.app_commands.describe(play_id="The play record ID", legs_left="Remaining legs", odds="New American odds", note="Optional regrade notes")
async def regrade_command(interaction: discord.Interaction, play_id: str, legs_left: int, odds: str, note: str | None = None):
    if OFFICIAL_ROLE_IDS and not any(role.id in OFFICIAL_ROLE_IDS for role in interaction.user.roles):
        await interaction.response.send_message("Only officials can regrade plays.", ephemeral=True)
        return

    try:
        outcome = official_play_service.regrade_play(int(play_id), legs_left, odds, note or "")
    except ValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    await interaction.response.send_message(
        f"Play {play_id} regraded to {outcome['legs_left']}-leg at {outcome['odds']}.",
        ephemeral=True,
    )


@bot.tree.command(name="rankings", description="Show the current team ranking summary")
@discord.app_commands.describe(sport="Sport to rank")
async def rankings_command(interaction: discord.Interaction, sport: str):
    if TEAM_STATS_CHANNEL_ID and interaction.channel_id != TEAM_STATS_CHANNEL_ID:
        await interaction.response.send_message(f"Use this command in <#{TEAM_STATS_CHANNEL_ID}>", ephemeral=True)
        return

    try:
        sport_id = int(sport)
    except (TypeError, ValueError):
        await interaction.response.send_message("Choose a sport from the suggestions.", ephemeral=True)
        return

    try:
        sports = await asyncio.to_thread(team_ranking_service.fetch_active_sports)
        selected_sport = next((item for item in sports if int(item["id"]) == sport_id), None)
        if selected_sport is None:
            await interaction.response.send_message("That sport is no longer active.", ephemeral=True)
            return
        rankings = await asyncio.to_thread(team_ranking_service.fetch_rankings_from_supabase, sport_id)
    except RuntimeError:
        await interaction.response.send_message("Could not load rankings from Supabase.", ephemeral=True)
        return

    embed = discord.Embed(title=f"{selected_sport['name']} Team Rankings", color=discord.Color.gold())
    if not rankings:
        embed.description = f"No settled results for {selected_sport['name']} yet."
    else:
        lines = []
        for index, item in enumerate(rankings[:10], start=1):
            label = item.get("team_name") or f"Team {item['team_id']}"
            lines.append(f"{index}. {label} — {item['net_units']:+.2f}u | W{item['wins']} L{item['losses']} V{item['voids']} P{item['partials']}")
        embed.description = "\n".join(lines)

    await interaction.response.send_message(embed=embed, ephemeral=False)


@rankings_command.autocomplete("sport")
async def rankings_sport_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[discord.app_commands.Choice[str]]:
    sports = await asyncio.to_thread(team_ranking_service.fetch_active_sports)
    matches = [sport for sport in sports if current.casefold() in sport["name"].casefold()]
    return [
        discord.app_commands.Choice(name=sport["name"][:100], value=str(sport["id"]))
        for sport in matches[:25]
    ]


def flatten_stat_values(value, prefix: str = "") -> list[str]:
    if isinstance(value, dict):
        lines = []
        for key, item in value.items():
            if key in {"player", "team", "league", "country", "season"}:
                continue
            lines.extend(flatten_stat_values(item, f"{prefix}{key} / "))
        return lines
    if isinstance(value, list):
        return [f"{prefix.rstrip(' /')}: {', '.join(map(str, value))}"] if value else []
    return [f"{prefix.rstrip(' /')}: {value}"]


def selected_league_name(sport_slug: str, league_value: str) -> str:
    league_id, season = api_sports_player_service.parse_league_value(league_value)
    matches = api_sports_player_service.list_leagues(sport_slug)
    row = next((item for item in matches if str(item["league_id"]) == league_id and str(item["current_season"]) == season), None)
    return f"{row['name']} ({season})" if row else f"League {league_id} ({season})"


@bot.tree.command(name="syncplayers", description="Cache player suggestions for a league")
@discord.app_commands.describe(sport="Sport", league="League and current season")
async def syncplayers_command(interaction: discord.Interaction, sport: str, league: str):
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    is_operator = any(role.id in OPERATOR_ROLE_IDS for role in getattr(interaction.user, "roles", []))
    can_manage = bool(member and member.guild_permissions.manage_guild)
    if not (is_operator or can_manage):
        await interaction.response.send_message("Only operators or server managers can sync player data.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    try:
        count = await asyncio.to_thread(api_sports_player_service.sync_player_directory, sport, league)
        league_name = selected_league_name(sport, league)
        await interaction.followup.send(f"Cached {count} player(s) for **{league_name}**.", ephemeral=True)
    except (RuntimeError, ValueError, requests.RequestException) as exc:
        await interaction.followup.send(f"Player sync failed: {exc}", ephemeral=True)
    except Exception:
        logger.exception("player_directory_sync_failed sport=%s", sport)
        await interaction.followup.send("Player sync failed. Check the bot logs for details.", ephemeral=True)


@syncplayers_command.autocomplete("sport")
async def syncplayers_sport_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[discord.app_commands.Choice[str]]:
    sports = await asyncio.to_thread(api_sports_player_service.list_sports, current)
    return [discord.app_commands.Choice(name=item["name"][:100], value=item["slug"]) for item in sports]


@syncplayers_command.autocomplete("league")
async def syncplayers_league_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[discord.app_commands.Choice[str]]:
    sport_slug = getattr(interaction.namespace, "sport", "")
    leagues = await asyncio.to_thread(api_sports_player_service.list_leagues, sport_slug, current)
    return [
        discord.app_commands.Choice(
            name=f"{item['name']} ({item['current_season']})"[:100],
            value=f"{item['league_id']}|{item['current_season']}",
        )
        for item in leagues
    ]


@bot.tree.command(name="playerstats", description="Look up cached player season or game stats")
@discord.app_commands.describe(
    sport="Sport",
    league="League and season",
    player="Player name",
    game="Optional event; choose by matchup and date",
)
async def playerstats_command(
    interaction: discord.Interaction,
    sport: str,
    league: str,
    player: str,
    game: str | None = None,
):
    await interaction.response.defer()
    try:
        player_info, records = await asyncio.to_thread(
            api_sports_player_service.fetch_player_stats,
            sport,
            league,
            player,
            game,
        )
    except (RuntimeError, ValueError, requests.RequestException) as exc:
        await interaction.followup.send(f"Player stats unavailable: {exc}", ephemeral=True)
        return
    except Exception:
        logger.exception("player_stats_lookup_failed sport=%s player=%s", sport, player)
        await interaction.followup.send("Player stats lookup failed. Check the bot logs for details.", ephemeral=True)
        return

    if not records:
        await interaction.followup.send("No stats are available for that player and selection yet.", ephemeral=True)
        return

    lines = []
    for record in records:
        lines.extend(flatten_stat_values(record))
    description = "\n".join(lines)[:4000] or "No statistics were returned."
    embed = discord.Embed(
        title=f"{player_info['name']} | Player Stats",
        description=description,
        color=discord.Color.blue(),
    )
    if player_info.get("team_name"):
        embed.set_author(name=player_info["team_name"])
    league_id, _season = api_sports_player_service.parse_league_value(league)
    selection = await asyncio.to_thread(
        api_sports_player_service.get_game_label,
        sport,
        league_id,
        game,
    ) if game else f"{selected_league_name(sport, league)} season"
    embed.set_footer(text=f"{selection} | Cached via API-Sports")
    await interaction.followup.send(embed=embed, ephemeral=False)


@playerstats_command.autocomplete("sport")
async def playerstats_sport_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[discord.app_commands.Choice[str]]:
    sports = await asyncio.to_thread(api_sports_player_service.list_sports, current)
    return [discord.app_commands.Choice(name=item["name"][:100], value=item["slug"]) for item in sports]


@playerstats_command.autocomplete("league")
async def playerstats_league_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[discord.app_commands.Choice[str]]:
    sport_slug = getattr(interaction.namespace, "sport", "")
    leagues = await asyncio.to_thread(api_sports_player_service.list_leagues, sport_slug, current)
    return [
        discord.app_commands.Choice(
            name=f"{item['name']} ({item['current_season']})"[:100],
            value=f"{item['league_id']}|{item['current_season']}",
        )
        for item in leagues
    ]


@playerstats_command.autocomplete("player")
async def playerstats_player_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[discord.app_commands.Choice[str]]:
    sport_slug = getattr(interaction.namespace, "sport", "")
    league_value = getattr(interaction.namespace, "league", "")
    try:
        league_id, _season = api_sports_player_service.parse_league_value(league_value)
        players = await asyncio.to_thread(
            api_sports_player_service.search_players,
            sport_slug,
            league_value,
            current,
        )
    except ValueError:
        return []
    return [
        discord.app_commands.Choice(
            name=f"{item['name']} · {item.get('team_name') or 'Free agent'}"[:100],
            value=str(item["player_id"]),
        )
        for item in players[:25]
    ]


@playerstats_command.autocomplete("game")
async def playerstats_game_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[discord.app_commands.Choice[str]]:
    sport_slug = getattr(interaction.namespace, "sport", "")
    league_value = getattr(interaction.namespace, "league", "")
    try:
        league_id, _season = api_sports_player_service.parse_league_value(league_value)
        games = await asyncio.to_thread(
            api_sports_player_service.search_cached_games,
            sport_slug,
            league_id,
            current,
        )
    except ValueError:
        return []
    return [
        discord.app_commands.Choice(
            name=f"{item['event_name']} · {item['start_at'][:10]}"[:100],
            value=str(item["event_id"]),
        )
        for item in games
    ]


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
