from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

import discord

from src.config import CAPPER_BALLOT_CHANNEL_ID, TRACKER_ROLE_ID
from src.services.ballot_vote_service import ChatVoteStore, infer_vote, statement_matches_ballot

logger = logging.getLogger("official_play_bot")
LOOKBACK = timedelta(minutes=30)
ESCALATION_DELAY = timedelta(minutes=10)


def verification_view(source_id: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for action, label, style in (
        ("confirm", "Confirm linked statement", discord.ButtonStyle.success),
        ("reject", "Not my vote (author only)", discord.ButtonStyle.danger),
    ):
        view.add_item(discord.ui.Button(
            label=label, style=style, custom_id=f"pm:cv:{source_id}:{action}",
        ))
    view.stop()
    return view


class ChatVoteCoordinator:
    def __init__(self, store: ChatVoteStore | None = None) -> None:
        self.store = store if store is not None else ChatVoteStore()

    async def valid_source(self, channel, row: dict) -> bool:
        try:
            source = await channel.fetch_message(int(row["source_message_id"]))
        except discord.NotFound:
            return False
        return (
            str(source.author.id) == row["voter_id"] and not source.author.bot
            and source.content == row["source_text"]
            and source.edited_at == (
                datetime.fromisoformat(row["source_edited_at"]) if row.get("source_edited_at") else None
            )
        )

    @staticmethod
    def prompt(row: dict, ballot, *, escalation: bool = False) -> str:
        mention = f"<@&{TRACKER_ROLE_ID}>" if escalation else f"<@{row['voter_id']}>"
        instruction = (
            "No confirmation was received within 10 minutes. Two other eligible cappers must each "
            "approve this exact linked statement, or its author can confirm their own vote."
            if escalation else
            "Please confirm or reject your inferred vote using the buttons. Nothing counts yet."
        )
        return (
            f"{mention} Inferred **{row['choice'].upper()}** by <@{row['voter_id']}> for "
            f"[this ballot]({ballot.jump_url}): **{discord.utils.escape_markdown(ballot.poll.question)[:300]}**.\n"
            f"[Original statement](https://discord.com/channels/{ballot.guild.id}/"
            f"{row['source_channel_id']}/{row['source_message_id']})\n"
            f"> {discord.utils.escape_mentions(discord.utils.escape_markdown(row['source_text']))[:500]}\n"
            f"{instruction} A native poll vote always takes precedence."
        )

    async def collect(self, ballot, ballot_row: dict, native: dict[str, str], active_ballots: list) -> tuple[dict[str, str], bool]:
        channel = ballot.channel
        roster = set(ballot_row["eligible_voter_ids"])
        rows = await asyncio.to_thread(self.store.requests, ballot.id)
        known = {row["voter_id"]: row for row in rows}
        expires = ballot.poll.expires_at
        if expires is None:
            raise ValueError("The ballot has no verifiable expiry time.")
        # Include pre-poll discussion, but never infer a statement made after voting closed.
        async for source in channel.history(
            limit=None, after=ballot.created_at - LOOKBACK, before=expires, oldest_first=True,
        ):
            voter_id = str(source.author.id)
            if source.author.bot or source.poll is not None or voter_id not in roster or voter_id in native:
                continue
            choice = infer_vote(source.content)
            if choice is None or not statement_matches_ballot(source.content, ballot.poll.question):
                continue
            links = re.findall(r"discord(?:app)?\.com/channels/\d+/\d+/(\d+)", source.content)
            reference = source.reference.message_id if source.reference else None
            candidates = [
                item for item in active_ballots
                if item.created_at - LOOKBACK < source.created_at < item.poll.expires_at
            ]
            if links or reference:
                targets = set(links) | ({str(reference)} if reference else set())
                if len(set(links)) > 1 or (
                    reference and links and str(reference) not in links
                ):
                    logger.info("chat_vote_ambiguous_links source=%s", source.id)
                    continue
                if not targets.intersection({str(ballot.id), str(ballot_row.get("source_message_id"))}):
                    if links or not reference or len(candidates) != 1 or candidates[0].id != ballot.id:
                        continue
                    try:
                        context = await channel.fetch_message(reference)
                    except discord.NotFound:
                        logger.info("chat_vote_reply_context_deleted source=%s", source.id)
                        continue
                    if (
                        context.poll is not None or context.author.bot or str(context.author.id) not in roster
                        or not ballot.created_at - LOOKBACK < context.created_at < expires
                        or infer_vote(context.content) is None
                        or not statement_matches_ballot(context.content, ballot.poll.question)
                    ):
                        continue
                    if choice == "yes" and infer_vote(context.content) != "yes":
                        logger.info("chat_vote_ambiguous_agreement source=%s context=%s", source.id, reference)
                        continue
            else:
                if len(candidates) != 1 or candidates[0].id != ballot.id:
                    logger.info("chat_vote_ambiguous source=%s ballots=%s", source.id, len(candidates))
                    continue
            previous = known.get(voter_id)
            if previous is not None:
                if (
                    int(source.id) > int(previous["source_message_id"]) and choice != previous["choice"]
                    and previous["state"] not in {"invalid", "rejected"}
                ):
                    await asyncio.to_thread(self.store.update, previous["source_message_id"], state="invalid")
                    previous["state"] = "invalid"
                    await channel.send(
                        f"Conflicting chat statements by <@{voter_id}> were found for {ballot.jump_url}. "
                        "Their inferred vote will not count. Please vote directly on the poll.",
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                continue
            await asyncio.to_thread(self.store.remember, {
                "source_message_id": str(source.id), "ballot_message_id": str(ballot.id),
                "source_channel_id": str(channel.id), "voter_id": voter_id,
                "choice": choice, "source_text": source.content,
                "source_edited_at": source.edited_at.isoformat() if source.edited_at else None,
            })
            known[voter_id] = {
                "source_message_id": str(source.id), "choice": choice, "state": "pending",
            }
        rows = await asyncio.to_thread(self.store.requests, ballot.id)
        confirmed: dict[str, str] = {}
        waiting = False
        for row in rows:
            source_id = row["source_message_id"]
            if row["voter_id"] in native or row["state"] in {"rejected", "invalid"}:
                continue
            if not await self.valid_source(channel, row):
                await asyncio.to_thread(self.store.update, source_id, state="invalid")
                await channel.send(
                    f"Vote inference for <@{row['voter_id']}> was invalidated because its source changed "
                    f"or was deleted. Vote directly on {ballot.jump_url}.",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                continue
            if row["state"] == "confirmed":
                confirmed[row["voter_id"]] = row["choice"]
                continue
            waiting = True
            if not row["prompt_message_id"]:
                # Persist the attempt first. An uncertain send is held for staff, never automatically duplicated.
                await asyncio.to_thread(self.store.update, source_id, prompt_message_id="sending")
                sent = await channel.send(
                    self.prompt(row, ballot), view=verification_view(source_id),
                    reference=discord.MessageReference(
                        message_id=int(source_id), channel_id=channel.id, guild_id=ballot.guild.id,
                    ),
                    allowed_mentions=discord.AllowedMentions(
                        everyone=False, roles=False, users=[discord.Object(id=int(row["voter_id"]))], replied_user=False,
                    ),
                )
                await asyncio.to_thread(
                    self.store.update, source_id, prompt_message_id=str(sent.id),
                    prompted_at=datetime.now(timezone.utc).isoformat(),
                )
            elif row["prompt_message_id"] == "sending" or row["escalation_message_id"] == "sending":
                raise RuntimeError(f"Verification prompt {source_id} was interrupted; reconcile its Discord post before retrying.")
            elif not row["escalation_message_id"] and datetime.now(timezone.utc) >= (
                datetime.fromisoformat(row["prompted_at"]) + ESCALATION_DELAY
            ):
                if channel.id != CAPPER_BALLOT_CHANNEL_ID or not TRACKER_ROLE_ID:
                    raise RuntimeError("Capper verification escalation channel/role is unavailable.")
                role = ballot.guild.get_role(TRACKER_ROLE_ID)
                if role is None or (
                    not role.mentionable and not channel.permissions_for(ballot.guild.me).mention_everyone
                ):
                    raise RuntimeError("The bot cannot ping the capper role. Enable role mentions or grant the bot mention permission.")
                await asyncio.to_thread(self.store.update, source_id, escalation_message_id="sending")
                sent = await channel.send(
                    self.prompt(row, ballot, escalation=True), view=verification_view(source_id),
                    allowed_mentions=discord.AllowedMentions(
                        everyone=False, users=False, roles=[discord.Object(id=TRACKER_ROLE_ID)], replied_user=False,
                    ),
                )
                await asyncio.to_thread(
                    self.store.update, source_id, escalation_message_id=str(sent.id),
                    escalated_at=datetime.now(timezone.utc).isoformat(),
                )
        return confirmed, waiting
