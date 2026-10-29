from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from io import BytesIO
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands

from src.config import CAPPER_RECORD_CHANNEL_ID, GUILD_ID, OFFICIAL_CHANNEL_ID
from src.forum_health import CheckedForum
from src.services.supabase_service import supabase_service

EASTERN = ZoneInfo("America/New_York")
logger = logging.getLogger("official_play_bot")


class ReviewRecordStore:
    def day(self, date: str) -> dict | None:
        rows = supabase_service.select("capper_review_record_days", "*", {"record_date": date}).data or []
        return rows[0] if rows else None

    def record(self, message_id: str) -> dict | None:
        rows = supabase_service.select("capper_review_records", "*", {"message_id": message_id}).data or []
        return rows[0] if rows else None

    def claim(self, table: str, values: dict, key: str) -> bool:
        result = supabase_service._execute(lambda: supabase_service._ensure_client().table(table).upsert(
            values, on_conflict=key, ignore_duplicates=True,
        ).execute())
        return bool(result.data)

    def update(self, table: str, values: dict, filters: dict) -> None:
        if not supabase_service.update(table, values, filters).data:
            raise RuntimeError("The daily review record was not saved.")

    def play(self, message_id: str) -> dict | None:
        rows = supabase_service.select("plays", "id,status,units,odds,message_id", {"ballot_message_id": message_id}).data or []
        return rows[0] if rows else None

    def snapshots(self) -> list[dict]:
        rows = []
        offset = 0
        while True:
            batch = supabase_service._execute(
                lambda: supabase_service._ensure_client().table("capper_review_record_work")
                .select("*").order("message_id").range(offset, offset + 99).execute(),
            ).data or []
            rows.extend(batch)
            if len(batch) < 100:
                return rows
            offset += 100

    def snapshot(self, message_id: str) -> dict:
        rows = supabase_service.select("capper_review_record_snapshots", "*", {"message_id": message_id}).data or []
        if len(rows) != 1:
            raise RuntimeError("The daily record snapshot is unavailable.")
        return rows[0]

    def complete(self, record: dict, content: str, *, remove_evidence: bool = False) -> bool:
        return supabase_service._execute(
            lambda: supabase_service._ensure_client().rpc("complete_capper_record_refresh", {
                "p_message_id": record["message_id"], "p_revision": record["revision"],
                "p_content": content, "p_remove_evidence": remove_evidence,
            }).execute(),
        ).data is True

    def begin_expiry(self, record: dict) -> bool:
        return supabase_service._execute(
            lambda: supabase_service._ensure_client().rpc("begin_capper_evidence_expiry", {
                "p_message_id": record["message_id"], "p_revision": record["revision"],
            }).execute(),
        ).data is True

    def deliveries(self, play_id: int) -> list[dict]:
        return supabase_service.select("play_bang_notifications", "*", {"play_id": play_id}).data or []

    def source_deleted(self, message_id: str) -> None:
        self.update("capper_review_records", {"source_deleted_at": datetime.now(timezone.utc).isoformat()},
                    {"message_id": message_id})


def record_text(row: dict, play: dict | None) -> str:
    review = row["review"]
    lines = [
        f"Capper review {row['message_id']}",
        f"Author: {review.get('author_name') or review['author_id']} (Discord {review['author_id']})",
        f"Submitted: {review.get('source_created_at') or row.get('created_at', 'Unknown')}",
        f"Post: {review.get('post_title') or 'Slip review'}",
        f"Review decision: {row['state'].upper()}",
    ]
    for index, leg in enumerate(review.get("parsed", {}).get("legs", []), 1):
        odds = f"{int(leg['odds']):+d}" if leg.get("odds") else "odds unavailable"
        lines.append(f"{index}. {leg['selection']} | {odds}")
    if not review.get("parsed", {}).get("legs"):
        lines.append("Bet details could not be extracted; see the saved original slip.")
    if review.get("source_text"):
        lines.append(f"Original note: {review['source_text']}")
    if row.get("votes"):
        lines.append(f"Saved decision / votes: {row['votes']}")
    if row.get("error"):
        lines.append(f"Review note: {row['error']}")
    if play:
        status = {"open": "Pending", "win": "Win", "loss": "Loss", "void": "Push / Void",
                  "partial": "Partial", "regraded": "Regraded"}.get(play["status"], play["status"])
        lines.append(f"Official play #{play['id']}: {status} | {play['units']}u | odds {play['odds']}")
        if not play.get("message_id"):
            lines.append("Official Discord publication is not confirmed; staff reconciliation required.")
    else:
        lines.append("Betting result: Not tracked (no linked official play; never inferred from the ballot decision).")
    return "\n".join(lines)


def record_content(row: dict, play: dict | None) -> str:
    text = discord.utils.escape_mentions(record_text(row, play))
    if len(text) > 1900:
        # Keep the current result visible even when the full bet requires the attached text file.
        result_lines = 2 if play and not play.get("message_id") else 1
        text = text[:1400] + "\n[Full bet details in attached text file]\n" + "\n".join(text.splitlines()[-result_lines:])
    return text


def summary_ballot(row: dict) -> dict:
    return {**row, "review": {key: value for key, value in row["review"].items()
                             if key not in {"source_text", "slip_base64", "image_filename"}}}


def evidence_expired(record: dict) -> bool:
    eligible = record.get("evidence_eligible_at")
    return (
        record.get("evidence_removed_at") is None and record.get("source_deleted_at") is not None
        and eligible is not None
        and datetime.fromisoformat(eligible) <= datetime.now(timezone.utc) - timedelta(days=365)
    )


class ReviewRecords:
    def __init__(
        self, bot: commands.Bot, resolve_channel: Callable[..., Awaitable], *, store: ReviewRecordStore | None = None,
    ) -> None:
        self.bot, self.resolve_channel = bot, resolve_channel
        self.store = store if store is not None else ReviewRecordStore()
        self.lock = asyncio.Lock()
        self.checked_forum = CheckedForum(resolve_channel, CAPPER_RECORD_CHANNEL_ID, "CAPPER_RECORD_CHANNEL_ID", records=True)

    async def forum(self) -> discord.ForumChannel:
        forum = await self.checked_forum.resolve()
        if not isinstance(forum, discord.ForumChannel) or forum.guild.id != GUILD_ID:
            raise RuntimeError("CAPPER_RECORD_CHANNEL_ID must be a forum in the configured guild.")
        if forum.permissions_for(forum.guild.default_role).view_channel:
            raise RuntimeError("The daily records forum must not be visible to @everyone.")
        if forum.flags.require_tag:
            raise RuntimeError("Disable required tags in the records forum so dated records can be created.")
        return forum

    async def dated_thread(self, forum: discord.ForumChannel, date: str) -> discord.Thread:
        day = await asyncio.to_thread(self.store.day, date)
        if day is not None:
            if day["forum_channel_id"] != str(forum.id):
                raise RuntimeError("The saved daily record belongs to a different forum; reconcile before cleanup.")
            if day["thread_id"] == "sending":
                raise RuntimeError(f"Daily record {date} creation was interrupted. Link its Discord thread in capper_review_record_days before retrying cleanup.")
            thread = await self.resolve_channel(int(day["thread_id"]), "daily record thread", required=True)
        else:
            claimed = await asyncio.to_thread(self.store.claim, "capper_review_record_days", {
                "record_date": date, "forum_channel_id": str(forum.id), "thread_id": "sending",
            }, "record_date")
            if not claimed:
                raise RuntimeError("Daily record creation was already claimed. Reconcile its saved thread before retrying.")
            created = await forum.create_thread(
                name=date, content=f"Capper slips submitted on {date} (Eastern), recorded before nightly cleanup.\n"
                "Each entry retains the submitting author, slip and decision. Linked official results update automatically. "
                "Untracked personal bets are not graded here. Screenshots and detailed submission attachments expire "
                "365 days after review and bet resolution; bet/result summaries remain. Contact the owner for deletion requests.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            thread = created.thread
            await asyncio.to_thread(self.store.update, "capper_review_record_days", {
                "thread_id": str(thread.id),
            }, {"record_date": date})
        if not isinstance(thread, discord.Thread) or thread.parent_id != forum.id:
            raise RuntimeError("Saved daily record thread does not belong to the records forum.")
        if thread.locked:
            raise RuntimeError("Daily record thread is locked; staff must unlock it before cleanup.")
        if thread.archived:
            thread = await thread.edit(archived=False, reason="Update private daily capper records")
        return thread

    async def save(self, source, row: dict) -> None:
        async with self.lock:
            if str(source.id) != str(row["review"]["source_channel_id"]):
                raise RuntimeError("The review does not belong to the forum post being cleared.")
            forum = await self.forum()
            message_id = str(row["message_id"])
            record = await asyncio.to_thread(self.store.record, message_id)
            if record and record.get("evidence_removed_at"):
                raise RuntimeError("This review's evidence has expired; never recreate deleted evidence.")
            submitted = datetime.fromisoformat(row["review"].get("source_created_at") or row["created_at"])
            date = record["record_date"] if record else submitted.astimezone(EASTERN).date().isoformat()
            thread = await self.dated_thread(forum, date)
            play = await asyncio.to_thread(self.store.play, message_id)
            content = record_content(row, play)
            if record:
                if record["record_message_id"] == "sending":
                    raise RuntimeError(f"Record for slip {message_id} was interrupted. Link its Discord message in capper_review_records before deleting the original.")
                message = await thread.fetch_message(int(record["record_message_id"]))
                if message.author.id != self.bot.user.id:
                    raise RuntimeError("Saved daily record is not bot-owned.")
                await message.edit(content=content, allowed_mentions=discord.AllowedMentions.none())
            else:
                slip = base64.b64decode(row["review"]["slip_base64"], validate=True)
                archive_text = record_text(row, play).encode("utf-8")
                if max(len(slip), len(archive_text)) > forum.guild.filesize_limit:
                    raise RuntimeError("The saved evidence exceeds this server's upload limit; preserve the source.")
                claimed = await asyncio.to_thread(self.store.claim, "capper_review_records", {
                    "message_id": message_id, "record_date": date, "record_message_id": "sending",
                }, "message_id")
                if not claimed:
                    raise RuntimeError("This slip record was already claimed. Reconcile its saved Discord message before retrying.")
                files = [
                    discord.File(BytesIO(archive_text), filename=f"review-{message_id}.txt"),
                    discord.File(BytesIO(slip), filename=row["review"]["image_filename"]),
                ]
                try:
                    message = await thread.send(content, files=files, allowed_mentions=discord.AllowedMentions.none())
                finally:
                    for file in files:
                        file.close()
                await asyncio.to_thread(self.store.update, "capper_review_records", {
                    "record_message_id": str(message.id), "rendered_content": content,
                }, {"message_id": message_id})
            # Deletion is allowed only after the saved archive message can be read back.
            archived = await thread.fetch_message(message.id)
            if archived.author.id != self.bot.user.id or archived.content != content or len(archived.attachments) < 2:
                raise RuntimeError("Daily record could not be verified; the original review will be preserved.")
            if record:
                await asyncio.to_thread(self.store.update, "capper_review_records", {
                    "rendered_content": content,
                }, {"message_id": message_id})
            if play and not play.get("message_id"):
                raise RuntimeError("The review was recorded, but official publication is uncertain. Preserve its source for staff reconciliation.")

    async def refresh(self) -> None:
        async with self.lock:
            snapshots = await asyncio.to_thread(self.store.snapshots)
            if snapshots:
                await self.forum()
            failed = []
            for record in snapshots:
                if record["record_message_id"] == "sending" or record["thread_id"] == "sending":
                    continue
                try:
                    if "source_deleted_at" in record and record["source_deleted_at"] is None:
                        checked = record.get("source_checked_at")
                        if checked is None or datetime.fromisoformat(checked) <= datetime.now(timezone.utc) - timedelta(days=1):
                            await self.check_source_deleted(record)
                    expired = evidence_expired(record)
                    if expired:
                        # Re-read immediately before destructive work; corrections/reopened plays cancel expiry.
                        record = await asyncio.to_thread(self.store.snapshot, record["message_id"])
                        expired = evidence_expired(record)
                        if expired and not await asyncio.to_thread(self.store.begin_expiry, record):
                            continue
                    row = summary_ballot(record["ballot"]) if expired or record.get("evidence_removed_at") else record["ballot"]
                    content = record_content(row, record["play"])
                    if expired or record.get("evidence_removed_at"):
                        content = "[Original slip evidence removed under the 365-day policy]\n" + content
                    if content == record["rendered_content"] and not expired:
                        if not await asyncio.to_thread(self.store.complete, record, content):
                            logger.warning("capper_record_changed_during_refresh ballot=%s; queued for retry", record["message_id"])
                        continue
                    thread = await self.resolve_channel(int(record["thread_id"]), "daily record thread", required=True)
                    if (not isinstance(thread, discord.Thread) or thread.guild.id != GUILD_ID
                            or thread.parent_id != int(record["forum_channel_id"])):
                        raise RuntimeError("Saved daily record thread is outside its configured forum.")
                    if thread.archived:
                        thread = await thread.edit(archived=False, reason="Update saved capper betting outcome")
                    message = await thread.fetch_message(int(record["record_message_id"]))
                    if message.author.id != self.bot.user.id:
                        raise RuntimeError("Saved daily record is not bot-owned.")
                    if expired:
                        await self.remove_published_evidence(record)
                        summary = discord.File(
                            BytesIO(record_text(row, record["play"]).encode("utf-8")),
                            filename=f"bet-summary-{record['message_id']}.txt",
                        )
                        try:
                            await message.edit(content=content, attachments=[summary], allowed_mentions=discord.AllowedMentions.none())
                        finally:
                            summary.close()
                        verified = await thread.fetch_message(message.id)
                        if (verified.content != content or len(verified.attachments) != 1
                                or verified.attachments[0].filename != f"bet-summary-{record['message_id']}.txt"):
                            raise RuntimeError("Evidence removal could not be verified; database evidence was preserved.")
                    else:
                        await message.edit(content=content, allowed_mentions=discord.AllowedMentions.none())
                    saved = await asyncio.to_thread(self.store.complete, record, content, remove_evidence=expired)
                    if not saved:
                        logger.warning("capper_record_changed_during_refresh ballot=%s; queued for retry", record["message_id"])
                except Exception:
                    logger.exception("capper_record_update_failed ballot=%s", record["message_id"])
                    failed.append(record["message_id"])
            if failed:
                raise RuntimeError(f"Could not update records for slips {', '.join(failed)}; inspect the bot log and saved Discord messages.")

    async def check_source_deleted(self, record: dict) -> None:
        guild = self.bot.get_guild(GUILD_ID)
        if guild is None:
            raise RuntimeError("Cannot verify source deletion without the configured guild.")
        channel_id = record["ballot"]["review"]["source_channel_id"]
        missing = False
        try:
            channel = await guild.fetch_channel(int(channel_id))
            await channel.fetch_message(int(record["message_id"]))
        except discord.NotFound:
            missing = True
        if missing:
            await asyncio.to_thread(self.store.source_deleted, record["message_id"])
            record["source_deleted_at"] = datetime.now(timezone.utc).isoformat()
        else:
            await asyncio.to_thread(self.store.update, "capper_review_records", {
                "source_checked_at": datetime.now(timezone.utc).isoformat(),
            }, {"message_id": record["message_id"]})

    async def remove_published_evidence(self, record: dict) -> None:
        play = record["play"]
        if not play:
            return
        deliveries = await asyncio.to_thread(self.store.deliveries, int(play["id"]))
        targets = [(int(record["ballot"].get("official_channel_id") or OFFICIAL_CHANNEL_ID), play["message_id"])]
        for delivery in deliveries:
            if delivery["message_id"] is None:
                raise RuntimeError("Result announcement delivery is uncertain; reconcile it before evidence expiry.")
            targets.append((int(delivery["channel_id"]), delivery["message_id"]))
        details = [
            "Original slip evidence removed under the retention policy.",
            f"Official stake: {play['units']}u | odds {play['odds']} | Result: {play['status']}",
        ]
        details.extend(
            f"{index}. {leg['selection']} | {int(leg['odds']):+d}" if leg.get("odds")
            else f"{index}. {leg['selection']}"
            for index, leg in enumerate(record["ballot"]["review"].get("parsed", {}).get("legs", []), 1)
        )
        public_summary = discord.utils.escape_mentions("\n".join(details))
        for channel_id, message_id in targets:
            channel = await self.resolve_channel(channel_id, "published slip evidence", required=True)
            if channel.guild.id != GUILD_ID:
                raise RuntimeError("Published evidence is outside the configured guild.")
            try:
                message = await channel.fetch_message(int(message_id))
            except discord.NotFound:
                continue
            if message.author.id != self.bot.user.id:
                raise RuntimeError("Published evidence is not bot-owned.")
            description = public_summary if len(public_summary) <= 3900 else public_summary[:3800] + "\n[Full bet details in attached summary]"
            embed = discord.Embed(title=f"Play #{play['id']} - retained bet/result summary", description=description)
            files = []
            if len(public_summary) > 3900:
                files.append(discord.File(BytesIO(public_summary.encode("utf-8")), filename=f"play-{play['id']}-summary.txt"))
            try:
                await message.edit(embed=embed, attachments=files, allowed_mentions=discord.AllowedMentions.none())
            finally:
                for file in files:
                    file.close()
            verified = await channel.fetch_message(message.id)
            if (len(verified.attachments) != len(files)
                    or (files and verified.attachments[0].filename != files[0].filename)
                    or len(verified.embeds) != 1 or verified.embeds[0].to_dict() != embed.to_dict()):
                raise RuntimeError("Published evidence removal could not be verified.")
