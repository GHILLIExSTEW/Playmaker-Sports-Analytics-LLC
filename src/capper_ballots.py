from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import datetime

import discord
from discord.ext import commands, tasks

from src.config import CAPPER_BALLOT_CHANNEL_ID, GUILD_ID, HIGHROLLER_ROLE_ID, OFFICIAL_CHANNEL_ID
from src.play_presentation import build_engagement_view, build_play_embed
from src.services.image_play_service import ImagePlayService, image_play_service
from src.services.official_play_service import OfficialPlayService
from src.services.play_service import PlayService
from src.services.supabase_service import supabase_service

logger = logging.getLogger("official_play_bot")


def ballot_rating(yes: int, no: int) -> tuple[int, int] | None:
    if yes < 0 or no < 0 or yes > 5:
        raise ValueError("Ballots require 0-5 Yes votes and a nonnegative No count.")
    if yes <= no or yes < 3:
        return None
    return yes, yes - 2


def ballot_counts(poll: discord.Poll) -> dict[str, int]:
    answers = {}
    for answer in poll.answers:
        match = re.match(r"^(yes|no|pass)\b", answer.text.strip(), re.IGNORECASE)
        if match is None or match[1].lower() in answers:
            raise ValueError("Every choice must begin with Yes, No, or Pass, without duplicates.")
        answers[match[1].lower()] = answer.vote_count
    if poll.multiple or len(poll.answers) != 3 or set(answers) != {"yes", "no", "pass"}:
        raise ValueError("Use a single-choice poll with exactly Yes, No, and Pass.")
    return answers


class BallotStore:
    def scan_state(self) -> dict:
        rows = supabase_service.select("capper_ballot_scan", "*", {"singleton": True}).data
        if not rows:
            raise RuntimeError("Apply the capper tracking migration before starting ballot processing.")
        return rows[0]

    def advance(self, message_id: int) -> None:
        supabase_service.update("capper_ballot_scan", {"last_message_id": str(message_id)}, {"singleton": True})

    def remember(self, message_id: int) -> None:
        supabase_service._execute(lambda: supabase_service._ensure_client().table("capper_ballots").upsert(
            {"message_id": str(message_id)}, on_conflict="message_id", ignore_duplicates=True,
        ).execute())

    def pending(self) -> list[dict]:
        return supabase_service.select("capper_ballots", "*", {"state": "pending"}).data or []

    def interrupted(self) -> list[dict]:
        return supabase_service.select("capper_ballots", "*", {"state": "processing"}).data or []

    def source_message_id(self, message_id: int) -> int | None:
        rows = supabase_service.select("capper_ballots", "source_message_id", {"message_id": str(message_id)}).data or []
        value = rows[0].get("source_message_id") if rows else None
        return int(value) if value else None

    def claim(self, message_id: int) -> bool:
        result = supabase_service._execute(lambda: supabase_service._ensure_client().rpc(
            "claim_capper_ballot", {"p_message_id": str(message_id)},
        ).execute())
        return result.data is True

    def finish(self, message_id: int, state: str, **values) -> None:
        result = supabase_service.update(
            "capper_ballots", {"state": state, **values}, {"message_id": str(message_id)},
        )
        if not result.data:
            raise RuntimeError(f"Ballot {message_id} state was not saved.")


class CapperBallots(commands.Cog):
    def __init__(
        self, bot: commands.Bot, *, official: OfficialPlayService,
        resolve_channel: Callable[..., Awaitable],
        is_tracked_operator: Callable[[discord.abc.User], bool],
        staff_alert: Callable[[str, str], Awaitable[None]],
        get_testing: Callable[[], bool],
        images: ImagePlayService | None = None, store: BallotStore | None = None,
    ) -> None:
        self.bot, self.official = bot, official
        self.resolve_channel, self.is_tracked_operator = resolve_channel, is_tracked_operator
        self.staff_alert, self.get_testing = staff_alert, get_testing
        self.images = images if images is not None else image_play_service
        self.store = store if store is not None else BallotStore()

    async def cog_load(self) -> None:
        if self.bot.is_ready():
            await self.on_ready()

    def cog_unload(self) -> None:
        self.evaluate_ballots.cancel()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if not self.evaluate_ballots.is_running():
            self.evaluate_ballots.start()

    async def discover(self, channel: discord.TextChannel) -> None:
        state = await asyncio.to_thread(self.store.scan_state)
        after = (
            discord.Object(id=int(state["last_message_id"]))
            if state["last_message_id"] else datetime.fromisoformat(state["started_at"])
        )
        async for message in channel.history(limit=None, after=after, oldest_first=True):
            if message.poll is not None and not message.author.bot:
                await asyncio.to_thread(self.store.remember, message.id)
            await asyncio.to_thread(self.store.advance, message.id)

    async def bet_source(self, message: discord.Message) -> tuple[discord.Attachment | None, str]:
        text = f"{message.poll.question if message.poll else ''}\n{message.content}"
        source_id = await asyncio.to_thread(self.store.source_message_id, message.id)
        if source_id is not None:
            source = await message.channel.fetch_message(source_id)
            image = next((item for item in source.attachments if (item.content_type or "").startswith("image/")), None)
            if source.poll is not None or source.author.bot or (image is None and not source.content.strip()):
                raise ValueError("The staff-linked ballot source must contain bet text or an image, not another poll.")
            return image, f"{text}\n{source.content}"
        image = next((item for item in message.attachments if (item.content_type or "").startswith("image/")), None)
        if image is not None:
            return image, text
        async for following in message.channel.history(limit=1, after=message, oldest_first=True):
            if following.author.id == message.author.id and following.poll is None:
                text += f"\n{following.content}"
                image = next((item for item in following.attachments if (item.content_type or "").startswith("image/")), None)
        return image, text

    async def evaluate(self, message: discord.Message) -> None:
        poll = message.poll
        if poll is None:
            raise ValueError("The saved ballot no longer contains a poll.")
        if not poll.is_finalised():
            return
        if not await asyncio.to_thread(self.store.claim, message.id):
            return
        try:
            if message.author.bot or not message.guild or message.guild.id != GUILD_ID:
                raise ValueError("Ballots must be posted by a capper in the configured guild.")
            member = message.guild.get_member(message.author.id) or await message.guild.fetch_member(message.author.id)
            if not self.is_tracked_operator(member):
                raise ValueError("The ballot author must hold the capper role.")
            counts = ballot_counts(poll)
            rating = ballot_rating(counts["yes"], counts["no"])
            if rating is None:
                await asyncio.to_thread(self.store.finish, message.id, "rejected", votes=counts)
                return
            stars, units = rating
            image, text = await self.bet_source(message)
            if image is not None:
                parsed = await asyncio.to_thread(self.images.extract_play, image.url, text, require_readable=True)
            else:
                parsed = await asyncio.to_thread(self.images.extract_text_play, text)
            legs = parsed["legs"]
            channel = await self.resolve_channel(OFFICIAL_CHANNEL_ID, "OFFICIAL_CHANNEL_ID", required=True)
            if channel.guild.id != message.guild.id or self.bot.user is None:
                raise RuntimeError("Official channel or bot identity is unavailable.")
            payload = await asyncio.to_thread(
                self.official.create_play_record,
                discord_user_id=str(self.bot.user.id), username="Official Playmaker Picks",
                units=units, legs=len(legs),
                odds=PlayService.combine_american_odds([int(leg["odds"]) for leg in legs]),
                play_text="\n".join(
                    f"Leg {index}: {leg['selection']} ({int(leg['odds']):+d})"
                    for index, leg in enumerate(legs, start=1)
                ),
                team_name=parsed.get("team_name"), leg_records=legs,
                tracking_scope="official", stars=stars, ballot_message_id=str(message.id),
            )
            if payload.get("error"):
                raise ValueError(payload["error"])
            await asyncio.to_thread(self.store.finish, message.id, "processing", play_id=payload["play_id"], votes=counts)
            embed = build_play_embed(payload)
            embed.set_author(name=self.bot.user.display_name, icon_url=self.bot.user.display_avatar.url)
            embed.set_footer(text=f"Team ballot: {counts['yes']} Yes / {counts['no']} No / {counts['pass']} Pass")
            file = await image.to_file() if image is not None else None
            try:
                kwargs = {}
                if file is not None:
                    embed.set_image(url=f"attachment://{file.filename}")
                    kwargs["file"] = file
                published = await channel.send(
                    content=f"<@&{HIGHROLLER_ROLE_ID}>",
                    allowed_mentions=discord.AllowedMentions(
                        everyone=False, users=False, roles=[discord.Object(id=HIGHROLLER_ROLE_ID)], replied_user=False,
                    ),
                    embed=embed, view=build_engagement_view(int(payload["play_id"]), 0), **kwargs,
                )
            finally:
                if file is not None:
                    file.close()
            await asyncio.to_thread(self.official.attach_message_id, payload["play_id"], published.id)
            await asyncio.to_thread(self.store.finish, message.id, "published", official_message_id=str(published.id))
        except Exception as exc:
            logger.exception("capper_ballot_failed message=%s", message.id)
            try:
                await asyncio.to_thread(self.store.finish, message.id, "failed", error=str(exc)[:1500])
            finally:
                await self.staff_alert(
                    f"ballot:{message.id}",
                    f"Ballot {message.jump_url} needs staff review: `{str(exc)[:1500]}`. "
                    "Check for an existing official post before retrying; it will not be republished automatically.",
                )

    @tasks.loop(minutes=1)
    async def evaluate_ballots(self) -> None:
        if self.get_testing():
            return
        try:
            channel = await self.resolve_channel(CAPPER_BALLOT_CHANNEL_ID, "CAPPER_BALLOT_CHANNEL_ID", required=True)
            if channel.guild.id != GUILD_ID:
                raise RuntimeError("The ballot channel must belong to the configured guild.")
            await self.discover(channel)
            for row in await asyncio.to_thread(self.store.pending):
                try:
                    message = await channel.fetch_message(int(row["message_id"]))
                    await self.evaluate(message)
                except discord.NotFound:
                    await asyncio.to_thread(self.store.finish, int(row["message_id"]), "failed", error="Ballot was deleted.")
                    await self.staff_alert(f"ballot:{row['message_id']}", f"Ballot {row['message_id']} was deleted before evaluation.")
        except Exception as exc:
            logger.exception("capper_ballot_scan_failed")
            await self.staff_alert("ballot_scan", f"Ballot processing failed: `{str(exc)[:1500]}`")

    @evaluate_ballots.before_loop
    async def before_evaluate_ballots(self) -> None:
        await self.bot.wait_until_ready()
        try:
            for row in await asyncio.to_thread(self.store.interrupted):
                await self.staff_alert(
                    f"ballot:{row['message_id']}",
                    f"Ballot {row['message_id']} was interrupted during publication. "
                    "Reconcile its saved play and official post before retrying; no automatic duplicate will be sent.",
                )
        except Exception as exc:
            logger.exception("capper_ballot_recovery_failed")
            await self.staff_alert("ballot_recovery", f"Could not check interrupted ballots: `{str(exc)[:1500]}`")
