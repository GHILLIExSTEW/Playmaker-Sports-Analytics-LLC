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
from src.config import CAPPER_BALLOT_CHANNEL_ID, GUILD_ID, TRACKER_ROLE_ID
from src.interaction_views import PreparedModalView
from src.forum_health import CheckedForum
from src.services.ballot_vote_service import approval_text, slip_rating
from src.services.image_play_service import ImagePlayService
from src.services.official_play_service import OfficialPlayService
from src.services.photo_service import accepted_photo, sanitize_photo
from src.services.supabase_service import supabase_service
from src.capper_review_records import ReviewRecords
from src.services.slip_review_service import (
    SlipReviewStore, confirmed_legs, friendly_time, parse_cutoff, parse_event_start, response_choices, review_time,
)

logger = logging.getLogger("official_play_bot")
SUPPORT = "\N{THUMBS UP SIGN}"
OPPOSE = "\N{THUMBS DOWN SIGN}"
SUBMISSION_NOTICE = (
    "Before submitting a capper slip\n\n"
    "Use one static JPEG, PNG or WebP (10 MB maximum) per post, with the matchup in its title. "
    "Crop/redact names, account IDs, balances and barcodes BEFORE uploading; metadata removal cannot hide visible details.\n\n"
    "By submitting, you request OpenAI image/text extraction and a private review. This bot uses inference, "
    "not training on Discord content. Check and correct the extracted details before confirming. "
    "Confirmation, voting and discussion stay in your post.\n\n"
    "At 3 AM Eastern, completed reviews are copied to the private owner/bot records forum before deletion here. "
    "Screenshots and detailed submission archives are removed 365 days after review and bet resolution; "
    "unresolved reviews/bets remain protected. Capper, bet, approval and result summaries remain for statistics. "
    "Discord and processors handle data under their own policies; this does not erase their backups or other people's copies.\n\n"
    "For access, corrections or deletion requests, contact the server owner or support@playmakersportsanalytics.com."
)


def review_view(message_id: int, *, draft: bool = False) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for action, label, style in (
        [("confirm", "Confirm details + set cutoff", discord.ButtonStyle.primary)]
        if draft else [("pass", "Pass / not reviewed", discord.ButtonStyle.secondary)]
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
        counts = {choice: sum(value == choice for value in choices.values()) for choice in ("yes", "no", "pass")}
        size = len(row["eligible_voter_ids"])
        rating = slip_rating(counts["yes"], counts["no"], size)
        embed.add_field(
            name="Live responses",
            value=f"{counts['yes']} supported | {counts['no']} opposed | {counts['pass']} passed"
                  f" | {size - len(choices)} did not respond",
            inline=False,
        )
        embed.add_field(
            name="Provisional result",
            value=f"Currently qualifies - projected {rating[1]}u" if rating else "Does not currently qualify - personal only",
            inline=False,
        )
        embed.add_field(
            name="How to respond",
            value=f"{SUPPORT} Support | {OPPOSE} Oppose | Pass button\n"
                  "Use only one reaction. Both reactions count as unresolved; remove one to choose."
                  " The author's confirmation is their Yes. Missing responses are not No votes."
                  " Locks at the cutoff or when everyone responds. Published units never change.",
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


class CapperSlipReviews(commands.Cog):
    def __init__(
        self, bot: commands.Bot, *, official: OfficialPlayService,
        resolve_channel: Callable[..., Awaitable],
        is_tracked_operator: Callable[[discord.abc.User], bool],
        staff_alert: Callable[[str, str], Awaitable[None]],
        get_testing: Callable[[], bool],
        tracked_operators: Callable[[], Awaitable[set[int] | None]],
        request_insight: Callable[[int], Awaitable[bool]],
        images: ImagePlayService | None = None, store: SlipReviewStore | None = None,
    ) -> None:
        self.bot = bot
        self.request_insight = request_insight
        self.store = store if store is not None else SlipReviewStore()
        self.publisher = CapperBallots(
            bot, store=self.store, official=official, resolve_channel=resolve_channel,
            is_tracked_operator=is_tracked_operator, staff_alert=staff_alert,
            get_testing=get_testing, tracked_operators=tracked_operators, images=images,
        )
        self.lock = asyncio.Lock()
        self.review_locks: dict[int, asyncio.Lock] = {}
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
        if (not message.author.bot and isinstance(message.channel, discord.Thread)
                and message.guild is not None and message.guild.id == GUILD_ID
                and message.channel.parent_id == CAPPER_BALLOT_CHANNEL_ID
                and message.id == message.channel.id):
            await self.queue_intake(message.id)

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
            channel_id, "CAPPER_BALLOT_CHANNEL_ID", required=True,
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
        finally:
            await self.publisher.staff_alert(
                f"slip:{message_id}",
                f"Slip review {message_id} needs reconciliation: {reason[:1500]}. "
                "Check the saved play and official channel before retrying; no automatic duplicate will be sent.",
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
        if since is None:
            scan = await asyncio.to_thread(self.store.scan_state)
            since = review_time(scan["started_at"])
        message = await thread.fetch_message(thread.id)
        if message.created_at < since or message.author.bot:
            return
        member = forum.guild.get_member(message.author.id) or await forum.guild.fetch_member(message.author.id)
        if not self.publisher.is_tracked_operator(member):
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
            roster = await self.publisher.tracked_operators()
            if roster is None or len(roster) < 2 or interaction.user.id not in roster:
                raise ValueError("Cannot verify an eligible roster containing you and another capper.")
            await asyncio.to_thread(self.store.open, message_id, interaction.user.id, roster, parsed, start, cutoff)
            try:
                row = await asyncio.to_thread(self.store.get, message_id)
                prompt = await channel.fetch_message(int(row["review_message_id"]))
                await self.edit_prompt(channel, prompt, embed=review_embed(row), view=review_view(message_id))
                await prompt.clear_reactions()
                for emoji in (SUPPORT, OPPOSE):
                    await prompt.add_reaction(emoji)
                await channel.send(
                    f"<@&{TRACKER_ROLE_ID}> [Slip review is open]({prompt.jump_url}). "
                    f"Author-set cutoff: <t:{int(cutoff.timestamp())}:F>. React {SUPPORT} / {OPPOSE} or choose Pass.",
                    allowed_mentions=discord.AllowedMentions(
                        everyone=False, users=False, roles=[discord.Object(id=TRACKER_ROLE_ID)],
                    ),
                )
            except Exception as exc:
                await self.fail(message_id, str(exc))
                raise
            await interaction.followup.send("Review opened. Your Yes is recorded and your cutoff is locked.", ephemeral=True)

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
            await self.fail(message_id, "The earliest event has started. Late official posting is blocked.")
            embed.title = "Review closed - event already started"
            await self.edit_prompt(channel, prompt, embed=embed, view=None)
        elif decision["outcome"] == "rejected":
            embed.title = "Review closed - personal only"
            counts = decision["counts"]
            embed.add_field(
                name="Final responses",
                value=f"{counts['yes']} supported | {counts['no']} opposed | {counts['pass']} passed"
                      f" | {decision['roster_size'] - sum(counts.values())} did not respond",
                inline=False,
            )
            await self.edit_prompt(channel, prompt, embed=embed, view=None)
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

    async def reaction(self, payload, *, added: bool) -> None:
        if self.publisher.get_testing():
            self.needs_reconciliation = True
            return
        if (
            payload.guild_id != GUILD_ID
            or str(payload.emoji) not in {SUPPORT, OPPOSE}
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
                choice = "yes" if str(payload.emoji) == SUPPORT else "no"
                status = await asyncio.to_thread(
                    self.store.respond, int(row["message_id"]), payload.user_id,
                    f"{choice}_{'add' if added else 'remove'}",
                )
                logger.info("slip_response message=%s voter=%s result=%s", row["message_id"], payload.user_id, status)
                if status == "conflict":
                    channel = await self.channel(row)
                    await channel.send(
                        f"<@{payload.user_id}>, both reactions are selected. Remove one to record your response.",
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                if status in {"saved", "conflict"}:
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
        actual = {"yes": set(), "no": set()}
        for reaction in prompt.reactions:
            choice = {SUPPORT: "yes", OPPOSE: "no"}.get(str(reaction.emoji))
            if choice:
                async for user in reaction.users(limit=None):
                    if not user.bot:
                        actual[choice].add(str(user.id))
        # Do not finalize until the complete roster is synchronized.
        for voter in row["eligible_voter_ids"]:
            for choice in ("yes", "no"):
                present = voter in actual[choice]
                saved = row["response_states"].get(voter, {}).get(choice, False)
                if present != saved:
                    status = await asyncio.to_thread(
                        self.store.respond, int(row["message_id"]), int(voter),
                        f"{choice}_{'add' if present else 'remove'}",
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
            or (hasattr(payload, "emoji") and str(payload.emoji) not in {SUPPORT, OPPOSE})
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
        if not custom_id.startswith("pm:sr:"):
            return
        try:
            match = re.fullmatch(r"pm:sr:(\d{1,20}):(confirm|pass|cancel)", custom_id)
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
                if action == "cancel":
                    if str(interaction.user.id) != row["review"]["author_id"]:
                        raise ValueError("Only the author can cancel their review.")
                    if not await asyncio.to_thread(self.store.cancel, message_id, interaction.user.id):
                        raise ValueError("The review is already locked or closed; it cannot be cancelled.")
                    await interaction.message.edit(content="Review cancelled by its author.", view=None)
                    result = "Review cancelled. No official pick will be published."
                else:
                    if self.needs_reconciliation:
                        raise ValueError("Reaction recovery is in progress. Try again shortly.")
                    status = await asyncio.to_thread(self.store.respond, message_id, interaction.user.id, "pass")
                    if status != "saved":
                        raise ValueError(f"Pass not recorded: {status}.")
                    # Remove visible reactions after saving Pass. Remove events do not undo Pass.
                    for emoji in (SUPPORT, OPPOSE):
                        await interaction.message.remove_reaction(emoji, interaction.user)
                    await self.evaluate(await self.channel(row), await asyncio.to_thread(self.store.get, message_id))
                    result = "Pass recorded. It is not an opposing vote."
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
