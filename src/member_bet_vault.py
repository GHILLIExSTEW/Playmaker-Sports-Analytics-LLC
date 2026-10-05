from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from io import BytesIO

import discord
from discord.ext import tasks

from src.config import OPERATOR_ROLE_IDS
from src.datetime_utils import parse_iso_datetime
from src.services.member_bet_service import (
    MemberBetService, SETTLED, accepted_photo, sanitize_photo, validate_ticket,
)
from src.services.play_service import PlayService
from src.services.membership_service import MembershipService

logger = logging.getLogger("member_bet_vault")


def can_moderate(user) -> bool:
    return isinstance(user, discord.Member) and (
        user.guild_permissions.manage_guild
        or any(role.id in OPERATOR_ROLE_IDS for role in user.roles)
    )


def card_embed(row: dict) -> discord.Embed:
    status = row["status"]
    settled = status in SETTLED
    labels = {
        "processing": "Processing", "draft": "Awaiting confirmation",
        "open": "Open", "review": "Moderator review required",
        "win": "Won", "loss": "Lost", "void": "Push / Void",
    }
    embed = discord.Embed(
        title=f"Member Bet #{row['id']} - {labels[status]}",
        description=f"Submitted by <@{row['owner_id']}>. Not an official Playmaker pick.",
        color=discord.Color.green() if status == "win" else discord.Color.red() if status == "loss" else discord.Color.blurple(),
    )
    # Draft OCR is private until the author has reviewed and confirmed it.
    if row.get("confirmed_at"):
        embed.add_field(name="Game / Match", value=row["details"]["game"][:1024], inline=False)
        embed.add_field(name="Units", value=f"{float(row['units']):g}u")
        embed.add_field(name="Odds", value=f"{int(row['odds']):+d}")
    if settled:
        embed.add_field(name="Selections", value="\n".join(
            leg["selection"] for leg in row["details"]["legs"]
        )[:1024], inline=False)
        net = PlayService.calculate_to_win(float(row["units"]), int(row["odds"])) if status == "win" else -float(row["units"]) if status == "loss" else 0
        embed.add_field(name="Net units", value=f"{net:+g}u")
        embed.add_field(name="Verification", value=row["verification"], inline=False)
    else:
        embed.add_field(name="Selections", value="Hidden until settled.", inline=False)
        if not row.get("original_removed", True):
            embed.add_field(
                name="Upload visibility warning",
                value="Removal of the original upload is not confirmed. It may still be visible.",
                inline=False,
            )
        if status == "review":
            embed.add_field(
                name="Action required",
                value="A moderator must review this ticket. Its selection remains hidden.",
                inline=False,
            )
    embed.set_footer(text=f"Member Vault ticket {row['id']} | Units are personal, not dollar amounts.")
    embed.timestamp = parse_iso_datetime(row["created_at"])
    return embed


def review_embed(row: dict) -> discord.Embed:
    embed = discord.Embed(title=f"Private review - Member Bet #{row['id']}")
    details = row["details"]
    embed.add_field(name="Game / Match", value=details.get("game") or "Not readable", inline=False)
    embed.add_field(name="Units", value=str(row.get("units") or "Required"))
    embed.add_field(name="Ticket odds", value=str(row.get("odds") or "Required"))
    legs = details.get("legs", [])
    field_limit = min(1024, 4400 // max(1, len(legs)))
    for index, leg in enumerate(legs, 1):
        text = (
            f"{leg['selection']} ({leg['odds']:+d})\n"
            f"API grading: {leg.get('sport', 'unknown')} / {leg.get('market', 'unknown')} / "
            f"{leg.get('side', 'unknown')} / line {leg.get('line')}\n"
            f"Home: {leg.get('home_name')} | Away: {leg.get('away_name')}\n"
            f"Date (Eastern): {leg.get('event_date')} | Scope: {leg.get('scope', 'unknown')}"
        )
        embed.add_field(name=f"Leg {index}", value=text[:field_limit], inline=False)
    if row.get("review_reason"):
        embed.add_field(name="Review reason", value=row["review_reason"][:500], inline=False)
    embed.set_footer(text="Full details are in the private review attachment.")
    return embed


def ticket_file(row: dict, private: bool = False) -> discord.File:
    lines = [
        f"Member Bet #{row['id']}", "Not an official Playmaker pick.",
        f"Game / Match: {row['details'].get('game')}",
        f"Units: {row.get('units')}", f"Ticket odds: {row.get('odds')}",
    ]
    for index, leg in enumerate(row["details"].get("legs", []), 1):
        lines.append(f"\nLeg {index}: {leg['selection']} ({leg['odds']:+d})")
        if private:
            lines.extend(f"{field}: {leg.get(field)}" for field in (
                "sport", "home_name", "away_name", "event_date", "market", "side", "line", "scope",
            ))
    if private and row.get("review_reason"):
        lines.append(f"\nReview reason: {row['review_reason']}")
    if not private:
        lines.extend([f"\nResult: {row['status']}", f"Verification: {row.get('verification')}"])
    suffix = "private-review" if private else "settled-selections"
    return discord.File(BytesIO("\n".join(lines).encode("utf-8")), filename=f"member-bet-{row['id']}-{suffix}.txt")


class ConfirmTicketModal(discord.ui.Modal, title="Confirm member ticket"):
    units = discord.ui.TextInput(label="Units (not dollars)", max_length=20)
    odds = discord.ui.TextInput(label="Actual ticket odds (American)", max_length=20)
    game = discord.ui.TextInput(label="Game/match names ONLY (public)", max_length=500)

    def __init__(self, vault: MemberBetVault, row: dict):
        super().__init__()
        self.vault = vault
        self.bet_id = row["id"]
        self.units.default = str(row.get("units") or "")
        self.odds.default = str(row.get("odds") or "")
        self.game.default = row["details"]["game"]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            validate_ticket(self.units.value, self.odds.value)
            async with self.vault.lock:
                if not await asyncio.to_thread(self.vault.membership.has_vault_access, interaction.user.id):
                    raise ValueError("A current verified paid membership, eligible trial, or owner grant is required to confirm a new ticket.")
                row = await asyncio.to_thread(self.vault.service.get, self.bet_id)
                if row["owner_id"] != str(interaction.user.id) or row["status"] != "draft":
                    raise ValueError("Only the uploader can confirm an awaiting-confirmation ticket.")
                # Only matchup text is editable here; selection/market corrections require resubmission.
                details = {**row["details"], "game": self.game.value.strip()}
                if not details["game"]:
                    raise ValueError("Game/match names are required.")
                await asyncio.to_thread(self.vault.service.update, self.bet_id, {"details": details}, "draft")
                row = await asyncio.to_thread(
                    self.vault.service.confirm, self.bet_id, interaction.user.id,
                    self.units.value, self.odds.value,
                )
                await self.vault.publish(row)
            await interaction.followup.send(
                "Ticket confirmed and locked. " + (
                    "API verification is scheduled." if row["status"] == "open"
                    else "A moderator must settle this ticket."
                ), ephemeral=True,
            )
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
        except Exception:
            logger.exception("member_confirm_failed bet=%s", self.bet_id)
            await interaction.followup.send(
                "Could not complete confirmation. Check the card before retrying; moderators have a logged error.",
                ephemeral=True,
            )


class SettleTicketModal(discord.ui.Modal, title="Moderator settlement"):
    result = discord.ui.TextInput(label="Result: win, loss, or void", max_length=4)
    reason = discord.ui.TextInput(label="Reason / evidence (private audit)", style=discord.TextStyle.paragraph, max_length=1000)

    def __init__(self, vault: MemberBetVault, bet_id: int):
        super().__init__()
        self.vault = vault
        self.bet_id = bet_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not can_moderate(interaction.user):
            await interaction.response.send_message("Only moderators can settle member tickets.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            async with self.vault.lock:
                row = await asyncio.to_thread(self.vault.service.get, self.bet_id)
                if not row.get("confirmed_at"):
                    raise ValueError("This ticket was never confirmed. Ask the member to resubmit a readable slip.")
                changed = await asyncio.to_thread(
                    self.vault.service.settle, self.bet_id, str(interaction.user.id),
                    self.result.value.strip().lower(), "Moderator-settled", self.reason.value,
                )
                if not changed:
                    raise ValueError("The ticket is already settled or is not ready for settlement.")
                await self.vault.publish(await asyncio.to_thread(self.vault.service.get, self.bet_id))
            await interaction.followup.send("Settlement saved with a private audit record.", ephemeral=True)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
        except Exception:
            logger.exception("member_settlement_failed bet=%s", self.bet_id)
            await interaction.followup.send(
                "Settlement or card update failed. Check the card before retrying; the error was logged.",
                ephemeral=True,
            )


class PrivateReviewView(discord.ui.View):
    def __init__(self, vault: MemberBetVault, row: dict):
        super().__init__(timeout=600)
        self.vault = vault
        self.row = row

    @discord.ui.button(label="Details are correct - confirm", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.row["owner_id"]:
            await interaction.response.send_message("Only the uploader can confirm.", ephemeral=True)
            return
        await interaction.response.send_modal(ConfirmTicketModal(self.vault, self.row))


class ModeratorSettlementView(discord.ui.View):
    def __init__(self, vault: MemberBetVault, bet_id: int):
        super().__init__(timeout=600)
        self.vault = vault
        self.bet_id = bet_id

    @discord.ui.button(label="Enter result and reason", style=discord.ButtonStyle.secondary)
    async def settle(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not can_moderate(interaction.user):
            await interaction.response.send_message("Only moderators can settle member tickets.", ephemeral=True)
            return
        await interaction.response.send_modal(SettleTicketModal(self.vault, self.bet_id))


class VaultCardView(discord.ui.View):
    def __init__(self, vault: MemberBetVault):
        super().__init__(timeout=None)
        self.vault = vault

    async def row_for_interaction(self, interaction: discord.Interaction) -> dict | None:
        if interaction.channel_id != self.vault.channel_id or interaction.message is None:
            await interaction.response.send_message("This is not a member-vault card.", ephemeral=True)
            return None
        await interaction.response.defer(ephemeral=True)
        try:
            row = await asyncio.to_thread(self.vault.service.for_card, interaction.message.id)
        except Exception:
            logger.exception("member_card_lookup_failed message=%s", interaction.message.id)
            await interaction.followup.send("Ticket lookup failed. Please retry; the error was logged.", ephemeral=True)
            return None
        if row is None:
            await interaction.followup.send("This ticket is not available yet. Please retry shortly.", ephemeral=True)
        return row

    @discord.ui.button(label="Private review / confirm", custom_id="member-vault:review", style=discord.ButtonStyle.primary)
    async def review(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = await self.row_for_interaction(interaction)
        if row is None:
            return
        if str(interaction.user.id) != row["owner_id"] and not can_moderate(interaction.user):
            await interaction.followup.send("Only the uploader or moderators can review hidden details.", ephemeral=True)
            return
        if not row["details"].get("legs"):
            await interaction.followup.send(
                "The slip is still processing, or could not be read. Check its status; unreadable slips need resubmission.",
                ephemeral=True,
            )
            return
        text = (
            "Review every extracted field in the attached private review, including the API grading fields. Confirm only an ordinary, "
            "uncashed-out ticket with no special promotions. If any selection, event date, market, or rule "
            "is wrong, resubmit a clearer photo instead. Confirmation locks the ticket."
        )
        await interaction.followup.send(
            text, embed=review_embed(row), file=ticket_file(row, private=True), ephemeral=True,
            view=PrivateReviewView(self.vault, row) if row["status"] == "draft" and str(interaction.user.id) == row["owner_id"] else None,
        )

    @discord.ui.button(label="Moderator settle", custom_id="member-vault:settle", style=discord.ButtonStyle.secondary)
    async def settle(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not can_moderate(interaction.user):
            await interaction.response.send_message("Only moderators can settle member tickets.", ephemeral=True)
            return
        if interaction.channel_id != self.vault.channel_id or interaction.message is None:
            await interaction.response.send_message("This is not a member-vault card.", ephemeral=True)
            return
        row = await self.row_for_interaction(interaction)
        if row is None:
            return
        if row["status"] not in {"open", "review"} or not row.get("confirmed_at"):
            await interaction.followup.send("This ticket is not ready for settlement.", ephemeral=True)
            return
        await interaction.followup.send(
            "Review the ticket, then enter a result and evidence. This is labeled Moderator-settled, not API-verified.",
            embed=review_embed(row), file=ticket_file(row, private=True),
            view=ModeratorSettlementView(self.vault, row["id"]), ephemeral=True,
        )


class MemberBetVault:
    def __init__(self, bot, channel_id: int, service=None, membership=None):
        self.bot = bot
        self.channel_id = channel_id
        self.service = service or MemberBetService()
        self.membership = membership or MembershipService()
        self.lock = asyncio.Lock()
        self.ingest_slots = asyncio.Semaphore(2)
        self.view = VaultCardView(self)

    async def dm(self, user, text: str) -> None:
        try:
            await user.send(text, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            logger.warning("member_vault_dm_failed user=%s channel=%s", user.id, self.channel_id, exc_info=True)

    async def delete(self, message) -> bool:
        try:
            await message.delete()
            return True
        except discord.NotFound:
            return True
        except discord.HTTPException:
            logger.exception("member_vault_delete_failed message=%s channel=%s", message.id, self.channel_id)
            await self.dm(message.author, "I could not remove your original submission. It may still be visible. Please delete it and contact a moderator.")
            return False

    async def handle_message(self, message) -> None:
        if message.author.bot or message.webhook_id or not message.guild or message.channel.id != self.channel_id:
            return
        try:
            eligible = await asyncio.to_thread(self.membership.has_vault_access, message.author.id)
        except Exception:
            logger.exception("member_vault_membership_lookup_failed user=%s", message.author.id)
            await self.delete(message)
            await self.dm(message.author, "I could not verify your membership access. Your submission was not accepted. Please try again later or contact support.")
            return
        if not eligible:
            await self.delete(message)
            await self.dm(message.author, "Member Vault submissions require current verified paid access, an eligible seven-day trial, or an explicit owner grant. Your submission was not accepted. Link your Discord account in Whop and contact support if access is missing.")
            return
        photos = [attachment for attachment in message.attachments if accepted_photo(attachment)]
        if len(photos) != 1:
            deleted = await self.delete(message)
            await self.dm(message.author, (
                f"Your message in <#{self.channel_id}> was removed." if deleted else "Your message could not be removed."
            ) + " Attach exactly one static JPEG, PNG, or WebP bet-slip photo, up to 10 MB. Text-only messages, image links, GIFs, and PDFs are not accepted. Units can go in the caption.")
            return
        async with self.ingest_slots:
            try:
                photo = await photos[0].read()
                photo = await asyncio.to_thread(sanitize_photo, photo)
            except ValueError as exc:
                await self.delete(message)
                await self.dm(message.author, f"Your photo was rejected: {exc} Please resubmit a readable bet-slip photo.")
                return
            except discord.HTTPException:
                logger.exception("member_photo_download_failed message=%s", message.id)
                await self.delete(message)
                await self.dm(message.author, "I could not download your photo. The submission was not recorded. Please try again.")
                return
            try:
                async with self.lock:
                    if not await asyncio.to_thread(self.membership.has_vault_access, message.author.id):
                        await self.delete(message)
                        await self.dm(message.author, "Your membership access expired before the submission completed. This ticket was not accepted.")
                        return
                    row = await asyncio.to_thread(self.service.save_submission, message, photo)
                    if not await self.delete(message):
                        return
                    row = await asyncio.to_thread(self.service.update, row["id"], {"original_removed": True}) or row
                    await self.publish(row)
                await self.dm(message.author, f"Your slip was saved privately. Use Private review / confirm on Member Bet #{row['id']} in <#{self.channel_id}> once processing finishes.")
            except Exception:
                logger.exception("member_submission_failed message=%s", message.id)
                await self.dm(message.author, "Your submission could not be completed. Its original photo may still be visible; check the channel and contact a moderator before resubmitting.")

    async def channel(self):
        return self.bot.get_channel(self.channel_id) or await self.bot.fetch_channel(self.channel_id)

    async def publish(self, row: dict) -> None:
        channel = await self.channel()
        selection_text = "\n".join(leg["selection"] for leg in row["details"].get("legs", []))
        reveal = ticket_file(row) if row["status"] in SETTLED and len(selection_text) > 1024 else None
        card = None
        if row.get("card_message_id"):
            try:
                card = await channel.fetch_message(int(row["card_message_id"]))
            except discord.NotFound:
                logger.warning("member_card_missing bet=%s; recreating", row["id"])
        else:
            # Recover a send that succeeded just before a restart/database failure.
            async for candidate in channel.history(limit=None, after=discord.Object(id=int(row["source_message_id"]))):
                if candidate.author.id == self.bot.user.id and candidate.embeds and candidate.embeds[0].footer.text == card_embed(row).footer.text:
                    card = candidate
                    break
        if card is None:
            kwargs = {"file": reveal} if reveal else {}
            card = await channel.send(embed=card_embed(row), view=self.view if row["status"] not in SETTLED else None, allowed_mentions=discord.AllowedMentions.none(), **kwargs)
        else:
            await card.edit(embed=card_embed(row), attachments=[reveal] if reveal else [], view=self.view if row["status"] not in SETTLED else None, allowed_mentions=discord.AllowedMentions.none())
        await asyncio.to_thread(self.service.db.update, "member_bets", {
            "card_message_id": str(card.id), "card_dirty": False,
        }, {"id": row["id"], "updated_at": row["updated_at"]})

    async def original_removed(self, row: dict) -> bool:
        channel = await self.channel()
        try:
            message = await channel.fetch_message(int(row["source_message_id"]))
        except discord.NotFound:
            return True
        return await self.delete(message)

    @tasks.loop(minutes=1)
    async def reconcile(self):
        try:
            rows = await asyncio.to_thread(self.service.pending, self.channel_id)
            event_cache = {}
            for row in rows:
                try:
                    if row["status"] == "processing":
                        if not await self.original_removed(row):
                            await asyncio.to_thread(self.service.retry, row, "Original photo could not be deleted; moderator assistance required.")
                            continue
                        await asyncio.to_thread(self.service.update, row["id"], {"original_removed": True}, "processing")
                        await asyncio.to_thread(self.service.extract, row)
                    else:
                        event = row["event"]
                        key = (event["sport"], event["id"])
                        if key not in event_cache:
                            try:
                                event_cache[key] = await asyncio.to_thread(self.service.fetch_event, event)
                            except Exception as exc:
                                event_cache[key] = exc
                        response = event_cache[key]
                        if isinstance(response, Exception):
                            raise response
                        async with self.lock:
                            current = await asyncio.to_thread(self.service.get, row["id"])
                            if current["status"] == "open":
                                await asyncio.to_thread(self.service.check, current, response)
                except Exception:
                    logger.exception("member_vault_processing_failed bet=%s", row["id"])
                    await asyncio.to_thread(self.service.retry, row, "Processing/API verification failed after retries; moderator review required.")
            for row in await asyncio.to_thread(self.service.dirty, self.channel_id):
                try:
                    async with self.lock:
                        row = await asyncio.to_thread(self.service.get, row["id"])
                        await self.publish(row)
                except Exception:
                    logger.exception("member_card_sync_failed bet=%s", row["id"])
        except Exception:
            logger.exception("member_vault_reconciliation_failed channel=%s", self.channel_id)

    @reconcile.before_loop
    async def before_reconcile(self):
        await self.bot.wait_until_ready()
