from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from io import BytesIO
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands

from src.config import CAPPER_RECORD_CHANNEL_ID, GUILD_ID
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
                lambda: supabase_service._ensure_client().table("capper_review_record_snapshots")
                .select("*").order("message_id").range(offset, offset + 99).execute(),
            ).data or []
            rows.extend(batch)
            if len(batch) < 100:
                return rows
            offset += 100


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
        text = text[:1400] + "\n[Full bet details in attached archive-time text file]\n" + "\n".join(text.splitlines()[-result_lines:])
    return text


class ReviewRecords:
    def __init__(
        self, bot: commands.Bot, resolve_channel: Callable[..., Awaitable], *, store: ReviewRecordStore | None = None,
    ) -> None:
        self.bot, self.resolve_channel = bot, resolve_channel
        self.store = store if store is not None else ReviewRecordStore()
        self.lock = asyncio.Lock()

    async def forum(self) -> discord.ForumChannel:
        forum = await self.resolve_channel(CAPPER_RECORD_CHANNEL_ID, "CAPPER_RECORD_CHANNEL_ID", required=True)
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
                "Untracked personal bets are not graded here.",
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
                claimed = await asyncio.to_thread(self.store.claim, "capper_review_records", {
                    "message_id": message_id, "record_date": date, "record_message_id": "sending",
                }, "message_id")
                if not claimed:
                    raise RuntimeError("This slip record was already claimed. Reconcile its saved Discord message before retrying.")
                files = [
                    discord.File(BytesIO(record_text(row, play).encode("utf-8")), filename=f"review-{message_id}.txt"),
                    discord.File(BytesIO(base64.b64decode(row["review"]["slip_base64"], validate=True)),
                                 filename=row["review"]["image_filename"]),
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
            failed = []
            for record in snapshots:
                if record["record_message_id"] == "sending" or record["thread_id"] == "sending":
                    continue
                content = record_content(record["ballot"], record["play"])
                if content == record["rendered_content"]:
                    continue
                try:
                    thread = await self.resolve_channel(int(record["thread_id"]), "daily record thread", required=True)
                    if (not isinstance(thread, discord.Thread) or thread.guild.id != GUILD_ID
                            or thread.parent_id != int(record["forum_channel_id"])):
                        raise RuntimeError("Saved daily record thread is outside its configured forum.")
                    if thread.archived:
                        thread = await thread.edit(archived=False, reason="Update saved capper betting outcome")
                    message = await thread.fetch_message(int(record["record_message_id"]))
                    if message.author.id != self.bot.user.id:
                        raise RuntimeError("Saved daily record is not bot-owned.")
                    await message.edit(content=content, allowed_mentions=discord.AllowedMentions.none())
                    await asyncio.to_thread(self.store.update, "capper_review_records", {
                        "rendered_content": content,
                    }, {"message_id": record["message_id"]})
                except Exception:
                    logger.exception("capper_record_update_failed ballot=%s", record["message_id"])
                    failed.append(record["message_id"])
            if failed:
                raise RuntimeError(f"Could not update records for slips {', '.join(failed)}; inspect the bot log and saved Discord messages.")
