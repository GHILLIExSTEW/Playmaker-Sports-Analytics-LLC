import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from io import BytesIO

import discord
from discord.ext import commands, tasks

from src.config import API_SPORTS_KEY, APPLICATION_ID, CONFIRMATION_CHANNEL_ID, DISCORD_TOKEN, FREE_CHAT_CHANNEL_ID, GUILD_ID, HIGHROLLER_ROLE_ID, IMAGE_INPUT_CHANNEL_ID, MEMBER_BET_CHANNEL_ID, LOSS_REACTION, OFFICIAL_CHANNEL_ID, OFFICIAL_ROLE_IDS, OPERATOR_ROLE_IDS, PARTIAL_REACTION, RESULT_CHANNEL_ID, ROOKIE_ROLE_ID, TEAM_STATS_CHANNEL_ID, TEST_CHANNEL_ID, TESTING, TRACKER_ROLE_ID, TRACKER_START_DATE, VIP_CHAT_CHANNEL_ID, VOID_REACTION, WIN_REACTION
from src.member_bet_vault import MemberBetVault
from src.config import PAID_MEMBER_ROLE_ID, WHOP_MEMBERSHIP_SYNC_ENABLED
from src.membership_access import MembershipRoleSync, WhopMembershipSync
from src.member_stats import MemberStats
from src.member_onboarding import MemberOnboarding
from src.member_activity import MemberActivity
from src.administration import Administration
from src.data_sync import DataSync
from src.play_submission import PlaySubmission
from src.play_settlement import PlaySettlement, build_suggestion_view as build_suggestion_view
from src.play_presentation import (
    PlayPresentation,
    build_play_embed as build_play_embed,
    build_settled_play_embed as build_settled_play_embed,
    build_play_card_embed as build_play_card_embed,
    build_engagement_view as build_engagement_view,
)
from src.website_sync import WebsiteSync
from src.reporting import (
    TRACKER_TIMEZONE as TRACKER_TIMEZONE,
    TRACKER_UPDATE_TIMES as TRACKER_UPDATE_TIMES,
    Reporting,
    operator_plays as operator_plays,
    fetch_official_tracker_rows as fetch_official_tracker_rows,
    parse_tracker_time as parse_tracker_time,
    build_official_tracker_embed as render_official_tracker_embed,
)
from src.services.official_play_service import OfficialPlayService
from src.services.play_features_service import play_features_service
from src.services.website_capper_service import WEBSITE_OWNER_ROLE_ID
from src.services.team_ranking_service import TeamRankingService
from src.services.api_sports_service import api_sports_service
from src.services.api_sports_multi_service import api_sports_multi_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("official_play_bot")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True

class OfficialBot(commands.Bot):
    _shutdown_started = False

    async def close(self) -> None:
        self._shutdown_started = True
        owned_tasks: set[asyncio.Task] = set()
        for component in (whop_membership_sync, member_bet_vault):
            if component is None or component.bot is not self:
                continue
            task = component.reconcile.get_task()
            component.reconcile.cancel()
            if task is not None:
                owned_tasks.add(task)
            if isinstance(component, MemberBetVault):
                component.view.stop()
        for cog in self.cogs.values():
            # Loop descriptors cache their bound instances on the cog after use.
            for value in vars(cog).values():
                if isinstance(value, tasks.Loop):
                    task = value.get_task()
                    value.cancel()
                    if task is not None:
                        owned_tasks.add(task)
            if isinstance(cog, DataSync):
                owned_tasks.update(cog.initial_tasks.values())
                for task in cog.initial_tasks.values():
                    task.cancel()
        try:
            await super().close()
        finally:
            if owned_tasks:
                outcomes = await asyncio.gather(*owned_tasks, return_exceptions=True)
                for outcome in outcomes:
                    if isinstance(outcome, Exception):
                        logger.error("background_shutdown_failed", exc_info=(type(outcome), outcome, outcome.__traceback__))

    async def setup_hook(self) -> None:
        await self.add_cog(MemberStats())
        await self.add_cog(MemberOnboarding())
        await self.add_cog(MemberActivity(
            self, features=play_features_service, resolve_channel=resolve_channel,
        ))
        await self.add_cog(Administration(
            set_testing=set_testing_enabled,
            get_tracker_start=get_tracker_start_date,
            set_tracker_start=set_tracker_start_date,
        ))
        await self.add_cog(WebsiteSync(
            self,
            tracked_operators=tracked_operator_ids,
            play_post_target=play_post_target,
            resolve_channel=resolve_channel,
            can_manage_plays=can_manage_plays,
            staff_alert=send_staff_alert,
        ))
        await self.add_cog(PlayPresentation(
            self, official=official_play_service, features=play_features_service,
            get_testing=get_testing_enabled, play_post_target=play_post_target,
            resolve_channel=resolve_channel, staff_alert=send_staff_alert,
        ))
        await self.add_cog(PlaySubmission(
            official=official_play_service,
            get_testing=get_testing_enabled,
            is_tracked_operator=is_tracked_operator,
            publish_play=publish_play_message,
            official_post_target=official_post_target,
            confirm_recorded=send_confirmation_message,
            repost_play=repost_official_play,
            announce_play=announce_new_play,
            staff_alert=send_staff_alert,
        ))
        await self.add_cog(Reporting(
            self,
            features=play_features_service,
            rankings=team_ranking_service,
            get_tracker_start=get_tracker_start_date,
            settlement_channels=settlement_channel_settings,
            reconcile_reactions=reconcile_open_play_reactions,
            tracked_operators=tracked_operator_ids,
            resolve_channel=resolve_channel,
            can_manage_plays=can_manage_plays,
            staff_alert=send_staff_alert,
        ))
        await self.add_cog(PlaySettlement(
            self, official=official_play_service, features=play_features_service,
            is_official=is_official, can_manage_plays=can_manage_plays,
            fetch_message=fetch_guild_message, update_message=update_play_message,
            refresh_card=refresh_play_card, resolve_channel=resolve_channel,
            staff_alert=send_staff_alert,
            reaction_results=REACTION_RESULTS, channel_settings=settlement_channel_settings,
            user_can_settle=user_can_settle, bang_notifications=send_bang_notifications,
        ))
        await self.add_cog(DataSync(
            self, nfl=api_sports_service, multi=api_sports_multi_service,
            enabled=bool(API_SPORTS_KEY), staff_alert=send_staff_alert,
        ))
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
testing_enabled = TESTING
tracker_start_date = date.fromisoformat(TRACKER_START_DATE) if TRACKER_START_DATE else None


def set_testing_enabled(enabled: bool) -> None:
    global testing_enabled
    testing_enabled = enabled


def get_testing_enabled() -> bool:
    return testing_enabled


def get_tracker_start_date() -> date | None:
    return tracker_start_date


def set_tracker_start_date(cutoff: date | None) -> None:
    global tracker_start_date
    tracker_start_date = cutoff


REACTION_RESULTS = {
    WIN_REACTION: "win",
    LOSS_REACTION: "loss",
    VOID_REACTION: "void",
    PARTIAL_REACTION: "partial",
}


async def update_play_message(message: discord.Message | None, play_id: int, result: str) -> None:
    await get_play_presentation().update_play_message(message, play_id, result)


async def fetch_guild_message(guild: discord.Guild | None, message_id: int) -> discord.Message | None:
    return await get_play_presentation().fetch_guild_message(guild, message_id)


def get_play_presentation() -> PlayPresentation:
    cog = bot.get_cog("PlayPresentation")
    if not isinstance(cog, PlayPresentation):
        raise RuntimeError("Play presentation is not loaded.")
    return cog


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
    return await get_play_presentation().publish_play_message(interaction, payload, target)


def is_tracked_operator(member) -> bool:
    return not TRACKER_ROLE_ID or any(role.id == TRACKER_ROLE_ID for role in getattr(member, "roles", []))


async def tracked_operator_ids() -> set[int] | None:
    """Discord IDs currently holding the tracker role, or None when the role can't be read."""
    if not TRACKER_ROLE_ID:
        return None
    guild = bot.get_guild(GUILD_ID) if GUILD_ID else None
    if guild is None:
        logger.warning("tracker_role_guild_unavailable guild=%s", GUILD_ID)
        return None
    if not guild.chunked:
        await guild.chunk()
    role = guild.get_role(TRACKER_ROLE_ID)
    if role is None:
        logger.warning("tracker_role_missing role=%s", TRACKER_ROLE_ID)
        return None
    return {member.id for member in role.members}


def settlement_channel_settings() -> list[tuple[str, int | None]]:
    # Reactions only count where play cards are posted, plus the official channel for cards published there.
    settings = [play_post_target(), ("OFFICIAL_CHANNEL_ID", OFFICIAL_CHANNEL_ID)]
    return [(name, channel_id) for name, channel_id in dict.fromkeys(settings) if channel_id]


def user_can_settle(user, owner_id: str, guild: discord.Guild | None) -> bool:
    if getattr(user, "bot", False):
        return False
    if str(user.id) == str(owner_id):
        return True
    member = user if isinstance(user, discord.Member) else (guild.get_member(user.id) if guild else None)
    if member is None:
        return False
    return any(role.id == WEBSITE_OWNER_ROLE_ID for role in getattr(member, "roles", []))


async def reconcile_open_play_reactions(plays: list[dict], users: list[dict], channels: list) -> int:
    return await get_play_settlement().reconcile_reactions(plays, users, channels)


def get_play_settlement() -> PlaySettlement:
    cog = bot.get_cog("PlaySettlement")
    if not isinstance(cog, PlaySettlement):
        raise RuntimeError("Play settlement is not loaded.")
    return cog


def build_official_tracker_embed(
    plays: list[dict],
    users: list[dict],
    now: datetime | None = None,
    cutoff: date | None = None,
) -> tuple[discord.Embed, list[str], str]:
    return render_official_tracker_embed(plays, users, now, cutoff if cutoff is not None else get_tracker_start_date())


def get_reporting() -> Reporting:
    cog = bot.get_cog("Reporting")
    if not isinstance(cog, Reporting):
        raise RuntimeError("Reporting is not loaded.")
    return cog


async def update_or_post_tracker_embed(channel, embed: discord.Embed, image: BytesIO | None = None) -> None:
    await get_reporting().update_or_post_tracker_embed(channel, embed, image)


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
    return await get_reporting().refresh_tracker()


@bot.event
async def on_ready():
    if bot._shutdown_started:
        return
    print(f"Logged in as {bot.user}")
    if whop_membership_sync is not None and not whop_membership_sync.reconcile.is_running():
        whop_membership_sync.reconcile.start()
    if member_bet_vault is not None and not member_bet_vault.reconcile.is_running():
        member_bet_vault.reconcile.start()


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    try:
        await get_play_settlement().handle_reaction(payload)
    except Exception:
        logger.exception("reaction_settlement_dispatch_failed")
        await send_staff_alert("reaction_settlement_dispatch", "Play settlement could not handle a reaction. Check bot logs.")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    if member_bet_vault is not None and message.channel.id == MEMBER_BET_CHANNEL_ID:
        await member_bet_vault.handle_message(message)
        return

    submission = bot.get_cog("PlaySubmission")
    if not isinstance(submission, PlaySubmission):
        logger.error("play_submission_unavailable message=%s", message.id)
        await send_staff_alert("play_submission", "Play submission is not loaded. Image intake is unavailable; check bot logs.")
        return
    if await submission.handle_message(message):
        return
    await bot.process_commands(message)


async def send_confirmation_message(interaction: discord.Interaction, payload: dict) -> None:
    payload["leg_records"] = await asyncio.to_thread(official_play_service.get_play_legs, payload["play_id"])
    confirmation = f"Bet recorded: Play #{payload['play_id']}"
    view = get_play_settlement().edit_view(payload["play_id"], interaction.user.id, payload)
    await interaction.followup.send(confirmation, ephemeral=True, view=view)
    try:
        await send_insight_request(int(payload["play_id"]))
    except Exception:
        logger.exception("insight_request_failed play=%s", payload["play_id"])
        await send_staff_alert("insight_request", "A capper insight request failed. Check logs and retry with /request_insights.")


async def send_insight_request(play_id: int) -> bool:
    cog = bot.get_cog("WebsiteSync")
    if not isinstance(cog, WebsiteSync):
        raise RuntimeError("Website synchronization is not loaded; insight request was not sent.")
    return await cog.send_insight_request(play_id)


def is_official(user) -> bool:
    return not OFFICIAL_ROLE_IDS or any(role.id in OFFICIAL_ROLE_IDS for role in getattr(user, "roles", []))


def can_manage_plays(user, guild: discord.Guild | None) -> bool:
    member = user if isinstance(user, discord.Member) else (guild.get_member(user.id) if guild else None)
    return is_official(user) or bool(member and (
        any(role.id in OPERATOR_ROLE_IDS or role.id == WEBSITE_OWNER_ROLE_ID for role in getattr(member, "roles", []))
        or getattr(member.guild_permissions, "manage_guild", False)
    ))


async def send_bang_notifications(source: discord.Message, play_id: int) -> None:
    await get_play_presentation().send_bang_notifications(source, play_id)

STAFF_ALERT_INTERVAL = timedelta(minutes=10)
staff_alert_sent: dict[str, datetime] = {}


async def send_staff_alert(key: str, text: str) -> None:
    """Post a throttled heads-up for staff in the confirmation channel; never raises."""
    now = datetime.now(timezone.utc)
    last = staff_alert_sent.get(key)
    if last is not None and now - last < STAFF_ALERT_INTERVAL:
        return
    staff_alert_sent[key] = now
    try:
        channel = await resolve_channel(CONFIRMATION_CHANNEL_ID, "CONFIRMATION_CHANNEL_ID")
        if channel is not None:
            await channel.send(f"⚠️ {text}"[:2000], allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        logger.exception("staff_alert_failed key=%s", key)


async def refresh_play_card(guild: discord.Guild | None, play_id: int) -> None:
    await get_play_presentation().refresh_play_card(guild, play_id)


async def repost_official_play(channel, source_message_id: int, payload: dict, operator) -> discord.Message | None:
    return await get_play_presentation().repost_official_play(channel, source_message_id, payload, operator)


async def announce_new_play(payload: dict, capper_name: str, card: discord.Message, tracked: discord.Message | None) -> None:
    await get_play_presentation().announce_new_play(payload, capper_name, card, tracked)


async def main() -> None:
    if not DISCORD_TOKEN:
        raise RuntimeError("DISCORD_TOKEN is not configured.")
    async with bot:
        await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
