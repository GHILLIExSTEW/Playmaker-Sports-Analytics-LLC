from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands, tasks

from src.config import CAPPER_BALLOT_CHANNEL_ID, GUILD_ID, HIGHROLLER_ROLE_ID, OFFICIAL_CHANNEL_ID
from src.capper_chat_votes import ChatVoteCoordinator
from src.services.ballot_vote_service import POLICY_VERSION, ballot_rating
from src.play_presentation import build_engagement_view, build_play_embed
from src.services.image_play_service import BallotUnreadable, ImagePlayService, image_play_service
from src.services.official_play_service import OfficialPlayService
from src.services.play_service import PlayService
from src.services.play_card_service import play_card_service
from src.services.supabase_service import supabase_service

logger = logging.getLogger("official_play_bot")
BALLOT_CLEANUP_TIME = time(hour=3, tzinfo=ZoneInfo("America/New_York"))


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

    def remember(self, message_id: int, eligible_voter_ids: list[str]) -> None:
        supabase_service._execute(lambda: supabase_service._ensure_client().table("capper_ballots").upsert(
            {"message_id": str(message_id), "eligible_voter_ids": eligible_voter_ids, "policy_version": POLICY_VERSION},
            on_conflict="message_id", ignore_duplicates=True,
        ).execute())

    def hold(self, message_id: int, reason: str) -> None:
        supabase_service._execute(lambda: supabase_service._ensure_client().table("capper_ballots").upsert(
            {"message_id": str(message_id), "state": "failed", "error": reason, "policy_version": POLICY_VERSION},
            on_conflict="message_id", ignore_duplicates=True,
        ).execute())

    def get(self, message_id: int) -> dict:
        rows = supabase_service.select("capper_ballots", "*", {"message_id": str(message_id)}).data or []
        if not rows:
            raise ValueError("The ballot has no saved eligibility snapshot.")
        return rows[0]

    def pending(self) -> list[dict]:
        return supabase_service.select("capper_ballots", "*", {"state": "pending"}).data or []

    def interrupted(self) -> list[dict]:
        return supabase_service.select("capper_ballots", "*", {"state": "processing"}).data or []

    def source_message_id(self, message_id: int) -> int | None:
        rows = supabase_service.select("capper_ballots", "source_message_id", {"message_id": str(message_id)}).data or []
        value = rows[0].get("source_message_id") if rows else None
        return int(value) if value else None

    def claim(self, message_id: int, decision: dict) -> bool:
        result = supabase_service._execute(lambda: supabase_service._ensure_client().rpc(
            "claim_rated_capper_ballot", {"p_message_id": str(message_id), "p_decision": decision},
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
        tracked_operators: Callable[[], Awaitable[set[int] | None]],
        images: ImagePlayService | None = None, store: BallotStore | None = None,
        chat_votes: ChatVoteCoordinator | None = None,
    ) -> None:
        self.bot, self.official = bot, official
        self.resolve_channel, self.is_tracked_operator = resolve_channel, is_tracked_operator
        self.staff_alert, self.get_testing = staff_alert, get_testing
        self.tracked_operators = tracked_operators
        self.images = images if images is not None else image_play_service
        self.store = store if store is not None else BallotStore()
        self.chat_votes = chat_votes if chat_votes is not None else ChatVoteCoordinator()
        self.ballot_lock = asyncio.Lock()

    async def cog_load(self) -> None:
        if self.bot.is_ready():
            await self.on_ready()

    def cog_unload(self) -> None:
        self.evaluate_ballots.cancel()
        self.daily_ballot_cleanup.cancel()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if not self.evaluate_ballots.is_running():
            self.evaluate_ballots.start()
        if not self.daily_ballot_cleanup.is_running():
            self.daily_ballot_cleanup.start()

    async def discover(self, channel: discord.TextChannel) -> None:
        state = await asyncio.to_thread(self.store.scan_state)
        after = (
            discord.Object(id=int(state["last_message_id"]))
            if state["last_message_id"] else datetime.fromisoformat(state["started_at"])
        )
        async for message in channel.history(limit=None, after=after, oldest_first=True):
            if message.poll is not None and not message.author.bot:
                member = channel.guild.get_member(message.author.id) or await channel.guild.fetch_member(message.author.id)
                if not self.is_tracked_operator(member):
                    await asyncio.to_thread(self.store.advance, message.id)
                    continue
                reason = None
                if message.created_at < datetime.fromisoformat(state["policy_started_at"]):
                    reason = "Legacy ballot predates the dynamic policy. Reconcile and repost with a fresh roster."
                else:
                    member_ids = await self.tracked_operators()
                    if member_ids is None or len(member_ids) not in {3, 4, 5}:
                        reason = "Cannot verify a roster of 3-5 eligible cappers. Reconcile and repost this ballot."
                    elif message.author.id not in member_ids:
                        reason = "The ballot author is not on the eligible roster."
                    else:
                        await asyncio.to_thread(
                            self.store.remember, message.id, sorted(str(member_id) for member_id in member_ids),
                        )
                if reason is not None:
                    await asyncio.to_thread(self.store.hold, message.id, reason)
                    await self.staff_alert(
                        f"ballot:{message.id}", f"Ballot {message.jump_url} needs staff review: {reason}",
                    )
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

    async def native_votes(self, poll: discord.Poll, roster: set[str]) -> dict[str, str]:
        ballot_counts(poll)
        votes: dict[str, str] = {}
        for answer in poll.answers:
            choice = re.match(r"^(yes|no|pass)\b", answer.text.strip(), re.IGNORECASE)
            if choice is None:
                raise ValueError("Invalid poll answer.")
            seen_voters: set[int] = set()
            async for voter in answer.voters(limit=None):
                seen_voters.add(voter.id)
                voter_id = str(voter.id)
                if voter.bot or voter_id not in roster:
                    logger.info("capper_ballot_ineligible_vote voter=%s", voter_id)
                    continue
                if voter_id in votes:
                    raise ValueError("One capper appears in multiple poll answers. Wait for Discord to reconcile the poll.")
                votes[voter_id] = choice[1].lower()
            if len(seen_voters) != answer.vote_count:
                raise ValueError("Discord's voter list does not match its poll total. Staff must reconcile this ballot.")
        return votes

    async def evaluate(self, message: discord.Message, active_ballots: list | None = None) -> None:
        poll = message.poll
        if poll is None:
            raise ValueError("The saved ballot no longer contains a poll.")
        try:
            if message.author.bot or not message.guild or message.guild.id != GUILD_ID:
                raise ValueError("Ballots must be posted by a capper in the configured guild.")
            member = message.guild.get_member(message.author.id) or await message.guild.fetch_member(message.author.id)
            if not self.is_tracked_operator(member):
                raise ValueError("The ballot author must hold the capper role.")
            row = await asyncio.to_thread(self.store.get, message.id)
            if row["state"] != "pending":
                return
            roster = set(row.get("eligible_voter_ids") or [])
            ballot_rating(0, 0, len(roster))
            if row.get("policy_version") != POLICY_VERSION:
                raise ValueError("This ballot requires staff reconciliation before using dynamic voting.")
            native = await self.native_votes(poll, roster)
            inferred, waiting = await self.chat_votes.collect(message, row, native, active_ballots or [message])
            if not poll.is_finalised() or waiting:
                return
            votes = {**inferred, **native}
            counts = {choice: sum(value == choice for value in votes.values()) for choice in ("yes", "no", "pass")}
            rating = ballot_rating(counts["yes"], counts["no"], len(roster))
            if rating is None:
                await asyncio.to_thread(self.store.finish, message.id, "rejected", votes=counts)
                return
            stars, units = rating
            decision = {
                "policy_version": POLICY_VERSION, "roster_size": len(roster), "yes": counts["yes"],
                "counts": counts, "voter_choices": votes, "native_voter_ids": sorted(native),
                "chat_voter_ids": sorted(set(inferred) - set(native)), "stars": stars, "units": units,
            }
            if not await asyncio.to_thread(self.store.claim, message.id, decision):
                return
            image, text = await self.bet_source(message)
            if image is not None:
                parsed = await asyncio.to_thread(self.images.extract_play, image.url, text, require_readable=True)
            else:
                parsed = await asyncio.to_thread(self.images.extract_text_play, text)
            await self.publish_approved(
                message, parsed, decision, slip=await image.read() if image is not None else None,
            )
        except Exception as exc:
            logger.exception("capper_ballot_failed message=%s", message.id)
            next_step = (
                "Ask the capper to post the slip or the odds for every leg in this channel, set "
                "`capper_ballots.source_message_id` to that message ID, then reset the ballot to pending."
                if isinstance(exc, BallotUnreadable) else
                "Check for an existing official post before retrying; it will not be republished automatically."
            )
            try:
                await asyncio.to_thread(self.store.finish, message.id, "failed", error=str(exc)[:1500])
            finally:
                await self.staff_alert(
                    f"ballot:{message.id}",
                    f"Ballot {message.jump_url} needs staff review: `{str(exc)[:1500]}`. {next_step}",
                )

    async def publish_approved(
        self, message: discord.Message, parsed: dict, decision: dict, *, slip: bytes | None,
    ) -> dict:
        """Publish only after the caller has durably claimed the final decision."""
        legs, units, stars = parsed["legs"], decision["units"], decision["stars"]
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
            team_approval=decision,
        )
        if payload.get("error"):
            raise ValueError(payload["error"])
        await asyncio.to_thread(
            self.store.finish, message.id, "processing", play_id=payload["play_id"], votes=decision["counts"],
        )
        embed = build_play_embed(payload)
        embed.set_author(name=self.bot.user.display_name, icon_url=self.bot.user.display_avatar.url)
        file = await play_card_service.file(
            {**payload, "id": payload["play_id"], "status": "open"}, message.guild, slip=slip, legs=legs,
        )
        try:
            kwargs = {}
            if file is not None:
                embed.clear_fields()
                embed.set_image(url=f"attachment://{file.filename}")
                kwargs["file"] = file
            if decision.get("event_starts_at") and datetime.now(timezone.utc) >= datetime.fromisoformat(decision["event_starts_at"]):
                raise ValueError("The earliest event started before the official post could be sent.")
            if self.get_testing():
                raise ValueError("Official publication is disabled in testing mode.")
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
        return payload

    @tasks.loop(minutes=1)
    async def evaluate_ballots(self) -> None:
        if self.get_testing():
            return
        try:
            async with self.ballot_lock:
                channel = await self.resolve_channel(CAPPER_BALLOT_CHANNEL_ID, "CAPPER_BALLOT_CHANNEL_ID", required=True)
                if channel.guild.id != GUILD_ID:
                    raise RuntimeError("The ballot channel must belong to the configured guild.")
                await self.discover(channel)
                messages = []
                for row in await asyncio.to_thread(self.store.pending):
                    try:
                        message = await channel.fetch_message(int(row["message_id"]))
                        messages.append(message)
                    except discord.NotFound:
                        await asyncio.to_thread(self.store.finish, int(row["message_id"]), "failed", error="Ballot was deleted.")
                        await self.staff_alert(f"ballot:{row['message_id']}", f"Ballot {row['message_id']} was deleted before evaluation.")
                for message in messages:
                    await self.evaluate(message, messages)
        except Exception as exc:
            logger.exception("capper_ballot_scan_failed")
            await self.staff_alert("ballot_scan", f"Ballot processing failed: `{str(exc)[:1500]}`")

    @tasks.loop(time=BALLOT_CLEANUP_TIME)
    async def daily_ballot_cleanup(self) -> None:
        if self.get_testing():
            return
        try:
            async with self.ballot_lock:
                channel = await self.resolve_channel(CAPPER_BALLOT_CHANNEL_ID, "CAPPER_BALLOT_CHANNEL_ID", required=True)
                if channel.guild.id != GUILD_ID:
                    raise RuntimeError("The ballot channel must belong to the configured guild.")
                active_ballots = await asyncio.to_thread(self.store.pending)
                active_ballots.extend(await asyncio.to_thread(self.store.interrupted))
                for row in active_ballots:
                    await asyncio.to_thread(
                        self.store.finish,
                        int(row["message_id"]),
                        "failed",
                        error="Ballot post removed by the scheduled 3:00 AM Eastern channel cleanup.",
                    )
                deleted = await channel.purge(
                    limit=None,
                    reason="Scheduled daily capper ballot channel cleanup",
                )
                logger.info("capper_ballot_channel_cleanup_complete deleted=%s", len(deleted))
        except Exception as exc:
            logger.exception("capper_ballot_channel_cleanup_failed")
            await self.staff_alert(
                "ballot_channel_cleanup",
                f"Daily capper ballot channel cleanup failed: `{str(exc)[:1500]}`.",
            )

    @daily_ballot_cleanup.before_loop
    async def before_daily_ballot_cleanup(self) -> None:
        await self.bot.wait_until_ready()

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

    @commands.Cog.listener("on_interaction")
    async def on_vote_verification(self, interaction: discord.Interaction) -> None:
        custom_id = str((interaction.data or {}).get("custom_id") or "")
        if not custom_id.startswith("pm:cv:"):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            if self.get_testing():
                raise ValueError("Ballot verification is disabled in testing mode.")
            match = re.fullmatch(r"pm:cv:(\d{1,20}):(confirm|reject)", custom_id)
            if match is None or interaction.guild_id != GUILD_ID or interaction.channel_id != CAPPER_BALLOT_CHANNEL_ID:
                raise ValueError("This verification control is not in the configured capper ballot channel.")
            if interaction.user.bot or not self.is_tracked_operator(interaction.user):
                raise ValueError("Only eligible cappers may verify statements.")
            async with self.ballot_lock:
                request = await asyncio.to_thread(self.chat_votes.store.get, match[1])
                if interaction.message is None or str(interaction.message.id) not in {
                    request["prompt_message_id"], request["escalation_message_id"],
                }:
                    raise ValueError("This control does not belong to the saved verification request.")
                channel = await self.resolve_channel(CAPPER_BALLOT_CHANNEL_ID, "CAPPER_BALLOT_CHANNEL_ID", required=True)
                if not await self.chat_votes.valid_source(channel, request):
                    await asyncio.to_thread(self.chat_votes.store.update, match[1], state="invalid")
                    raise ValueError("The linked statement changed or was deleted. Cast a native poll vote instead.")
                status = await asyncio.to_thread(
                    self.chat_votes.store.approve, match[1], str(interaction.user.id), match[2] == "reject",
                )
            await interaction.followup.send({
                "confirmed": "Statement verified. Its vote can now count unless the capper casts a native poll vote.",
                "rejected": "Inference rejected. It will not count.",
                "awaiting-second-capper": "Your verification was saved. A second, distinct eligible capper must also approve.",
            }[status], ephemeral=True)
        except Exception as exc:
            logger.exception("capper_vote_verification_failed user=%s", interaction.user.id)
            await interaction.followup.send(f"Verification not accepted: {str(exc)[:1000]}", ephemeral=True)
