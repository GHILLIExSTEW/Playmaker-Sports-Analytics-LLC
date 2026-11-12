from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from io import BytesIO

import discord
from discord import app_commands
from discord.ext import commands, tasks

from src.capper_ballots import BALLOT_CLEANUP_TIME, CapperBallots
from src.config import CAPPER_BALLOT_CHANNEL_ID, CONFIRMATION_CHANNEL_ID, FREE_PLAYS_CHANNEL_ID, GUILD_ID, TRACKER_ROLE_ID
from src.interaction_views import PreparedModalView
from src.forum_health import CheckedForum
from src.services.ballot_vote_service import approval_text, tail_rating
from src.services.image_play_service import ImagePlayService
from src.services.official_play_service import OfficialPlayService
from src.services.play_service import PlayService
from src.services.photo_service import accepted_photo, sanitize_photo
from src.services.supabase_service import supabase_service
from src.capper_review_records import ReviewRecords
from src.services.slip_review_service import (
    SlipReviewStore, confirmed_legs, friendly_time, parse_cutoff, parse_event_start, response_choices, review_time,
)

logger = logging.getLogger("official_play_bot")
TAIL = "\N{THUMBS UP SIGN}"
ACKNOWLEDGEMENT_THREAD_ID = 1565183972244201622
ACKNOWLEDGEMENT_EMOJI = "\N{HANDSHAKE}"
SUBMISSION_NOTICE = (
    "Before submitting a capper slip\n\n"
    f"Read this notice and react to this opening message with {ACKNOWLEDGEMENT_EMOJI} to acknowledge it "
    "BEFORE creating a ballot post. Posts submitted without this acknowledgement are deleted; "
    "the bot will DM you the acknowledgement link. After acknowledging, submit a fresh post.\n\n"
    "ONLY this READ FIRST rules thread is read-only. React here to acknowledge; do not reply here. "
    "All separate ballot posts allow replies and discussion. Create your own ballot post for your slip.\n\n"
    "Title = the teams or players on your slip. Examples: Lakers vs Celtics; Musetti vs Struff; "
    "for multiple games: NBA 3-leg parlay. No special title format is required.\n"
    "Attach one static JPEG, PNG or WebP slip image (10 MB maximum), then press Post. "
    "The message/body can be blank when the image is attached. The bot asks you to confirm details and times AFTER posting.\n"
    "Crop/redact names, account IDs, balances and barcodes BEFORE uploading; metadata removal cannot hide visible details.\n\n"
    "By submitting, you request OpenAI image/text extraction and a private review. This bot uses inference, "
    "not training on Discord content. Check and correct the extracted details before confirming. "
    "Confirmation, voting and discussion stay in your post.\n\n"
    "If not approved, your slip becomes a regular tracked Free Play, not an official pick. Missing units? "
    "Choose a stake above 0 and up to 3u (parlays never above 1u) using the team-channel button; the card posts automatically. "
    "At 3 AM Eastern, completed reviews are copied to the private owner/bot records forum before deletion here. "
    "Screenshots and detailed submission archives are removed 365 days after review and bet resolution; "
    "unresolved reviews/bets remain protected. Capper, bet, approval and result summaries remain for statistics. "
    "Discord and processors handle data under their own policies; this does not erase their backups or other people's copies.\n\n"
    "For access, corrections or deletion requests, contact the server owner or support@playmakersportsanalytics.com."
)


def review_view(message_id: int, *, draft: bool = False) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for action, label, style in (
        [("confirm", "Confirm details + set cutoff", discord.ButtonStyle.primary)] if draft else []
    ) + [("cancel", "Cancel (author only)", discord.ButtonStyle.danger)]:
        view.add_item(discord.ui.Button(
            label=label, style=style, custom_id=f"pm:sr:{message_id}:{action}",
        ))
    view.stop()
    return view


def review_embed(row: dict) -> discord.Embed:
    parsed = row["review"]["parsed"]
    embed = discord.Embed(title="Capper slip review", color=discord.Color.orange())
    embed.description = "\n".join(
        f"{index}. {discord.utils.escape_markdown(leg['selection'])} "
        f"({int(leg['odds']):+d})" if leg.get("odds") else
        f"{index}. {discord.utils.escape_markdown(leg['selection'])} (odds needed)"
        for index, leg in enumerate(parsed["legs"], 1)
    )[:4096]
    if row["review"].get("image_filename"):
        embed.set_image(url=f"attachment://{row['review']['image_filename']}")
    if not row.get("cutoff_at"):
        embed.add_field(
            name="Awaiting author confirmation",
            value="Check every leg, confirm the earliest event start, and set your voting cutoff. No responses count yet.",
            inline=False,
        )
    else:
        cutoff, start = review_time(row["cutoff_at"]), review_time(row["event_starts_at"])
        embed.add_field(
            name="Author-set voting cutoff",
            value=f"<t:{int(cutoff.timestamp())}:F> (<t:{int(cutoff.timestamp())}:R>)\n"
                  f"Earliest event: <t:{int(start.timestamp())}:F>",
            inline=False,
        )
        choices = response_choices(row)
        tails = sum(value == "yes" for value in choices.values())
        size = len(row["eligible_voter_ids"])
        rating = tail_rating(tails, size, len(parsed["legs"]))
        embed.add_field(
            name="Live responses",
            value=f"{tails} tailed (author included) | {size - tails} have not tailed",
            inline=False,
        )
        embed.add_field(
            name="Provisional result",
            value=f"Currently qualifies - projected {rating[1]}u" if rating else "Does not currently qualify - personal only",
            inline=False,
        )
        embed.add_field(
            name="How to respond",
            value=f"{TAIL} Tail this slip if you would play it; the author's confirmation is their tail.\n"
                  "There is no down vote or pass - not reacting simply means no tail."
                  " One tail from another capper publishes the official at 2u; two or more lock the 3u maximum."
                  " Parlays never exceed 1u, and slips with 3+ legs cannot go official."
                  " Locks at the cutoff or when every capper has tailed. Published units never change.",
            inline=False,
        )
    return embed


class SlipConfirmation(discord.ui.Modal, title="Confirm slip and set voting cutoff"):
    def __init__(self, cog: CapperSlipReviews, row: dict) -> None:
        super().__init__(timeout=600)
        self.cog, self.row = cog, row
        self.legs = discord.ui.TextInput(
            label="One leg per line: Selection | Odds",
            style=discord.TextStyle.paragraph,
            default="\n".join(
                f"{leg['selection']} | {int(leg['odds']):+d}" if leg.get("odds") else f"{leg['selection']} | "
                for leg in row["review"]["parsed"]["legs"]
            ),
            max_length=4000,
        )
        starts = [leg.get("event_start") for leg in row["review"]["parsed"]["legs"]]
        default_start = ""
        if all(isinstance(value, str) and value for value in starts):
            try:
                default_start = friendly_time(min(review_time(value) for value in starts))
            except ValueError:
                logger.info("slip_reader_start_requires_confirmation message=%s", row["message_id"])
        self.start = discord.ui.TextInput(
            label="When does the first game start? (Eastern)",
            placeholder="Tonight 7:30 PM  |  Sat 1pm  |  Oct 22 8:15 PM", default=default_start, max_length=40,
        )
        self.cutoff = discord.ui.TextInput(
            label="Voting closes (optional)",
            placeholder="Blank = 10 min before start  |  30 min before  |  6:45 PM",
            required=False, max_length=40,
        )
        for item in (self.legs, self.start, self.cutoff):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            await self.cog.confirm(
                interaction, int(self.row["message_id"]), str(self.legs), str(self.start), str(self.cutoff),
            )
        except Exception as exc:
            logger.exception("slip_confirmation_failed message=%s", self.row["message_id"])
            await interaction.followup.send(f"Review not opened: {str(exc)[:1000]}", ephemeral=True)


class FreePlayUnitsModal(discord.ui.Modal, title="Choose Free Play units"):
    def __init__(self, cog: CapperSlipReviews, ballot_id: int, prompt_id: int) -> None:
        super().__init__(timeout=300)
        self.cog, self.ballot_id, self.prompt_id = cog, ballot_id, prompt_id
        self.units = discord.ui.TextInput(
            label="Unit stake (above 0, max 3u; parlays 1u)",
            placeholder="For example: 1 or 1.5",
            min_length=1,
            max_length=6,
        )
        self.add_item(self.units)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            message, play_id = await self.cog.submit_free_play_units(
                interaction, self.ballot_id, self.prompt_id, str(self.units),
            )
        except ValueError as exc:
            await interaction.followup.send(
                f"{str(exc)[:500]} Click the button again to enter a valid stake.",
                ephemeral=True,
            )
        except Exception as exc:
            logger.exception("free_play_units_submission_failed ballot=%s", self.ballot_id)
            await self.cog.publisher.staff_alert(
                f"free_play_units:{self.ballot_id}",
                f"Free Play units for slip {self.ballot_id} need reconciliation. "
                f"Inspect the ballot and Free Plays before retrying. Error: {str(exc)[:800]}",
            )
            await interaction.followup.send(
                "The Free Play could not be posted automatically. Staff have been alerted; please don't submit again.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                f"Posted as regular Free Play #{play_id}: {message.jump_url}",
                ephemeral=True,
            )


class CapperSlipReviews(commands.Cog):
    def __init__(
        self, bot: commands.Bot, *, official: OfficialPlayService,
        resolve_channel: Callable[..., Awaitable],
        is_tracked_operator: Callable[[discord.abc.User], bool],
        staff_alert: Callable[[str, str], Awaitable[None]],
        get_testing: Callable[[], bool],
        tracked_operators: Callable[[], Awaitable[set[int] | None]],
        request_insight: Callable[[int], Awaitable[bool]],
        publish_free_play: Callable[[discord.Guild, discord.abc.User, dict, bytes], Awaitable[discord.Message]],
        images: ImagePlayService | None = None, store: SlipReviewStore | None = None,
    ) -> None:
        self.bot = bot
        self.request_insight = request_insight
        self.publish_free_play = publish_free_play
        self.store = store if store is not None else SlipReviewStore()
        self.publisher = CapperBallots(
            bot, store=self.store, official=official, resolve_channel=resolve_channel,
            is_tracked_operator=is_tracked_operator, staff_alert=staff_alert,
            get_testing=get_testing, tracked_operators=tracked_operators, images=images,
        )
        self.lock = asyncio.Lock()
        self.review_locks: dict[int, asyncio.Lock] = {}
        self.discovery_locks: dict[int, asyncio.Lock] = {}
        self.intake_ids: set[int] = set()
        self.prompt_edits: dict[int, tuple] = {}
        self.needs_reconciliation = True
        self.records = ReviewRecords(bot, resolve_channel)
        self.intake_queue: asyncio.Queue[int] = asyncio.Queue(maxsize=100)
        self.queued_ids: set[int] = set()
        self.scan_lock = asyncio.Lock()
        self.processing_slots = asyncio.Semaphore(4)
        self.checked_forum = CheckedForum(resolve_channel, CAPPER_BALLOT_CHANNEL_ID, "CAPPER_BALLOT_CHANNEL_ID", records=False)
        self.submission_notice_ready = False

    def review_lock(self, message_id: int) -> asyncio.Lock:
        return self.review_locks.setdefault(message_id, asyncio.Lock())

    async def cog_load(self) -> None:
        if self.bot.is_ready():
            await self.on_ready()

    def cog_unload(self) -> None:
        self.process_reviews.cancel()
        self.scan_slips.cancel()
        self.daily_cleanup.cancel()
        self.refresh_records.cancel()
        self.intake_worker.cancel()

    async def free_play_units_button(self, interaction: discord.Interaction, message_id: int) -> None:
        try:
            row = await asyncio.to_thread(self.store.get, message_id)
            review = row.get("review") or {}
            delivery = review.get("personal_free_play") or {}
            if (
                interaction.guild_id != GUILD_ID
                or interaction.channel_id != CONFIRMATION_CHANNEL_ID
                or interaction.user.bot
                or self.publisher.get_testing()
                or interaction.message is None
                or str(interaction.message.id) != delivery.get("prompt_message_id")
                or str(interaction.channel_id) != delivery.get("prompt_channel_id")
                or str(interaction.user.id) != str(review.get("author_id"))
                or delivery.get("state") != "awaiting_units"
                or row.get("state") not in {"failed", "rejected"}
            ):
                await interaction.response.send_message(
                    "This Free Play unit request is closed or does not belong to you.",
                    ephemeral=True,
                )
                return
            await interaction.response.send_modal(
                FreePlayUnitsModal(self, message_id, int(interaction.message.id)),
            )
        except Exception as exc:
            logger.exception("free_play_units_button_failed ballot=%s", message_id)
            await self.publisher.staff_alert(
                f"free_play_units:{message_id}",
                f"Free Play unit request for slip {message_id} needs reconciliation: {str(exc)[:800]}",
            )
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "The unit request could not be opened. Staff have been alerted.",
                    ephemeral=True,
                )

    @staticmethod
    def free_play_units_view(message_id: int) -> discord.ui.View:
        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(
            label="Choose Free Play units",
            style=discord.ButtonStyle.primary,
            custom_id=f"pm:freeplay_units:{message_id}",
        ))
        return view

    async def restore_free_play_units_buttons(self) -> None:
        rows = await asyncio.to_thread(self.store.awaiting_personal_free_play_units)
        for row in rows:
            message_id = int(row["message_id"])
            try:
                if (
                    CONFIRMATION_CHANNEL_ID is None
                    or str(row["prompt_channel_id"]) != str(CONFIRMATION_CHANNEL_ID)
                ):
                    raise RuntimeError("Saved unit prompt is not in the configured team discussion channel.")
                channel = await self.publisher.resolve_channel(
                    CONFIRMATION_CHANNEL_ID, "CONFIRMATION_CHANNEL_ID", required=True,
                )
                if channel.guild.id != GUILD_ID:
                    raise RuntimeError("Saved unit prompt belongs to a different guild.")
                prompt = await channel.fetch_message(int(row["prompt_message_id"]))
                if prompt.author.id != self.bot.user.id:
                    raise RuntimeError("Saved unit prompt is not bot-owned.")
                custom_id = f"pm:freeplay_units:{message_id}"
                existing_ids = {
                    item.custom_id
                    for action_row in prompt.components
                    for item in action_row.children
                    if item.custom_id
                }
                if custom_id in existing_ids:
                    continue
                if existing_ids:
                    raise RuntimeError("Saved unit prompt has unexpected controls; it was not overwritten.")
                await prompt.edit(view=self.free_play_units_view(message_id))
            except Exception as exc:
                logger.exception("free_play_units_button_restore_failed ballot=%s", message_id)
                await self.publisher.staff_alert(
                    f"free_play_units_button_restore:{message_id}",
                    f"Saved unit request for slip {message_id} needs reconciliation: {str(exc)[:800]}",
                )

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        self.needs_reconciliation = True
        report = await self.health_report(starting=True)
        if any(line.startswith("FAIL") for line in report):
            await self.publisher.staff_alert("bot_health", "\n".join(report)[:1800])
        try:
            await self.ensure_submission_notice()
            self.submission_notice_ready = True
        except Exception as exc:
            self.submission_notice_ready = False
            logger.exception("capper_submission_notice_failed")
            await self.publisher.staff_alert("capper_submission_notice", f"Submission notice needs attention: {str(exc)[:1500]}")
        try:
            await self.restore_free_play_units_buttons()
        except Exception as exc:
            logger.exception("free_play_units_button_restore_failed")
            await self.publisher.staff_alert(
                "free_play_units_button_restore",
                f"Saved Free Play unit prompts could not be refreshed: {str(exc)[:1000]}",
            )
        if not self.process_reviews.is_running():
            self.process_reviews.start()
        if not self.scan_slips.is_running():
            self.scan_slips.start()
        if not self.daily_cleanup.is_running():
            self.daily_cleanup.start()
        if not self.refresh_records.is_running():
            self.refresh_records.start()
        if not self.intake_worker.is_running():
            self.intake_worker.start()
    async def ensure_submission_notice(self) -> None:
        forum = await self.channel()
        if not isinstance(forum, discord.ForumChannel):
            return
        store = self.records.store
        rows = await asyncio.to_thread(
            supabase_service.select, "capper_forum_notices", "*", {"forum_channel_id": str(forum.id)},
        )
        notice = (rows.data or [None])[0]
        if notice is None:
            if not await asyncio.to_thread(store.claim, "capper_forum_notices", {
                "forum_channel_id": str(forum.id), "thread_id": "sending",
            }, "forum_channel_id"):
                raise RuntimeError("Notice creation is already claimed; inspect Discord before retrying.")
            created = await forum.create_thread(
                name="READ FIRST - Slip privacy and submission rules", content=SUBMISSION_NOTICE,
                auto_archive_duration=10080,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            thread = created.thread
            await asyncio.to_thread(store.update, "capper_forum_notices", {"thread_id": str(thread.id)},
                                    {"forum_channel_id": str(forum.id)})
        else:
            if notice["thread_id"] == "sending":
                raise RuntimeError("Submission notice delivery is uncertain. Link its verified thread ID before retrying.")
            thread = await self.publisher.resolve_channel(int(notice["thread_id"]), "submission notice", required=True)
            if not isinstance(thread, discord.Thread) or thread.parent_id != forum.id:
                raise RuntimeError("Saved submission notice is outside the configured forum.")
            message = await thread.fetch_message(thread.id)
            if message.author.id != self.bot.user.id:
                raise RuntimeError("Submission notice is not bot-owned.")
            if thread.archived:
                thread = await thread.edit(archived=False, reason="Maintain capper submission notice")
            if message.content != SUBMISSION_NOTICE:
                await message.edit(content=SUBMISSION_NOTICE, allowed_mentions=discord.AllowedMentions.none())
        if not thread.flags.pinned:
            await thread.edit(pinned=True, reason="Keep privacy and submission rules visible before upload")
        if thread.id != ACKNOWLEDGEMENT_THREAD_ID:
            raise RuntimeError("The saved submission notice does not match the required acknowledgement thread.")
        message = await thread.fetch_message(thread.id)
        await message.add_reaction(ACKNOWLEDGEMENT_EMOJI)
        async for reply in thread.history(limit=None, after=discord.Object(id=thread.id)):
            await self.delete_notice_reply(reply)

    @commands.Cog.listener()
    async def on_disconnect(self) -> None:
        self.needs_reconciliation = True

    @commands.Cog.listener()
    async def on_resumed(self) -> None:
        self.needs_reconciliation = True
        await self.scan_slips.coro(self)

    async def queue_intake(self, thread_id: int) -> None:
        if thread_id in self.queued_ids or thread_id in self.intake_ids or self.publisher.get_testing():
            return
        try:
            self.intake_queue.put_nowait(thread_id)
            self.queued_ids.add(thread_id)
        except asyncio.QueueFull:
            logger.error("slip_intake_queue_full thread=%s", thread_id)
            await self.publisher.staff_alert(
                "slip_intake_queue", "Slip intake queue is full. Five-minute reconciliation will recover missed posts.",
            )

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread) -> None:
        if thread.guild.id == GUILD_ID and thread.parent_id == CAPPER_BALLOT_CHANNEL_ID:
            await self.queue_intake(thread.id)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if (isinstance(message.channel, discord.Thread)
                and message.guild is not None and message.guild.id == GUILD_ID
                and message.channel.parent_id == CAPPER_BALLOT_CHANNEL_ID
                and message.channel.id == ACKNOWLEDGEMENT_THREAD_ID):
            if message.id != ACKNOWLEDGEMENT_THREAD_ID:
                await self.delete_notice_reply(message)
            return
        if (not message.author.bot and isinstance(message.channel, discord.Thread)
                and message.guild is not None and message.guild.id == GUILD_ID
                and message.channel.parent_id == CAPPER_BALLOT_CHANNEL_ID
                and message.id == message.channel.id):
            await self.queue_intake(message.id)

    async def delete_notice_reply(self, message: discord.Message) -> None:
        try:
            await message.delete(reason="Submission rules thread is read-only; acknowledge with a reaction")
        except discord.NotFound:
            logger.info("slip_notice_reply_already_deleted message=%s", message.id)
        except discord.HTTPException:
            logger.exception("slip_notice_reply_delete_failed message=%s", message.id)
            await self.publisher.staff_alert(
                "slip_notice_read_only",
                f"Could not delete reply {message.id} in the read-only submission rules thread "
                f"{ACKNOWLEDGEMENT_THREAD_ID}. Check Manage Messages permission and bot logs.",
            )
            raise

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        if payload.guild_id == GUILD_ID and payload.message_id == payload.channel_id:
            channel = self.bot.get_channel(payload.channel_id)
            if isinstance(channel, discord.Thread) and channel.parent_id == CAPPER_BALLOT_CHANNEL_ID:
                await self.queue_intake(channel.id)

    @tasks.loop(seconds=1)
    async def intake_worker(self) -> None:
        async def consume(thread_id: int) -> None:
            try:
                if self.publisher.get_testing() or not self.submission_notice_ready:
                    return
                if await asyncio.to_thread(self.store.find, thread_id, columns="message_id"):
                    return
                thread = await self.publisher.resolve_channel(thread_id, "capper intake thread", required=True)
                if (not isinstance(thread, discord.Thread) or thread.guild.id != GUILD_ID
                        or thread.parent_id != CAPPER_BALLOT_CHANNEL_ID):
                    raise RuntimeError("Queued slip is outside the configured forum.")
                await self.discover_thread(await self.channel(), thread)
            except discord.NotFound:
                logger.info("slip_intake_thread_deleted thread=%s", thread_id)
            except Exception as exc:
                logger.exception("slip_event_intake_failed thread=%s", thread_id)
                await self.publisher.staff_alert("slip_event_intake", f"Slip intake failed: {str(exc)[:1500]}")
            finally:
                self.queued_ids.discard(thread_id)
                self.intake_queue.task_done()

        batch = []
        for _ in range(2):
            try:
                batch.append(self.intake_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        await asyncio.gather(*(consume(thread_id) for thread_id in batch))

    @intake_worker.before_loop
    async def before_intake_worker(self) -> None:
        await self.bot.wait_until_ready()

    @staticmethod
    def source_channel_id(row: dict) -> int:
        return int(row["review"].get("source_channel_id") or CAPPER_BALLOT_CHANNEL_ID)

    def check_review_channel(self, channel_id: int, row: dict) -> None:
        if channel_id != self.source_channel_id(row):
            raise ValueError("This control does not belong to the saved review's channel.")

    async def channel(self, row: dict | None = None):
        channel_id = self.source_channel_id(row) if row is not None else CAPPER_BALLOT_CHANNEL_ID
        channel = await self.checked_forum.resolve() if channel_id == CAPPER_BALLOT_CHANNEL_ID else await self.publisher.resolve_channel(
            channel_id, "saved capper review thread", required=True,
        )
        if channel.guild.id != GUILD_ID:
            raise RuntimeError("Slip reviews must use the configured guild.")
        if isinstance(channel, discord.ForumChannel) and channel.permissions_for(channel.guild.default_role).view_channel:
            raise RuntimeError("The capper ballot forum must not be visible to @everyone.")
        if isinstance(channel, discord.Thread):
            if channel.parent_id != CAPPER_BALLOT_CHANNEL_ID:
                raise RuntimeError("Review thread is outside the configured capper forum.")
            if channel.locked:
                raise RuntimeError("Review thread is locked. Staff must unlock it before processing.")
            if channel.archived:
                channel = await channel.edit(archived=False, reason="Resume saved capper slip review")
        return channel

    async def health_report(self, *, starting: bool = False) -> list[str]:
        report = []
        for name in ("message_content", "members", "guilds", "guild_messages", "guild_reactions"):
            enabled = getattr(self.bot.intents, name)
            report.append(f"{'OK' if enabled else 'FAIL'} Gateway intent: {name}")
        for label, checker in (("Ballot forum", self.checked_forum), ("Private records forum", self.records.checked_forum)):
            try:
                await checker.resolve(force=True)
                report.append(f"OK {label}: access and permissions")
            except Exception:
                logger.exception("bot_health_forum_failed target=%s", label)
                report.append(f"FAIL {label}: check bot logs for permission/privacy details")
        try:
            state = await asyncio.to_thread(self.store.rpc, "capper_review_health")
            report.append(f"OK DB compatibility schema v{state['version']}; pending={state['pending']}, "
                          f"interrupted={state['interrupted']}, record jobs={state['record_jobs']}")
            if state["interrupted"] or state["uncertain_records"]:
                report.append(f"FAIL Staff reconciliation required: interrupted={state['interrupted']}; "
                              f"uncertain records/notices={state['uncertain_records']}")
        except Exception:
            logger.exception("bot_health_database_failed")
            report.append("FAIL Database/schema readiness: apply the compatibility migration and check bot logs")
        for name in ("scan_slips", "process_reviews", "intake_worker", "daily_cleanup", "refresh_records"):
            loop = getattr(self, name)
            report.append(f"{'FAIL' if loop.failed() else 'OK' if loop.is_running() else 'WAIT' if starting else 'FAIL'} Worker {name}")
        if not starting:
            report.append(f"{'OK' if self.submission_notice_ready else 'FAIL'} Submission privacy notice")
        report.append(f"Testing mode: {'enabled (intake/publication paused)' if self.publisher.get_testing() else 'disabled'}")
        report.append(f"Intake queue: {self.intake_queue.qsize()}/100; in-flight={len(self.intake_ids)}")
        report.append("Portal privileged-intent toggles require manual verification; this reports requested intents only.")
        return report

    @app_commands.command(name="bot_health", description="Staff: inspect Discord permissions, database and review workers")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def health_command(self, interaction: discord.Interaction) -> None:
        if (interaction.guild_id != GUILD_ID or interaction.guild is None or not isinstance(interaction.user, discord.Member)
                or not (interaction.user.id == interaction.guild.owner_id or interaction.user.guild_permissions.manage_guild)):
            await interaction.response.send_message("Only server management can inspect bot health.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        report = await self.health_report()
        await interaction.followup.send("\n".join(report)[:1900], ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    async def forum_threads(
        self, forum: discord.ForumChannel, *, since: datetime | None = None,
    ) -> list[discord.Thread]:
        active = forum.threads
        threads = {thread.id: thread for thread in active if thread.parent_id == forum.id}
        async for thread in forum.archived_threads(limit=None):
            if since is not None and thread.archive_timestamp is not None and thread.archive_timestamp < since:
                break
            threads[thread.id] = thread
        return list(threads.values())

    async def edit_prompt(self, channel, prompt, *, final: bool = True, **kwargs) -> None:
        """Edit the review prompt, skipping no-op edits; Discord caps edits to messages older than an hour (30046)."""
        embed = kwargs.get("embed")
        signature = (embed.to_dict(), kwargs.get("view") is None) if embed is not None else None
        if signature is not None and self.prompt_edits.get(prompt.id) == signature:
            return
        try:
            await prompt.edit(**kwargs)
            if signature is not None:
                self.prompt_edits[prompt.id] = signature
        except discord.HTTPException as exc:
            if exc.code != 30046:
                raise
            logger.info("slip_prompt_edit_limit message=%s final=%s", prompt.id, final)
            if final and kwargs.get("embed") is not None:
                kwargs.pop("attachments", None)
                kwargs["embed"] = kwargs["embed"].copy()
                kwargs["embed"].set_image(url=None)
                await channel.send(
                    f"[Slip review update]({prompt.jump_url})", allowed_mentions=discord.AllowedMentions.none(), **kwargs,
                )

    async def fail(self, message_id: int, reason: str) -> None:
        logger.error("slip_review_failed message=%s reason=%s", message_id, reason)
        try:
            await asyncio.to_thread(self.store.fail, message_id, reason[:1500])
            row = await asyncio.to_thread(self.store.get, message_id)
        except Exception as exc:
            logger.exception("slip_review_failure_fallback_claim_failed message=%s", message_id)
            await self.publisher.staff_alert(
                f"slip:{message_id}",
                f"Slip review {message_id} needs reconciliation: {reason[:1500]}. "
                f"Could not confirm the failure or Free Play state. Inspect the saved play and both "
                f"Discord channels before retrying. Error: {str(exc)[:800]}",
            )
            raise

        if row["state"] != "failed":
            await self.publisher.staff_alert(
                f"slip:{message_id}",
                f"Slip review {message_id} failed: {reason[:800]}. Its saved state is {row['state']}; "
                "no Free Play was sent. Reconcile before retrying.",
            )
            return
        if FREE_PLAYS_CHANNEL_ID is None:
            await self.publisher.staff_alert(
                f"slip:{message_id}",
                f"Slip review {message_id} failed: {reason[:800]}. FREE_PLAYS_CHANNEL_ID is not configured; "
                "no Free Play was sent.",
            )
            return
        unit_error = self.capper_units_error(row)
        if unit_error:
            try:
                await self.request_personal_free_play_units(row, reason)
            except Exception as exc:
                logger.exception("free_play_units_request_failed message=%s", message_id)
                await self.publisher.staff_alert(
                    f"slip:{message_id}",
                    f"Slip review {message_id} failed: {reason[:800]}. {unit_error} "
                    f"Could not ask the capper for units; no Free Play was sent. Error: {str(exc)[:500]}",
                )
                return
            await self.publisher.staff_alert(
                f"slip:{message_id}",
                f"Slip review {message_id} failed: {reason[:800]}. {unit_error} "
                "The author was asked to choose units in the team channel; the regular Free Play will post after submission.",
            )
            return
        delivery = (row.get("review") or {}).get("personal_free_play")
        if delivery:
            await self.publisher.staff_alert(
                f"slip:{message_id}",
                f"Slip review {message_id} needs reconciliation: {reason[:800]}. A regular Free Play is "
                f"already marked {delivery.get('state', 'in flight')}; inspect Free Plays before retrying.",
            )
            return

        try:
            message, play_id = await self.send_personal_free_play(row, reason)
        except Exception as exc:
            logger.exception("slip_review_free_play_fallback_failed message=%s", message_id)
            delivery = (row.get("review") or {}).get("personal_free_play")
            state = delivery.get("state") if delivery else "unclaimed"
            await self.publisher.staff_alert(
                f"slip:{message_id}",
                f"Slip review {message_id} failed: {reason[:800]}. Standard Free Play delivery is {state}; "
                f"inspect Free Plays and play #{delivery.get('play_id', 'unknown') if delivery else 'unknown'} "
                f"before retrying. Error: {str(exc)[:500]}",
            )
            raise
        await self.publisher.staff_alert(
            f"slip:{message_id}",
            f"Slip review {message_id} failed: {reason[:800]}. Posted as regular capper Free Play "
            f"#{play_id}, not an official pick: {message.jump_url}",
        )

    async def valid_source(self, channel, row: dict) -> bool:
        try:
            message = await channel.fetch_message(int(row["message_id"]))
        except discord.NotFound:
            return False
        saved = row["review"]
        return (
            str(message.author.id) == saved["author_id"]
            and message.content == saved["source_text"]
            and (message.edited_at.isoformat() if message.edited_at else None) == saved["source_edited_at"]
            and [str(item.id) for item in message.attachments] == saved["attachment_ids"]
        )

    async def discover(self, channel) -> None:
        if isinstance(channel, discord.ForumChannel):
            scanned_at = datetime.now(timezone.utc)
            scan = await asyncio.to_thread(self.store.scan_state)
            threads = await self.forum_threads(channel, since=review_time(scan.get("forum_scanned_at") or scan["started_at"]))
            listed = {thread.id for thread in threads}
            failed = False
            for thread_id in await asyncio.to_thread(self.store.invalid_ids, channel.id):
                if thread_id in listed:
                    continue
                try:
                    thread = await channel.guild.fetch_channel(thread_id)
                except discord.NotFound:
                    logger.info("invalid_slip_thread_deleted thread=%s", thread_id)
                    await asyncio.to_thread(self.store.retire_invalid, thread_id)
                    continue
                except discord.HTTPException:
                    failed = True
                    logger.exception("invalid_slip_thread_inaccessible thread=%s", thread_id)
                    await self.publisher.staff_alert(
                        f"slip_discovery:{thread_id}", f"Cannot access invalid slip post {thread_id}; check its permissions and bot logs.",
                    )
                    continue
                if isinstance(thread, discord.Thread) and thread.parent_id == channel.id:
                    threads.append(thread)
            known = await asyncio.to_thread(self.store.known_ids, [thread.id for thread in threads])
            for thread in threads:
                if thread.id in self.intake_ids or thread.id in known:
                    continue
                try:
                    await self.discover_thread(channel, thread, since=review_time(scan["started_at"]))
                except discord.NotFound:
                    logger.info("slip_discovery_thread_deleted thread=%s", thread.id)
                except Exception as exc:
                    failed = True
                    logger.exception("slip_discovery_post_failed thread=%s", thread.id)
                    await self.publisher.staff_alert(
                        f"slip_discovery:{thread.id}", f"Slip post {thread.id} could not be reconciled: {str(exc)[:1500]}",
                    )
            if not failed:
                await asyncio.to_thread(self.store.advance_forum, scanned_at)
            return
        scan = await asyncio.to_thread(self.store.scan_state)
        after = discord.Object(id=int(scan["last_message_id"])) if scan["last_message_id"] else review_time(scan["started_at"])
        async for message in channel.history(limit=None, after=after, oldest_first=True):
            if not message.author.bot and self.publisher.is_tracked_operator(message.author):
                images = [item for item in message.attachments if (item.content_type or "").startswith("image/")]
                if message.poll is not None:
                    await channel.send(
                        f"<@{message.author.id}>, native polls are no longer used. Upload a slip image, "
                        "then confirm its details and set a voting cutoff.",
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                elif images:
                    await self.intake(message, images)
            await asyncio.to_thread(self.store.advance, message.id)

    async def discover_thread(self, forum, thread, *, since: datetime | None = None) -> None:
        if thread.id == ACKNOWLEDGEMENT_THREAD_ID:
            return
        async with self.discovery_locks.setdefault(thread.id, asyncio.Lock()):
            if await asyncio.to_thread(self.store.find, thread.id, columns="message_id"):
                return
            await self.discover_locked_thread(forum, thread, since=since)

    async def has_submission_acknowledgement(self, user_id: int) -> bool:
        thread = await self.publisher.resolve_channel(
            ACKNOWLEDGEMENT_THREAD_ID, "capper submission acknowledgement", required=True,
        )
        if (not isinstance(thread, discord.Thread) or thread.guild.id != GUILD_ID
                or thread.parent_id != CAPPER_BALLOT_CHANNEL_ID):
            raise RuntimeError("The acknowledgement thread is outside the configured ballot forum.")
        message = await thread.fetch_message(ACKNOWLEDGEMENT_THREAD_ID)
        if message.author.id != self.bot.user.id:
            raise RuntimeError("The acknowledgement notice must be bot-owned.")
        for reaction in message.reactions:
            if str(reaction.emoji) == ACKNOWLEDGEMENT_EMOJI:
                async for user in reaction.users(limit=None):
                    if user.id == user_id and not user.bot:
                        return True
        return False

    async def discover_locked_thread(self, forum, thread, *, since: datetime | None = None) -> None:
        if since is None:
            scan = await asyncio.to_thread(self.store.scan_state)
            since = review_time(scan["started_at"])
        message = await thread.fetch_message(thread.id)
        if message.created_at < since or message.author.bot:
            return
        member = forum.guild.get_member(message.author.id) or await forum.guild.fetch_member(message.author.id)
        if not self.publisher.is_tracked_operator(member):
            return
        if not await self.has_submission_acknowledgement(member.id):
            await thread.delete(reason="Capper has not acknowledged the submission rules with the required reaction")
            logger.info("slip_unacknowledged_post_deleted thread=%s author=%s", thread.id, member.id)
            link = f"https://discord.com/channels/{GUILD_ID}/{ACKNOWLEDGEMENT_THREAD_ID}/{ACKNOWLEDGEMENT_THREAD_ID}"
            error = (
                f"Your capper ballot post was deleted because you have not acknowledged the submission rules. "
                f"Read the opening message and react with {ACKNOWLEDGEMENT_EMOJI} here: {link}\n"
                "Then create a fresh ballot post. Reactions on other messages or using another emoji do not count."
            )
            try:
                await member.send(error, allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                logger.exception("slip_acknowledgement_dm_failed thread=%s author=%s", thread.id, member.id)
                await self.publisher.staff_alert(
                    f"slip_acknowledgement:{member.id}",
                    f"Deleted ballot post {thread.id}: capper {member.id} has not acknowledged the rules. "
                    f"The error DM could not be delivered. Ask them to react with {ACKNOWLEDGEMENT_EMOJI} to {link} "
                    "and submit a fresh ballot post.",
                )
            return
        images = [item for item in message.attachments if (item.content_type or "").startswith("image/")]
        if not images:
            await self.reject_intake(message, "Attach one readable slip image to the opening post.")
            return
        if thread.locked:
            raise RuntimeError(f"New slip thread {thread.id} is locked; staff must unlock it.")
        if thread.archived:
            await thread.edit(archived=False, reason="Prepare capper slip confirmation")
        await self.intake(message, images)

    async def reject_intake(self, message, reason: str) -> None:
        fingerprint = hashlib.sha256(json.dumps({
            "text": message.content,
            "edited": message.edited_at.isoformat() if message.edited_at else None,
            "attachments": [str(item.id) for item in message.attachments],
        }, sort_keys=True).encode("utf-8")).hexdigest()
        forum_id = str(message.channel.parent_id) if isinstance(message.channel, discord.Thread) else None
        if await asyncio.to_thread(self.store.claim_invalid, message.id, fingerprint, reason, forum_id):
            await message.channel.send(
                f"<@{message.author.id}>, {reason} Edit this opening post to correct it, or submit a fresh post.",
                allowed_mentions=discord.AllowedMentions.none(),
            )

    async def intake(self, message, images) -> None:
        if message.id in self.intake_ids:
            return
        self.intake_ids.add(message.id)
        try:
            if await asyncio.to_thread(self.store.find, message.id, columns="message_id"):
                return
            if len(images) != 1:
                await self.reject_intake(message, "Submit one slip image per post so responses refer to one exact play.")
                return
            image = images[0]
            try:
                if not accepted_photo(image):
                    raise ValueError("Upload one static JPEG, PNG, or WebP of at most 10 MB.")
                sanitized = await asyncio.to_thread(sanitize_photo, await image.read())
                if len(sanitized) > message.guild.filesize_limit:
                    raise ValueError("The processed slip exceeds this server's upload limit. Upload a smaller image.")
            except ValueError as exc:
                await self.reject_intake(message, str(exc))
                return
            encoded = base64.b64encode(sanitized).decode("ascii")
            review = {
                "author_id": str(message.author.id), "source_text": message.content,
                "author_name": str(message.author),
                "source_channel_id": str(message.channel.id),
                "source_parent_id": str(message.channel.parent_id) if isinstance(message.channel, discord.Thread) else None,
                "post_title": message.channel.name if isinstance(message.channel, discord.Thread) else "",
                "source_created_at": message.created_at.isoformat(),
                "source_edited_at": message.edited_at.isoformat() if message.edited_at else None,
                "attachment_ids": [str(item.id) for item in message.attachments],
                "slip_base64": encoded,
                "image_filename": f"slip-{message.id}.png",
            }
            if not await asyncio.to_thread(self.store.register, message.id, review):
                return
            parsed = await asyncio.to_thread(
                self.publisher.images.extract_play, f"data:image/png;base64,{encoded}", message.content,
                require_readable=True, allow_missing_odds=True,
            )
            review["parsed"] = parsed
            await asyncio.to_thread(self.store.update_review, message.id, review=review)
            row = {"message_id": str(message.id), "review": review}
            file = discord.File(
                BytesIO(base64.b64decode(review["slip_base64"], validate=True)), filename=review["image_filename"],
            )
            try:
                async with self.lock:
                    prompt = await message.channel.send(
                        f"<@{message.author.id}>, confirm [your slip]({message.jump_url}) and set its cutoff.\n"
                        "OpenAI reads this slip for inference, not model training by this bot. "
                        "Crop names, account IDs, balances and barcodes before submitting. Private review evidence "
                        "expires 365 days after review and bet resolution; bet/result summaries remain. "
                        "Contact the server owner for deletion requests.",
                        embed=review_embed(row), view=review_view(message.id, draft=True), file=file,
                        allowed_mentions=discord.AllowedMentions(users=[message.author], roles=False, everyone=False),
                    )
                    await asyncio.to_thread(self.store.update_review, message.id, review_message_id=str(prompt.id))
            finally:
                file.close()
        except Exception as exc:
            await self.fail(message.id, str(exc))
            await message.channel.send(
                f"<@{message.author.id}>, slip review could not be prepared: {str(exc)[:500]}. "
                "Correct the slip and submit a fresh image.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        finally:
            self.intake_ids.discard(message.id)

    async def confirm(self, interaction, message_id: int, legs_text: str, start_text: str, cutoff_text: str) -> None:
        async with self.review_lock(message_id):
            if self.publisher.get_testing() or interaction.guild_id != GUILD_ID:
                raise ValueError("Slip reviews are unavailable here or in testing mode.")
            row = await asyncio.to_thread(self.store.get, message_id)
            self.check_review_channel(interaction.channel_id, row)
            if str(interaction.user.id) != row["review"]["author_id"] or not self.publisher.is_tracked_operator(interaction.user):
                raise ValueError("Only the capper who submitted the slip can confirm it.")
            if row["state"] != "pending" or row["cutoff_at"] is not None:
                raise ValueError("This slip has already been confirmed or closed.")
            start = parse_event_start(start_text)
            cutoff = parse_cutoff(cutoff_text, start)
            if not datetime.now(timezone.utc) < cutoff < start:
                raise ValueError(
                    "Voting has to close before the first game starts, and after right now. "
                    "Check the start time, or set voting to close sooner (for example '5 min before')."
                )
            channel = await self.channel(row)
            if not await self.valid_source(channel, row):
                raise ValueError("The original slip was edited or deleted. Submit a fresh slip.")
            parsed = {**row["review"]["parsed"]}
            parsed["legs"] = confirmed_legs(legs_text, parsed["legs"])
            if len(parsed["legs"]) > 2:
                raise ValueError(
                    "Slips with 3 or more legs can never become official picks. Resubmit with at most 2 legs, "
                    "or cancel this review and post it as a personal Free Play (1u maximum) tomorrow."
                )
            roster = await self.publisher.tracked_operators()
            if roster is None or len(roster) < 2 or interaction.user.id not in roster:
                raise ValueError("Cannot verify an eligible roster containing you and another capper.")
            await asyncio.to_thread(self.store.open, message_id, interaction.user.id, roster, parsed, start, cutoff)
            try:
                row = await asyncio.to_thread(self.store.get, message_id)
                prompt = await channel.fetch_message(int(row["review_message_id"]))
                await self.edit_prompt(channel, prompt, embed=review_embed(row), view=review_view(message_id))
                await prompt.clear_reactions()
                await prompt.add_reaction(TAIL)
                await channel.send(
                    f"<@&{TRACKER_ROLE_ID}> [Slip review is open]({prompt.jump_url}). "
                    f"Author-set cutoff: <t:{int(cutoff.timestamp())}:F>. React {TAIL} to tail this slip.",
                    allowed_mentions=discord.AllowedMentions(
                        everyone=False, users=False, roles=[discord.Object(id=TRACKER_ROLE_ID)],
                    ),
                )
            except Exception as exc:
                await self.fail(message_id, str(exc))
                raise
            await interaction.followup.send("Review opened. Your tail is recorded and your cutoff is locked.", ephemeral=True)

    async def evaluate(self, channel, row: dict) -> None:
        message_id = int(row["message_id"])
        if message_id in self.intake_ids or self.needs_reconciliation:
            return
        if not await self.valid_source(channel, row):
            await self.fail(message_id, "Original slip was edited or deleted. Submit a fresh slip for new responses.")
            return
        if not row.get("review_message_id") or not row["review"].get("parsed"):
            await self.fail(message_id, "Slip intake was interrupted before its confirmation controls were saved.")
            return
        prompt = await channel.fetch_message(int(row["review_message_id"]))
        if not row.get("cutoff_at"):
            return
        decision = await asyncio.to_thread(self.store.finalize, message_id)
        if decision is None:
            await self.edit_prompt(channel, prompt, final=False, embed=review_embed(row), view=review_view(message_id))
            remaining = review_time(row["cutoff_at"]) - datetime.now(timezone.utc)
            if timedelta(0) < remaining <= timedelta(minutes=2) and not row["reminder_sent"]:
                missing = set(row["eligible_voter_ids"]) - response_choices(row).keys()
                if missing and await asyncio.to_thread(self.store.claim_reminder, message_id):
                    await channel.send(
                        " ".join(f"<@{voter}>" for voter in sorted(missing))
                        + f" Review closes <t:{int(review_time(row['cutoff_at']).timestamp())}:R>: {prompt.jump_url}",
                        allowed_mentions=discord.AllowedMentions(
                            everyone=False, roles=False, users=[discord.Object(id=int(voter)) for voter in missing],
                        ),
                    )
            return
        embed = review_embed(row)
        embed.clear_fields()
        if decision["outcome"] == "expired":
            embed.title = "Review closed - personal Free Play only"
            await self.edit_prompt(channel, prompt, embed=embed, view=None)
            expired_row = await asyncio.to_thread(self.store.get, message_id)
            if (
                expired_row["state"] != "failed"
                or expired_row.get("error")
                != "Event already started; no late official publication is permitted."
            ):
                raise RuntimeError("The expired capper review could not be verified before Free Play publication.")
            try:
                await self.post_or_request_personal_free_play(expired_row, "review expired after event start")
            except Exception as exc:
                logger.exception("expired_free_play_publish_failed message=%s", message_id)
                await self.publisher.staff_alert(
                    f"expired_free_play:{message_id}",
                    f"Expired slip {message_id} was not posted as a regular Free Play; its review thread was left intact. "
                    f"Check bot logs. Error: {str(exc)[:1000]}",
                )
        elif decision["outcome"] == "rejected":
            embed.title = "Review closed - personal only"
            counts = decision["counts"]
            embed.add_field(
                name="Final responses",
                value=f"{counts['yes']} tailed | {decision['roster_size'] - counts['yes']} did not tail",
                inline=False,
            )
            await self.edit_prompt(channel, prompt, embed=embed, view=None)
            if not isinstance(channel, discord.Thread) or channel.parent_id != CAPPER_BALLOT_CHANNEL_ID:
                raise RuntimeError("Rejected capper reviews must remain in their verified submission thread.")
            rejected_row = await asyncio.to_thread(self.store.get, message_id)
            if rejected_row["state"] != "rejected":
                raise RuntimeError("The rejected capper review could not be verified before thread deletion.")
            try:
                posted = await self.post_or_request_personal_free_play(
                    rejected_row, "review did not qualify for an official pick",
                )
            except Exception as exc:
                logger.exception("rejected_free_play_publish_failed message=%s", message_id)
                await self.publisher.staff_alert(
                    f"rejected_free_play:{message_id}",
                    f"Rejected slip {message_id} was not posted as a regular Free Play; its review thread was left intact. "
                    f"Check bot logs. Error: {str(exc)[:1000]}",
                )
                return
            if posted is None:
                return
            await self.archive_rejected_personal_free_play(rejected_row, channel)
        else:
            embed.title = "Review locked - publishing"
            await self.edit_prompt(channel, prompt, final=False, embed=embed, view=None)
            source = await channel.fetch_message(message_id)
            payload = await self.publisher.publish_approved(
                source, row["review"]["parsed"], decision,
                slip=base64.b64decode(row["review"]["slip_base64"], validate=True),
            )
            try:
                await self.request_insight(int(payload["play_id"]))
            except Exception as exc:
                logger.exception("official_insight_request_failed play=%s", payload["play_id"])
                await self.publisher.staff_alert(
                    f"official_insight:{payload['play_id']}",
                    f"Official Play #{payload['play_id']} was published, but its original capper's "
                    f"justification request failed: {str(exc)[:1000]}. "
                    "Reconcile the team-discussion prompt before using /request_insights. Do not republish the pick.",
                )
            embed.title = "Review closed - official pick published"
            embed.add_field(name="Final decision", value=approval_text(decision, decision["units"]), inline=False)
            await self.edit_prompt(channel, prompt, embed=embed)

    @staticmethod
    def capper_units_error(row: dict) -> str | None:
        parsed = ((row.get("review") or {}).get("parsed") or {})
        units = parsed.get("units")
        if units is None or str(units).strip() == "":
            return "The capper did not supply a unit stake."
        error = PlayService.validate_capper_units(units)
        if error:
            return error
        if float(units) <= 0:
            return "Units must be greater than zero."
        if len(parsed.get("legs") or []) >= 2 and float(units) > 1:
            return "Parlays can never be staked above 1 unit."
        return None

    async def post_or_request_personal_free_play(
        self, row: dict, reason: str,
    ) -> tuple[discord.Message, int] | None:
        if unit_error := self.capper_units_error(row):
            prompt = await self.request_personal_free_play_units(row, reason)
            delivery = (row.get("review") or {}).get("personal_free_play") or {}
            prompt_url = prompt.jump_url if prompt is not None else (
                f"https://discord.com/channels/{GUILD_ID}/{delivery.get('prompt_channel_id')}/"
                f"{delivery.get('prompt_message_id')}"
                if delivery.get("prompt_message_id") else "the saved team-channel prompt"
            )
            await self.publisher.staff_alert(
                f"free_play_units:{row['message_id']}",
                f"Slip {row['message_id']} needs a valid capper unit stake ({unit_error}); "
                f"the author was asked to choose it before Free Plays publication: {prompt_url}",
            )
            return None
        return await self.send_personal_free_play(row, reason)

    async def send_personal_free_play(self, row: dict, reason: str) -> tuple[discord.Message, int]:
        if unit_error := self.capper_units_error(row):
            raise ValueError(f"{unit_error} No regular Free Play can be created.")
        if FREE_PLAYS_CHANNEL_ID is None:
            raise RuntimeError("FREE_PLAYS_CHANNEL_ID is not configured.")
        claimed = await asyncio.to_thread(
            self.store.claim_personal_free_play, int(row["message_id"]), FREE_PLAYS_CHANNEL_ID, reason[:1000],
        )
        if not claimed:
            raise RuntimeError("The one-time regular Free Play delivery claim was not available.")
        return await self.publish_claimed_personal_free_play(row)

    async def publish_claimed_personal_free_play(self, row: dict) -> tuple[discord.Message, int]:
        guild = self.bot.get_guild(GUILD_ID)
        if guild is None:
            raise RuntimeError("The configured guild is unavailable to the bot.")
        message, play_id = await self.publish_personal_free_play(guild, row)
        completed = await asyncio.to_thread(
            self.store.complete_personal_free_play,
            int(row["message_id"]), FREE_PLAYS_CHANNEL_ID, message.id, play_id,
        )
        if not completed:
            raise RuntimeError("The Free Play was sent, but its delivery marker was not completed.")
        return message, play_id

    async def archive_rejected_personal_free_play(self, row: dict, channel=None) -> None:
        if row.get("state") != "rejected":
            return
        review_channel = channel or await self.channel(row)
        await self.records.save(review_channel, row)
        try:
            await review_channel.delete(reason="Archived rejected capper review; no official play was published")
        except discord.NotFound:
            logger.info("rejected_capper_review_thread_already_deleted thread=%s", review_channel.id)
        await asyncio.to_thread(self.records.store.source_deleted, str(row["message_id"]))

    async def request_personal_free_play_units(self, row: dict, reason: str) -> discord.Message | None:
        if FREE_PLAYS_CHANNEL_ID is None or CONFIRMATION_CHANNEL_ID is None:
            raise RuntimeError("Free Plays or team discussion channel is not configured.")
        delivery = (row.get("review") or {}).get("personal_free_play") or {}
        if delivery.get("state") == "awaiting_units":
            return None
        if delivery:
            raise RuntimeError(f"Free Play delivery is already marked {delivery.get('state', 'in flight')}.")
        claimed = await asyncio.to_thread(
            self.store.claim_personal_free_play_units_request,
            int(row["message_id"]), FREE_PLAYS_CHANNEL_ID, reason[:1000],
        )
        if not claimed:
            raise RuntimeError("The one-time unit request claim was not available.")
        channel = await self.publisher.resolve_channel(
            CONFIRMATION_CHANNEL_ID, "CONFIRMATION_CHANNEL_ID", required=True,
        )
        if channel is None or channel.guild.id != GUILD_ID:
            raise RuntimeError("The configured team discussion channel is unavailable.")
        author_id = int(row["review"]["author_id"])
        prompt = await channel.send(
            f"<@{author_id}> this slip did not qualify as an official pick. "
            "Choose the unit stake for its regular Free Plays card below; it will post automatically after you submit "
            "(maximum 3u).",
            view=self.free_play_units_view(int(row["message_id"])),
            allowed_mentions=discord.AllowedMentions(users=[discord.Object(id=author_id)]),
        )
        completed = await asyncio.to_thread(
            self.store.complete_personal_free_play_units_request,
            int(row["message_id"]), FREE_PLAYS_CHANNEL_ID, channel.id, prompt.id,
        )
        if not completed:
            raise RuntimeError("The unit prompt was sent, but its message marker was not saved.")
        return prompt

    async def submit_free_play_units(
        self, interaction: discord.Interaction, message_id: int, prompt_id: int, units_text: str,
    ) -> tuple[discord.Message, int]:
        try:
            units = float(units_text.strip())
        except ValueError as exc:
            raise ValueError("Enter a numeric unit stake greater than 0 and no more than 3.") from exc
        if error := PlayService.validate_capper_units(units):
            raise ValueError(error)
        if units <= 0:
            raise ValueError("Units must be greater than zero.")
        preview = await asyncio.to_thread(self.store.get, message_id)
        if len((((preview.get("review") or {}).get("parsed") or {}).get("legs")) or []) >= 2 and units > 1:
            raise ValueError("Parlays can never be staked above 1 unit.")
        if FREE_PLAYS_CHANNEL_ID is None or CONFIRMATION_CHANNEL_ID is None:
            raise RuntimeError("Free Plays or team discussion channel is not configured.")
        if interaction.guild_id != GUILD_ID or interaction.channel_id != CONFIRMATION_CHANNEL_ID:
            raise ValueError("This unit request is outside the configured team discussion channel.")
        claimed = await asyncio.to_thread(
            self.store.claim_personal_free_play_units,
            message_id, FREE_PLAYS_CHANNEL_ID, interaction.user.id, CONFIRMATION_CHANNEL_ID, prompt_id, units,
        )
        if not claimed:
            raise RuntimeError("This unit request is closed, already submitted, or does not belong to you.")
        row = await asyncio.to_thread(self.store.get, message_id)
        try:
            message, play_id = await self.publish_claimed_personal_free_play(row)
        except Exception as exc:
            raise RuntimeError(
                "The unit stake was saved, but the regular Free Play could not be completed; staff must reconcile it."
            ) from exc
        try:
            await self.archive_rejected_personal_free_play(row)
        except Exception as exc:
            logger.exception("rejected_free_play_archive_failed message=%s", message_id)
            await self.publisher.staff_alert(
                f"rejected_free_play_archive:{message_id}",
                f"Regular Free Play #{play_id} was posted, but rejected slip {message_id} could not be archived: "
                f"{str(exc)[:800]}",
            )
        try:
            prompt_channel = await self.publisher.resolve_channel(
                CONFIRMATION_CHANNEL_ID, "CONFIRMATION_CHANNEL_ID", required=True,
            )
            prompt = await prompt_channel.fetch_message(prompt_id)
            await prompt.edit(
                content=f"<@{interaction.user.id}> selected {units:g}u. "
                        f"Regular Free Play posted: {message.jump_url}",
                view=None,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception as exc:
            logger.exception("free_play_units_prompt_update_failed ballot=%s", message_id)
            await self.publisher.staff_alert(
                f"free_play_units_prompt:{message_id}",
                f"Free Play #{play_id} was posted, but its unit prompt could not be updated: {str(exc)[:800]}",
            )
        return message, play_id

    async def publish_personal_free_play(self, guild: discord.Guild, row: dict) -> tuple[discord.Message, int]:
        review = row["review"]
        parsed = review["parsed"]
        if unit_error := self.capper_units_error(row):
            raise ValueError(f"{unit_error} No regular Free Play can be created.")
        legs = parsed["legs"]
        author = guild.get_member(int(review["author_id"]))
        if author is None:
            author = await guild.fetch_member(int(review["author_id"]))
        payload = await asyncio.to_thread(
            self.publisher.official.create_play_record,
            discord_user_id=str(author.id),
            username=author.display_name,
            units=parsed["units"],
            legs=len(legs),
            odds=PlayService.combine_american_odds([int(leg["odds"]) for leg in legs]),
            play_text="\n".join(
                f"Leg {index}: {leg['selection']} ({int(leg['odds']):+d})"
                for index, leg in enumerate(legs, start=1)
            ),
            team_name=parsed.get("team_name"),
            leg_records=legs,
            tracking_scope="capper",
            ballot_message_id=str(row["message_id"]),
        )
        if payload.get("error"):
            raise ValueError(payload["error"])
        if not payload.get("play_id"):
            raise RuntimeError("The capper Free Play record was not created.")
        message = await self.publish_free_play(
            guild, author, payload, base64.b64decode(review["slip_base64"], validate=True),
        )
        await asyncio.to_thread(self.publisher.official.attach_message_id, payload["play_id"], message.id)
        return message, int(payload["play_id"])

    async def reaction(self, payload, *, added: bool) -> None:
        if self.publisher.get_testing():
            self.needs_reconciliation = True
            return
        if (
            payload.guild_id != GUILD_ID
            or str(payload.emoji) != TAIL
            or self.needs_reconciliation
            or (self.bot.user and payload.user_id == self.bot.user.id)
        ):
            return
        row = None
        try:
            rows = await asyncio.to_thread(self.store.pending)
            row = next((item for item in rows if item["review_message_id"] == str(payload.message_id)
                        and self.source_channel_id(item) == payload.channel_id), None)
            if row is None:
                return
            async with self.review_lock(int(row["message_id"])):
                status = await asyncio.to_thread(
                    self.store.respond, int(row["message_id"]), payload.user_id,
                    f"yes_{'add' if added else 'remove'}",
                )
                logger.info("slip_response message=%s voter=%s result=%s", row["message_id"], payload.user_id, status)
                if status == "saved":
                    await self.evaluate(await self.channel(row), await asyncio.to_thread(self.store.get, int(row["message_id"])))
        except Exception as exc:
            logger.exception("slip_reaction_failed")
            self.needs_reconciliation = True
            if row is not None:
                await self.fail(int(row["message_id"]), str(exc))
            else:
                await self.publisher.staff_alert("slip_reaction", f"Slip response could not be processed: {str(exc)[:1500]}")

    async def reconcile_reactions(self, channel, row: dict) -> None:
        if not row.get("cutoff_at"):
            return
        if review_time(row["cutoff_at"]) <= datetime.now(timezone.utc):
            await self.fail(int(row["message_id"]), "Reaction availability was interrupted across the voting cutoff.")
            return
        prompt = await channel.fetch_message(int(row["review_message_id"]))
        tailed: set[str] = set()
        for reaction in prompt.reactions:
            if str(reaction.emoji) == TAIL:
                async for user in reaction.users(limit=None):
                    if not user.bot:
                        tailed.add(str(user.id))
        # Do not finalize until the complete roster is synchronized.
        for voter in row["eligible_voter_ids"]:
            present = voter in tailed
            saved = row["response_states"].get(voter, {}).get("yes", False)
            if present != saved:
                status = await asyncio.to_thread(
                    self.store.respond, int(row["message_id"]), int(voter),
                    f"yes_{'add' if present else 'remove'}",
                )
                if status == "closed":
                    await self.fail(int(row["message_id"]), "Voting cutoff passed during reaction recovery.")
                    return

    async def cleared_reactions(self, payload) -> None:
        if self.publisher.get_testing():
            self.needs_reconciliation = True
            return
        if (
            payload.guild_id != GUILD_ID
            or (hasattr(payload, "emoji") and str(payload.emoji) != TAIL)
        ):
            return
        row = None
        try:
            for row in await asyncio.to_thread(self.store.pending):
                if (row["review_message_id"] == str(payload.message_id) and row.get("cutoff_at")
                        and self.source_channel_id(row) == payload.channel_id):
                    async with self.review_lock(int(row["message_id"])):
                        await self.reconcile_reactions(await self.channel(row), row)
        except Exception as exc:
            logger.exception("slip_reaction_clear_failed")
            self.needs_reconciliation = True
            if row is not None:
                await self.fail(int(row["message_id"]), str(exc))
            else:
                await self.publisher.staff_alert("slip_reaction_clear", f"Review reaction reset failed: {str(exc)[:1500]}")

    @commands.Cog.listener()
    async def on_raw_reaction_clear(self, payload: discord.RawReactionClearEvent) -> None:
        await self.cleared_reactions(payload)

    @commands.Cog.listener()
    async def on_raw_reaction_clear_emoji(self, payload: discord.RawReactionClearEmojiEvent) -> None:
        await self.cleared_reactions(payload)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        await self.reaction(payload, added=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        await self.reaction(payload, added=False)

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        custom_id = str((interaction.data or {}).get("custom_id") or "")
        units_match = re.fullmatch(r"pm:freeplay_units:(\d{1,20})", custom_id)
        if units_match is not None:
            await self.free_play_units_button(interaction, int(units_match.group(1)))
            return
        if not custom_id.startswith("pm:sr:"):
            return
        try:
            match = re.fullmatch(r"pm:sr:(\d{1,20}):(confirm|cancel)", custom_id)
            if (
                match is None or interaction.guild_id != GUILD_ID
                or interaction.user.bot or self.publisher.get_testing()
            ):
                raise ValueError("This review control is not available here.")
            await interaction.response.defer(ephemeral=True)
            message_id, action = int(match[1]), match[2]
            row = await asyncio.to_thread(self.store.get, message_id)
            self.check_review_channel(interaction.channel_id, row)
            if interaction.message is None or str(interaction.message.id) != row["review_message_id"]:
                raise ValueError("This control does not belong to the saved review.")
            if row["state"] != "pending":
                raise ValueError("This review is already closed.")
            if action == "confirm":
                if str(interaction.user.id) != row["review"]["author_id"] or row["cutoff_at"] is not None:
                    raise ValueError("Only the author can confirm an unopened review.")
                await interaction.followup.send(
                    "Your confirmation form is ready. This button expires in 3 minutes.",
                    view=PreparedModalView(interaction.user.id, lambda: SlipConfirmation(self, row)),
                    ephemeral=True,
                )
                return
            async with self.review_lock(message_id):
                row = await asyncio.to_thread(self.store.get, message_id)
                if row["state"] != "pending":
                    raise ValueError("This review is already closed.")
                if str(interaction.user.id) != row["review"]["author_id"]:
                    raise ValueError("Only the author can cancel their review.")
                if not await asyncio.to_thread(self.store.cancel, message_id, interaction.user.id):
                    raise ValueError("The review is already locked or closed; it cannot be cancelled.")
                await interaction.message.edit(content="Review cancelled by its author.", view=None)
                result = (
                    "Review cancelled. No official pick will be published, and its plays "
                    "cannot be posted as your Free Plays until tomorrow."
                )
            await interaction.followup.send(result, ephemeral=True)
        except Exception as exc:
            logger.exception("slip_control_failed")
            send = interaction.followup.send if interaction.response.is_done() else interaction.response.send_message
            await send(f"Review action not accepted: {str(exc)[:1000]}", ephemeral=True)

    @tasks.loop(minutes=5)
    async def scan_slips(self) -> None:
        if self.publisher.get_testing() or not self.bot.is_ready():
            return
        try:
            async with self.scan_lock:
                try:
                    await self.ensure_submission_notice()
                except Exception:
                    self.submission_notice_ready = False
                    raise
                self.submission_notice_ready = True
                await self.discover(await self.channel())
        except Exception as exc:
            logger.exception("slip_intake_scan_failed")
            await self.publisher.staff_alert("slip_intake_scan", f"Slip intake failed: {str(exc)[:1500]}")

    @scan_slips.before_loop
    async def before_scan(self) -> None:
        await self.bot.wait_until_ready()

    async def process_row(self, channel, row: dict, *, reconcile: bool = False) -> None:
        message_id = int(row["message_id"])
        async with self.review_lock(message_id):
            try:
                row = await asyncio.to_thread(self.store.get, message_id)
                if row["state"] != "pending" or message_id in self.intake_ids:
                    return
                channel = await self.channel(row)
                if reconcile:
                    await self.reconcile_reactions(channel, row)
                else:
                    await self.evaluate(channel, row)
            except Exception as exc:
                await self.fail(message_id, str(exc))

    async def bounded_process_row(self, channel, row: dict) -> None:
        async with self.processing_slots:
            await self.process_row(channel, row)

    @tasks.loop(seconds=15)
    async def process_reviews(self) -> None:
        if self.publisher.get_testing():
            self.needs_reconciliation = True
            return
        if not self.bot.is_ready():
            return
        try:
            channel = await self.channel()
            if self.needs_reconciliation:
                for row in await asyncio.to_thread(self.store.pending):
                    await self.process_row(channel, row, reconcile=True)
                self.needs_reconciliation = False
            results = await asyncio.gather(*(
                self.bounded_process_row(channel, row) for row in await asyncio.to_thread(self.store.pending)
            ), return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException):
                    raise result
        except Exception as exc:
            logger.exception("slip_review_scan_failed")
            self.needs_reconciliation = True
            await self.publisher.staff_alert("slip_review_scan", f"Slip processing failed: {str(exc)[:1500]}")

    @process_reviews.before_loop
    async def recover(self) -> None:
        await self.bot.wait_until_ready()
        try:
            for row in await asyncio.to_thread(self.store.interrupted):
                await self.publisher.staff_alert(
                    f"slip:{row['message_id']}",
                    f"Review {row['message_id']} was interrupted during publication. Reconcile before retrying.",
                )
            await asyncio.to_thread(self.store.pending)
        except Exception as exc:
            logger.exception("slip_review_recovery_failed")
            await self.publisher.staff_alert(
                "slip_review_recovery", f"Slip review recovery failed. Check migrations and database availability: {str(exc)[:1500]}",
            )
            raise

    @tasks.loop(time=BALLOT_CLEANUP_TIME)
    async def daily_cleanup(self) -> None:
        if self.publisher.get_testing():
            return
        try:
            async with self.lock:
                channel = await self.channel()
                rows = await asyncio.to_thread(self.store.pending)
                rows += await asyncio.to_thread(self.store.interrupted)
                if isinstance(channel, discord.ForumChannel):
                    protected_threads = {self.source_channel_id(row) for row in rows}
                    protected_threads.update(self.intake_ids)
                    for thread in await self.forum_threads(channel):
                        if thread.id in protected_threads:
                            continue
                        row = await asyncio.to_thread(self.store.find, thread.id)
                        if row is None:
                            # Leave unprocessed/invalid posts for staff, never delete an unrecorded submission.
                            continue
                        async with self.review_lock(int(row["message_id"])):
                            try:
                                row = await asyncio.to_thread(self.store.get, int(row["message_id"]))
                                if row["state"] in {"pending", "processing"} or thread.id in self.intake_ids:
                                    continue
                                await self.records.save(thread, row)
                                await thread.delete(reason="Recorded completed capper review; daily 3 AM Eastern cleanup")
                                await asyncio.to_thread(self.records.store.source_deleted, str(row["message_id"]))
                            except Exception as exc:
                                logger.exception("capper_forum_cleanup_failed thread=%s", thread.id)
                                await self.publisher.staff_alert(
                                    f"capper_forum_cleanup:{thread.id}",
                                    f"Could not safely clear capper post {thread.id}: {str(exc)[:1500]}. "
                                    "Its daily record must be verified before deletion.",
                                )
                    return
                protected = {
                    int(value) for row in rows for value in (row["message_id"], row.get("review_message_id")) if value
                }
                protected.update(self.intake_ids)
                await channel.purge(
                    limit=None, check=lambda message: message.id not in protected,
                    reason="Daily slip review cleanup; active reviews and saved history are preserved",
                )
        except Exception as exc:
            logger.exception("slip_review_cleanup_failed")
            await self.publisher.staff_alert("slip_review_cleanup", f"Review cleanup failed: {str(exc)[:1500]}")

    @daily_cleanup.before_loop
    async def before_cleanup(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=5)
    async def refresh_records(self) -> None:
        if self.publisher.get_testing() or not self.bot.is_ready():
            return
        try:
            await self.records.refresh()
        except Exception as exc:
            logger.exception("capper_records_refresh_failed")
            await self.publisher.staff_alert(
                "capper_records_refresh", f"Daily betting records could not be updated: {str(exc)[:1500]}",
            )

    @refresh_records.before_loop
    async def before_refresh_records(self) -> None:
        await self.bot.wait_until_ready()
