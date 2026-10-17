from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import discord
from discord import app_commands
from discord.ext import commands, tasks

from src.administration import can_change_settings
from src.config import GUILD_ID, HIGHROLLER_ROLE_ID, OFFICIAL_CHANNEL_ID, TEST_CHANNEL_ID, WINNING_SLIPS_CHANNEL_ID
from src.services import bang_service
from src.services.official_play_service import OfficialPlayService
from src.services.settlement_service import SettlementService
from src.services.play_card_service import play_card_service

logger = logging.getLogger("official_play_bot")


def displayed_status(play: dict) -> str:
    return play["status"] if play.get("tracking_scope") == "official" else "not_official"


def build_bang_embed(play: dict, source_url: str, author, image_url: str | None = None) -> discord.Embed:
    status = displayed_status(play)
    won = status == "win"
    label = "NOT AN OFFICIAL PLAY" if status == "not_official" else status.upper()
    embed = discord.Embed(
        title=f"💰 BANG! 💸 Play #{play['id']} won 🤑" if won else f"BANG CORRECTION: Play #{play['id']} - {label}",
        description=f"[Official Playmaker Picks]({source_url})",
        color=discord.Color.green() if won else discord.Color.orange(),
    )
    embed.set_author(name=author.display_name, icon_url=author.display_avatar.url)
    embed.add_field(name="Stake", value=f"{float(play['units']):g}u")
    embed.add_field(name="Odds", value=f"{int(play['odds']):+d}")
    profit = (
        f"{SettlementService.tally_for_result(status, float(play['units']), play['odds']):+g}u"
        if status in {"win", "loss", "void", "partial"} else "Pending" if status in {"open", "regraded"} else "Not applicable"
    )
    embed.add_field(name="Net profit", value=profit)
    if play.get("play_text"):
        embed.add_field(name="Selections", value=str(play["play_text"])[:1024], inline=False)
    if not won:
        embed.description += "\n**The original win announcement is no longer valid. See the current result above.**"
    embed.set_footer(text=f"Playmaker Picks • Official play {label} • Play ID: {play['id']}")
    if image_url:
        embed.set_image(url=image_url)
    return embed


class BangNotifications(commands.Cog):
    def __init__(
        self, bot: commands.Bot, *, official: OfficialPlayService,
        get_testing: Callable[[], bool], resolve_channel: Callable[..., Awaitable],
        staff_alert: Callable[[str, str], Awaitable[None]],
    ) -> None:
        self.bot, self.official = bot, official
        self.get_testing, self.resolve_channel, self.staff_alert = get_testing, resolve_channel, staff_alert
        self._locks: dict[int, asyncio.Lock] = {}

    def target(self) -> tuple[str, int | None, int | None]:
        if TEST_CHANNEL_ID is not None and TEST_CHANNEL_ID in {OFFICIAL_CHANNEL_ID, WINNING_SLIPS_CHANNEL_ID}:
            raise RuntimeError("TEST_CHANNEL_ID must not be an Official Picks or Winning Slips channel.")
        if self.get_testing():
            return "TEST_CHANNEL_ID", TEST_CHANNEL_ID, None
        if WINNING_SLIPS_CHANNEL_ID == OFFICIAL_CHANNEL_ID:
            raise RuntimeError("Official Picks and Winning Slips must be separate channels.")
        return "WINNING_SLIPS_CHANNEL_ID", WINNING_SLIPS_CHANNEL_ID, HIGHROLLER_ROLE_ID

    def in_mode(self, record: dict) -> bool:
        return bool(record["is_test"] or int(record["channel_id"]) == TEST_CHANNEL_ID) == self.get_testing()

    async def checked_channel(self, guild: discord.Guild, channel_id: int | None, name: str, *, ping: bool = False):
        if not channel_id:
            raise RuntimeError(f"{name} is not configured.")
        channel = await self.resolve_channel(channel_id, name, required=True)
        if channel is None or channel.guild.id != guild.id:
            raise RuntimeError(f"{name} is unavailable or belongs to another server.")
        if guild.me is None:
            raise RuntimeError("The bot's server membership is unavailable.")
        permissions = channel.permissions_for(guild.me)
        needed = ("view_channel", "read_message_history", "send_messages", "embed_links", "attach_files")
        missing = [permission for permission in needed if not getattr(permissions, permission)]
        if missing:
            raise RuntimeError(f"{name} is missing bot permissions: {', '.join(missing)}.")
        if ping:
            role = guild.get_role(HIGHROLLER_ROLE_ID)
            if role is None or role.is_default():
                raise RuntimeError("HIGHROLLER role is missing or invalid; check DISCORD_PLATINUM_ROLE_ID.")
            if not role.mentionable and not permissions.mention_everyone:
                raise RuntimeError("HIGHROLLER is not mentionable and the bot lacks Mention Everyone permission.")
        return channel

    async def validate_configuration(self) -> None:
        try:
            guild = self.bot.get_guild(GUILD_ID) if GUILD_ID else None
            if guild is None:
                raise RuntimeError("GUILD_ID is missing or the bot cannot access the configured server.")
            if guild.icon is None:
                raise RuntimeError("Set a server icon for the generated play-card artwork fallback.")
            name, channel_id, role_id = self.target()
            if not self.get_testing():
                if channel_id == OFFICIAL_CHANNEL_ID:
                    raise RuntimeError("Official Picks and Winning Slips must be separate channels.")
                await self.checked_channel(guild, OFFICIAL_CHANNEL_ID, "OFFICIAL_CHANNEL_ID")
            await self.checked_channel(guild, channel_id, name, ping=role_id is not None)
            await asyncio.to_thread(bang_service.stale, self.get_testing(), TEST_CHANNEL_ID)
        except Exception as exc:
            logger.exception("bang_configuration_failed")
            await self.staff_alert("bang_configuration", f"BANG startup checks failed: {exc}")

    async def cog_load(self) -> None:
        if self.bot.is_ready():
            await self.validate_configuration()
            self.start_jobs()

    def cog_unload(self) -> None:
        self.refresh_bangs.cancel()

    def start_jobs(self) -> None:
        if not self.refresh_bangs.is_running():
            self.refresh_bangs.start()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        await self.validate_configuration()
        self.start_jobs()

    @tasks.loop(minutes=1)
    async def refresh_bangs(self) -> None:
        try:
            if not self.get_testing():
                events = await asyncio.to_thread(bang_service.pending)
                for event in events:
                    if self.get_testing():
                        break
                    if await self.send(None, int(event["play_id"]), force=True):
                        await asyncio.to_thread(bang_service.finish_event, int(event["play_id"]), event["revision"])
            rows = await asyncio.to_thread(bang_service.stale, self.get_testing(), TEST_CHANNEL_ID)
            for row in rows:
                await self.send(None, int(row["play_id"]), create=False)
        except Exception as exc:
            logger.exception("bang_refresh_failed")
            await self.staff_alert("bang_refresh", f"BANG correction scan failed: {exc}")

    async def send(
        self, source: discord.Message | None, play_id: int, *, create: bool = True, force: bool = False,
    ) -> bool:
        if source is not None and (source.guild is None or source.guild.id != GUILD_ID):
            return False
        mode = self.get_testing()
        async with self._locks.setdefault(play_id, asyncio.Lock()):
            try:
                play = await asyncio.to_thread(self.official._fetch_play, play_id)
                guild = source.guild if source is not None else self.bot.get_guild(GUILD_ID)
                if guild is None:
                    raise RuntimeError("The configured Discord server is unavailable.")
                if self.bot.user is None:
                    raise RuntimeError("The bot's identity is unavailable.")
                records = await asyncio.to_thread(bang_service.deliveries, play_id)
                if mode != self.get_testing():
                    raise RuntimeError("Testing mode changed during BANG synchronization. It will be checked again on the next scan.")
                records = [{**row, "is_test": self.get_testing()} for row in records if self.in_mode(row)]
                for record in records:
                    if record["state"] == "delivered" and (force or record["displayed_status"] != displayed_status(play)):
                        await self.update_delivery(guild, play, record)
                name, channel_id, role_id = self.target()
                if not create or displayed_status(play) != "win" or not play.get("message_id"):
                    return True
                if records:
                    if any(row["state"] != "delivered" for row in records):
                        await self.staff_alert(
                            f"bang:{play_id}", f"Play #{play_id} has an unconfirmed BANG delivery. "
                            "No automatic resend was attempted. Inspect the destination and use /bang_status.",
                        )
                    return True
                channel = await self.checked_channel(guild, channel_id, name, ping=role_id is not None)
                await self.deliver(guild, channel, play, source, role_id)
                return True
            except Exception as exc:
                logger.exception("bang_notification_failed play=%s", play_id)
                await self.staff_alert(
                    f"bang:{play_id}",
                    f"BANG delivery/correction failed for Play #{play_id}: {exc}. "
                    "Use /bang_status; do not resend an uncertain delivery without checking Winning Slips.",
                )
                return False

    async def deliver(
        self, guild: discord.Guild, channel, play: dict, source: discord.Message | None,
        role_id: int | None, *, attempt_id: str | None = None,
    ) -> bool:
        play_id, file, send_started = int(play["id"]), None, False
        try:
            if source is None:
                source_name = "TEST_CHANNEL_ID" if self.get_testing() else "OFFICIAL_CHANNEL_ID"
                source_id = TEST_CHANNEL_ID if self.get_testing() else OFFICIAL_CHANNEL_ID
                source_channel = await self.checked_channel(guild, source_id, source_name)
                try:
                    source = await source_channel.fetch_message(int(play["message_id"]))
                except discord.NotFound:
                    await self.staff_alert(f"bang_source:{play_id}", f"Play #{play_id}'s original card is missing. BANG will link to its saved message without an image.")
            if source is not None and source.id != int(play["message_id"]):
                raise RuntimeError("BANG source does not match the play's tracked message.")
            legs = await asyncio.to_thread(self.official.get_play_legs, play_id)
            file = await play_card_service.file(play, guild, source=source, legs=legs)
            image_url = f"attachment://{file.filename}"
            source_channel_id = TEST_CHANNEL_ID if self.get_testing() else OFFICIAL_CHANNEL_ID
            source_url = source.jump_url if source else f"https://discord.com/channels/{guild.id}/{source_channel_id}/{play['message_id']}"
            embed = build_bang_embed(play, source_url, self.bot.user, image_url)
            embed.clear_fields()
            if attempt_id is None:
                attempt_id = await asyncio.to_thread(bang_service.claim, play_id, channel.id, self.get_testing())
                if attempt_id is None:
                    return False
            _, target_id, target_role = self.target()
            if channel.id != target_id or role_id != target_role:
                raise RuntimeError("BANG destination or testing mode changed during delivery. No message was sent.")
            kwargs = {"file": file} if file else {}
            send_started = True
            sent = await channel.send(
                content=f"## 💰 BANG! 💸 🤑\n<@&{role_id}>" if role_id else "## 💰 BANG! 💸 🤑",
                embed=embed, **kwargs,
                allowed_mentions=discord.AllowedMentions(
                    roles=[discord.Object(id=role_id)] if role_id else False,
                    users=False, everyone=False, replied_user=False,
                ),
            )
            await asyncio.to_thread(bang_service.complete, play_id, channel.id, attempt_id, sent.id, "win")
            return True
        except Exception as exc:
            if attempt_id is not None:
                try:
                    await asyncio.to_thread(
                        bang_service.fail, play_id, channel.id, attempt_id, str(exc),
                        send_started and not isinstance(exc, (discord.Forbidden, discord.NotFound)),
                    )
                except Exception:
                    logger.exception("bang_failure_tracking_failed play=%s channel=%s", play_id, channel.id)
            raise
        finally:
            if file is not None:
                file.close()

    async def update_delivery(self, guild: discord.Guild, play: dict, record: dict) -> None:
        channel = await self.checked_channel(guild, int(record["channel_id"]), "BANG destination")
        message = await channel.fetch_message(int(record["message_id"]))
        if self.bot.user is None or message.author.id != self.bot.user.id:
            raise RuntimeError("The tracked BANG is not owned by this bot.")
        source_url = (
            f"https://discord.com/channels/{guild.id}/"
            f"{TEST_CHANNEL_ID if record['is_test'] else OFFICIAL_CHANNEL_ID}/{play['message_id']}"
        )
        file = await play_card_service.file(
            {**play, "status": displayed_status(play)}, guild, source=message,
            legs=await asyncio.to_thread(self.official.get_play_legs, int(play["id"])),
        )
        embed = build_bang_embed(play, source_url, self.bot.user, f"attachment://{file.filename}")
        embed.clear_fields()
        status = displayed_status(play)
        content = "## 💰 BANG! 💸 🤑" if status == "win" else f"## BANG CORRECTION\nPlay #{play['id']}: {status.upper()}"
        try:
            if record["is_test"] != self.get_testing():
                raise RuntimeError("Testing mode changed during BANG correction. No message was edited.")
            await message.edit(content=content, embed=embed, attachments=[file], allowed_mentions=discord.AllowedMentions.none())
        finally:
            file.close()
        await asyncio.to_thread(
            bang_service.complete, int(play["id"]), channel.id, record["attempt_id"], message.id, status,
        )

    async def staff_only(self, interaction: discord.Interaction) -> bool:
        if (
            interaction.guild is None or interaction.guild.id != GUILD_ID
            or getattr(interaction.user, "bot", False) or not can_change_settings(interaction)
        ):
            await interaction.response.send_message("Only server managers or configured operators in this server can manage BANG delivery.", ephemeral=True)
            return False
        await interaction.response.defer(ephemeral=True)
        return True

    @app_commands.command(name="bang_status", description="Staff: inspect BANG delivery and correction tracking")
    @app_commands.guild_only()
    async def status_command(self, interaction: discord.Interaction, play_id: app_commands.Range[int, 1]) -> None:
        if not await self.staff_only(interaction):
            return
        try:
            play = await asyncio.to_thread(self.official._fetch_play, play_id)
            records = await asyncio.to_thread(bang_service.deliveries, play_id)
            lines = [f"Play #{play_id}: {play['tracking_scope']} / {play['status']}"]
            for row in records:
                if not self.in_mode(row):
                    continue
                link = f"https://discord.com/channels/{interaction.guild_id}/{row['channel_id']}/{row['message_id']}" if row["message_id"] else "not recorded"
                lines.append(
                    f"Channel {row['channel_id']}: **{row['state']}**; displayed: {row['displayed_status'] or 'unknown'}\n"
                    f"Message: {link}\nUpdated: {row['updated_at']}\nError: {row['last_error'] or 'none'}"
                )
            if len(lines) == 1:
                lines.append("No delivery is tracked in this mode. /bang_retry can publish an eligible win after staff checks the destination.")
            lines.append("For uncertain delivery, inspect the destination first. Use /bang_recover to link an existing BANG; only use /bang_retry after confirming no message exists.")
            await interaction.followup.send("\n\n".join(lines)[:1900], ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        except Exception as exc:
            logger.exception("bang_status_failed play=%s", play_id)
            await interaction.followup.send(f"Could not read BANG status: {exc}", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="bang_retry", description="Staff: retry only after confirming no BANG exists in the destination")
    @app_commands.guild_only()
    async def retry_command(
        self, interaction: discord.Interaction, play_id: app_commands.Range[int, 1],
        confirmed_no_message: bool,
    ) -> None:
        if not await self.staff_only(interaction):
            return
        if not confirmed_no_message:
            await interaction.followup.send("Inspect the destination, then explicitly confirm no BANG exists. No retry was attempted.", ephemeral=True)
            return
        logger.info("bang_retry_requested play=%s actor=%s", play_id, interaction.user.id)
        async with self._locks.setdefault(play_id, asyncio.Lock()):
            try:
                play = await asyncio.to_thread(self.official._fetch_play, play_id)
                if displayed_status(play) != "win" or not play.get("message_id"):
                    raise ValueError("Only published official winning plays can be retried.")
                name, channel_id, role_id = self.target()
                channel = await self.checked_channel(interaction.guild, channel_id, name, ping=role_id is not None)
                records = await asyncio.to_thread(bang_service.deliveries, play_id)
                if any(self.in_mode(row) and int(row["channel_id"]) != channel.id for row in records):
                    raise ValueError("A delivery is already tracked in another destination. Reconcile it before moving or resending.")
                record = next((row for row in records if int(row["channel_id"]) == channel.id), None)
                attempt_id = None
                if record:
                    if not self.in_mode(record):
                        raise ValueError("This destination has a delivery from another mode. Reconcile its configuration first.")
                    attempt_id = await asyncio.to_thread(
                        bang_service.recover, play_id, channel.id, record["attempt_id"], interaction.user.id, None,
                    )
                delivered = await self.deliver(interaction.guild, channel, play, None, role_id, attempt_id=attempt_id)
                text = (
                    "BANG delivered and tracked. Verify /bang_status for its message."
                    if delivered else "Another delivery already holds the claim. No message was sent; check /bang_status."
                )
                await interaction.followup.send(text, ephemeral=True)
            except Exception as exc:
                logger.exception("bang_retry_failed play=%s actor=%s", play_id, interaction.user.id)
                await self.staff_alert(f"bang:{play_id}", f"Staff retry for Play #{play_id} failed: {exc}. Check /bang_status before another attempt.")
                await interaction.followup.send(f"BANG retry failed: {exc}. Check /bang_status before retrying.", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="bang_recover", description="Staff: link an existing BANG after an uncertain delivery")
    @app_commands.guild_only()
    async def recover_command(
        self, interaction: discord.Interaction, play_id: app_commands.Range[int, 1],
        message_id: str,
    ) -> None:
        if not await self.staff_only(interaction):
            return
        async with self._locks.setdefault(play_id, asyncio.Lock()):
            try:
                if not message_id.isdecimal() or not 1 <= len(message_id) <= 20:
                    raise ValueError("Message ID must be a Discord snowflake.")
                play = await asyncio.to_thread(self.official._fetch_play, play_id)
                name, channel_id, _ = self.target()
                channel = await self.checked_channel(interaction.guild, channel_id, name)
                message = await channel.fetch_message(int(message_id))
                if (
                    self.bot.user is None or message.author.id != self.bot.user.id
                    or not message.embeds or f"Play #{play_id} " not in (message.embeds[0].title or "")
                    or "Playmaker Picks" not in (message.embeds[0].footer.text or "")
                    or "BANG" not in (message.embeds[0].title or "")
                ):
                    raise ValueError("That message is not this bot's BANG for the requested play.")
                records = await asyncio.to_thread(bang_service.deliveries, play_id)
                record = next((row for row in records if int(row["channel_id"]) == channel.id), None)
                if record is None or not self.in_mode(record):
                    raise ValueError("No recoverable claim exists in this mode/destination. Check /bang_status.")
                token = await asyncio.to_thread(
                    bang_service.recover, play_id, channel.id, record["attempt_id"], interaction.user.id, message.id,
                )
                await self.update_delivery(interaction.guild, play, {
                    **record, "message_id": str(message.id), "attempt_id": token, "is_test": self.get_testing(),
                })
                logger.info("bang_recovered play=%s message=%s actor=%s", play_id, message.id, interaction.user.id)
                await interaction.followup.send("Existing BANG linked and synchronized without another role ping.", ephemeral=True)
            except Exception as exc:
                logger.exception("bang_recovery_failed play=%s actor=%s", play_id, interaction.user.id)
                await interaction.followup.send(f"BANG recovery failed: {exc}. Check /bang_status before retrying.", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
