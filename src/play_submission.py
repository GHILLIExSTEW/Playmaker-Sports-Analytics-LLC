from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable

import discord
from discord import app_commands
from discord.ext import commands

from src.administration import TestFlowView, build_image_test_embed
from src.config import IMAGE_INPUT_CHANNEL_ID, OFFICIAL_CHANNEL_ID, OFFICIAL_ROLE_IDS, TEST_CHANNEL_ID
from src.services.image_play_service import ImagePlayService, image_play_service
from src.services.official_play_service import OfficialPlayService
from src.services.play_service import PlayService

logger = logging.getLogger("official_play_bot")


def build_image_review_embed(parsed: dict) -> discord.Embed:
    legs = parsed["legs"]
    odds = PlayService.combine_american_odds([int(leg["odds"]) for leg in legs])
    embed = discord.Embed(title="Image play detected", color=discord.Color.orange())
    embed.add_field(name="Units", value=str(parsed.get("units") or "Not visible"), inline=True)
    embed.add_field(name="Legs", value=str(len(legs)), inline=True)
    embed.add_field(name="Combined odds", value=f"{odds:+d}", inline=True)
    embed.description = "\n".join(
        f"{index}. {leg['selection']} ({int(leg['odds']):+d})"
        for index, leg in enumerate(legs, start=1)
    )[:4096]
    return embed


class PlaySubmission(commands.Cog):
    def __init__(
        self,
        *,
        official: OfficialPlayService,
        get_testing: Callable[[], bool],
        is_tracked_operator: Callable[[discord.abc.User], bool],
        publish_play: Callable[..., Awaitable[discord.Message]],
        official_post_target: Callable[[], tuple[str, int | None]],
        confirm_recorded: Callable[[discord.Interaction, dict], Awaitable[None]],
        repost_play: Callable[..., Awaitable[discord.Message | None]],
        announce_play: Callable[..., Awaitable[None]],
        staff_alert: Callable[[str, str], Awaitable[None]],
        images: ImagePlayService | None = None,
    ) -> None:
        self.official = official
        self.get_testing = get_testing
        self.is_tracked_operator = is_tracked_operator
        self.publish_play = publish_play
        self.official_post_target = official_post_target
        self.confirm_recorded = confirm_recorded
        self.repost_play = repost_play
        self.announce_play = announce_play
        self.staff_alert = staff_alert
        self.images = images if images is not None else image_play_service

    async def handle_message(self, message: discord.Message) -> bool:
        """Return whether this message consumes routing; prefix commands stay in the bot."""
        if message.author.bot or not message.guild:
            return True
        if self.get_testing() and TEST_CHANNEL_ID and message.channel.id == TEST_CHANNEL_ID:
            image = next((item for item in message.attachments if (item.content_type or "").startswith("image/")), None)
            if image is None:
                return True
            try:
                parsed = await asyncio.to_thread(self.images.extract_play, image.url, message.content)
                await message.channel.send(
                    f"{message.author.mention}, test image parsed. Nothing will be recorded.",
                    embed=build_image_test_embed(parsed), view=TestFlowView(parsed),
                )
            except Exception as exc:
                logger.exception("testing_image_extract_failed message=%s", message.id)
                await message.channel.send(f"{message.author.mention}, test image failed: {exc}", delete_after=30)
            return True
        if IMAGE_INPUT_CHANNEL_ID and message.channel.id != IMAGE_INPUT_CHANNEL_ID:
            return False
        image = next((item for item in message.attachments if (item.content_type or "").startswith("image/")), None)
        if image is None or not self.is_tracked_operator(message.author):
            return False
        try:
            parsed = await asyncio.to_thread(self.images.extract_play, image.url, message.content)
            parsed["image_url"] = image.url
            await message.channel.send(
                "User Reviewing Bet",
                view=AutoImageView(self, message.author.id, parsed, message.id),
            )
        except Exception as exc:
            logger.exception("automatic_image_extract_failed message=%s", message.id)
            await message.channel.send(f"{message.author.mention}, I could not read that betting image: {exc}", delete_after=30)
            await self.staff_alert(
                "image_extract",
                f"Image reading failed for {message.author.mention}'s post {message.jump_url}\n`{str(exc)[:1500]}`",
            )
        return False

    async def record_modal_play(
        self, interaction: discord.Interaction, units: float, legs: int,
        odds_values: list[int], team_name: str, play_text: str, leg_records: list[dict],
    ) -> None:
        logger.info("play_modal_deferred interaction=%s", interaction.id)
        try:
            logger.info("play_db_start interaction=%s", interaction.id)
            payload = await asyncio.to_thread(
                self.official.create_play_record,
                discord_user_id=str(interaction.user.id), username=interaction.user.display_name,
                units=units, legs=legs, odds=PlayService.combine_american_odds(odds_values),
                team_name=team_name, play_text=play_text, leg_records=leg_records,
            )
            logger.info("play_db_complete interaction=%s play_id=%s error=%s", interaction.id, payload.get("play_id"), bool(payload.get("error")))
        except Exception as exc:
            logger.exception("play_db_failed interaction=%s", interaction.id)
            await interaction.followup.send(f"Could not record the play: {exc}", ephemeral=True)
            return
        if payload.get("error"):
            logger.warning("play_validation_failed interaction=%s error=%s", interaction.id, payload["error"])
            await interaction.followup.send(payload["error"], ephemeral=True)
            return
        try:
            message = await self.publish_play(interaction, payload, self.official_post_target())
        except Exception as exc:
            logger.exception("play_publish_failed interaction=%s", interaction.id)
            await interaction.followup.send(f"Could not publish the play: {exc}", ephemeral=True)
            return
        await asyncio.to_thread(self.official.attach_message_id, payload["play_id"], message.id)
        await self.confirm_recorded(interaction, payload)
        await self.announce_play(payload, interaction.user.display_name, message, message)
        logger.info("play_complete interaction=%s play_id=%s", interaction.id, payload["play_id"])

    async def import_image_command(self, interaction: discord.Interaction, image: discord.Attachment) -> None:
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a guild.", ephemeral=True)
            return
        if OFFICIAL_ROLE_IDS and not any(role.id in OFFICIAL_ROLE_IDS for role in getattr(interaction.user, "roles", [])):
            await interaction.response.send_message("You do not have permission to import plays.", ephemeral=True)
            return
        if OFFICIAL_CHANNEL_ID and interaction.channel_id != OFFICIAL_CHANNEL_ID:
            await interaction.response.send_message(f"Use this command in the official channel: <#{OFFICIAL_CHANNEL_ID}>", ephemeral=True)
            return
        if not image.content_type or not image.content_type.startswith("image/"):
            await interaction.response.send_message("Attach an image file.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            parsed = await asyncio.to_thread(self.images.extract_play, image.url)
            parsed["image_url"] = image.url
        except Exception as exc:
            logger.exception("image_play_extract_failed interaction=%s", interaction.id)
            await interaction.followup.send(f"Could not read that image: {exc}", ephemeral=True)
            return
        await interaction.followup.send(
            embed=build_image_review_embed(parsed), content="Review the detected play before recording it:",
            view=ConfirmImageView(self, interaction.user.id, parsed), ephemeral=True,
        )

    @app_commands.command(name="play", description="Record a play manually without an image")
    async def play_command(self, interaction: discord.Interaction) -> None:
        logger.info("play_command_received interaction=%s user=%s channel=%s", interaction.id, interaction.user.id, interaction.channel_id)
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a guild.", ephemeral=True)
            return
        if OFFICIAL_ROLE_IDS and not any(role.id in OFFICIAL_ROLE_IDS for role in getattr(interaction.user, "roles", [])):
            await interaction.response.send_message("You do not have permission to log official plays.", ephemeral=True)
            return
        if OFFICIAL_CHANNEL_ID and interaction.channel_id != OFFICIAL_CHANNEL_ID:
            await interaction.response.send_message(f"Use this command in the official channel: <#{OFFICIAL_CHANNEL_ID}>", ephemeral=True)
            return
        await interaction.response.send_modal(PlayModal(self))


class LegModal(discord.ui.Modal):
    leg_details = discord.ui.TextInput(
        label="Leg details", placeholder="Enter the pick or selection for this leg",
        style=discord.TextStyle.paragraph, required=True, max_length=1000,
    )
    leg_odds = discord.ui.TextInput(label="Leg odds", placeholder="Example: -110 or +150", required=True, max_length=20)

    def __init__(
        self, submission: PlaySubmission, draft_id: str, units: float, legs: int,
        odds_values: list[int], team_name: str, leg_number: int, collected: list[str], progress_message=None,
    ):
        super().__init__(title=f"Enter Leg {leg_number} of {legs}")
        self.submission = submission
        self.draft_id, self.units, self.legs = draft_id, units, legs
        self.odds_values, self.team_name = odds_values, team_name
        self.leg_number, self.collected, self.progress_message = leg_number, collected, progress_message

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            leg_odds = PlayService.normalize_odds(self.leg_odds.value)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        self.collected.append(f"Leg {self.leg_number}: {self.leg_details.value.strip()} ({leg_odds:+d})")
        self.odds_values.append(leg_odds)
        await interaction.response.defer()
        await asyncio.to_thread(
            self.submission.official.save_draft_leg, self.draft_id, str(interaction.user.id),
            self.units, self.legs, self.leg_number, self.leg_details.value.strip(), leg_odds, self.team_name,
        )
        if self.leg_number < self.legs:
            next_view = LegEntryView(
                self.submission, self.draft_id, self.units, self.legs, self.odds_values,
                self.team_name, self.leg_number + 1, self.collected,
            )
            content = f"Leg {self.leg_number} saved. Continue with leg {self.leg_number + 1}."
            if self.progress_message is not None:
                next_view.message = self.progress_message
                await self.progress_message.edit(content=content, view=next_view)
            else:
                await interaction.followup.send(content, ephemeral=True, view=next_view)
            return
        draft_legs = await asyncio.to_thread(self.submission.official.get_draft_legs, self.draft_id, str(interaction.user.id))
        combined = "\n".join(f"Leg {leg['leg_number']}: {leg['selection']} ({int(leg['odds']):+d})" for leg in draft_legs)
        await self.submission.record_modal_play(
            interaction, self.units, self.legs, [int(leg["odds"]) for leg in draft_legs], self.team_name, combined, draft_legs,
        )
        await asyncio.to_thread(self.submission.official.clear_draft_legs, self.draft_id, str(interaction.user.id))


class LegEntryView(discord.ui.View):
    def __init__(
        self, submission: PlaySubmission, draft_id: str, units: float, legs: int,
        odds_values: list[int], team_name: str, leg_number: int, collected: list[str],
    ):
        super().__init__(timeout=900)
        self.submission = submission
        self.draft_id, self.units, self.legs = draft_id, units, legs
        self.odds_values, self.team_name = odds_values, team_name
        self.leg_number, self.collected = leg_number, collected
        self.message = None

    @discord.ui.button(label="Enter next leg", style=discord.ButtonStyle.primary)
    async def enter_next_leg(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.message = interaction.message
        await interaction.response.send_modal(LegModal(
            self.submission, self.draft_id, self.units, self.legs, self.odds_values,
            self.team_name, self.leg_number, self.collected, self.message,
        ))


class PlayModal(discord.ui.Modal, title="Record Official Play"):
    units = discord.ui.TextInput(label="Units risked", placeholder="Example: 2", required=True, max_length=20)
    legs = discord.ui.TextInput(label="Number of legs", placeholder="Example: 3", required=True, max_length=10)
    leg_one = discord.ui.TextInput(label="Leg 1 selection", placeholder="Enter the first pick or selection", required=True, max_length=1000)
    leg_one_odds = discord.ui.TextInput(label="Leg 1 odds", placeholder="Example: -110 or +150", required=True, max_length=20)
    team_name = discord.ui.TextInput(label="Team (optional)", placeholder="Enter a team, including an untracked team", required=False, max_length=100)

    def __init__(self, submission: PlaySubmission):
        super().__init__()
        self.submission = submission

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            units, legs = float(self.units.value), int(self.legs.value)
        except ValueError:
            await interaction.response.send_message("Units must be a number and legs must be a whole number.", ephemeral=True)
            return
        if legs < 1 or legs > 10:
            await interaction.response.send_message("Legs must be between 1 and 10.", ephemeral=True)
            return
        try:
            first_odds = PlayService.normalize_odds(self.leg_one_odds.value)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        collected = [f"Leg 1: {self.leg_one.value.strip()}"]
        draft_id = str(uuid.uuid4())
        await interaction.response.defer()
        await asyncio.to_thread(
            self.submission.official.save_draft_leg, draft_id, str(interaction.user.id), units,
            legs, 1, self.leg_one.value.strip(), first_odds, self.team_name.value,
        )
        if legs > 1:
            await interaction.followup.send(
                "Start entering the legs one at a time.", ephemeral=True,
                view=LegEntryView(self.submission, draft_id, units, legs, [first_odds], self.team_name.value, 2, collected),
            )
            return
        draft_legs = await asyncio.to_thread(self.submission.official.get_draft_legs, draft_id, str(interaction.user.id))
        await self.submission.record_modal_play(
            interaction, units, legs, [first_odds], self.team_name.value, "\n".join(collected), draft_legs,
        )
        await asyncio.to_thread(self.submission.official.clear_draft_legs, draft_id, str(interaction.user.id))


class ConfirmImageView(discord.ui.View):
    def __init__(self, submission: PlaySubmission, owner_id: int, parsed: dict, source_message_id: int | None = None):
        super().__init__(timeout=900)
        self.submission, self.owner_id = submission, owner_id
        self.parsed, self.source_message_id = parsed, source_message_id
        self.recording = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("Only the original uploader can confirm or cancel this image.", ephemeral=True)
        return False

    @discord.ui.button(label="Confirm and record", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        if self.recording or self.is_finished():
            await interaction.followup.send("This image is already being recorded or its review has finished.", ephemeral=True)
            return
        self.recording = True
        try:
            official = self.submission.official
            if self.source_message_id and await asyncio.to_thread(official.get_play_for_message, self.source_message_id):
                await interaction.followup.send("This image has already been recorded.", ephemeral=True)
                return
            legs = self.parsed["legs"]
            payload = await asyncio.to_thread(
                official.create_play_record,
                discord_user_id=str(interaction.user.id), username=interaction.user.display_name,
                units=float(self.parsed["units"]), legs=len(legs),
                odds=PlayService.combine_american_odds([int(leg["odds"]) for leg in legs]),
                team_name=self.parsed.get("team_name") or "",
                play_text="\n".join(f"Leg {index}: {leg['selection']} ({int(leg['odds']):+d})" for index, leg in enumerate(legs, start=1)),
                leg_records=legs,
            )
            if payload.get("error"):
                await interaction.followup.send(payload["error"], ephemeral=True)
                return
            payload["image_url"] = self.parsed.get("image_url")
            message = await self.submission.publish_play(interaction, payload)
            repost = None
            if self.source_message_id and interaction.channel is not None:
                repost = await self.submission.repost_play(interaction.channel, self.source_message_id, payload, interaction.user)
            tracked_message_id = repost.id if repost else (self.source_message_id or message.id)
            await asyncio.to_thread(official.attach_message_id, payload["play_id"], tracked_message_id)
            await self.submission.confirm_recorded(interaction, payload)
            if interaction.message is not None:
                await interaction.message.delete()
            self.stop()
            if repost is None:
                tracked = message
                if self.source_message_id and interaction.channel is not None:
                    try:
                        tracked = await interaction.channel.fetch_message(self.source_message_id)
                    except discord.HTTPException:
                        tracked = message
                await self.submission.announce_play(payload, interaction.user.display_name, message, tracked)
        except Exception as exc:
            logger.exception("image_play_confirm_failed interaction=%s", interaction.id)
            await interaction.followup.send(f"Could not record the play: {exc}", ephemeral=True)
        finally:
            self.recording = False

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Image import cancelled.", embed=None, view=None)
        self.stop()


class AutoUnitsModal(discord.ui.Modal, title="Enter Units"):
    units = discord.ui.TextInput(label="Units risked", placeholder="Example: 2", required=True, max_length=20)

    def __init__(
        self, submission: PlaySubmission, owner_id: int, parsed: dict,
        source_message_id: int, review_message: discord.Message,
    ):
        super().__init__()
        self.submission, self.owner_id, self.parsed = submission, owner_id, parsed
        self.source_message_id, self.review_message = source_message_id, review_message

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the original uploader can continue.", ephemeral=True)
            return
        try:
            units = float(self.units.value)
            if units <= 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("Units must be greater than zero.", ephemeral=True)
            return
        self.parsed["units"] = units
        await interaction.response.defer()
        await interaction.followup.edit_message(
            self.review_message.id, content="User Reviewing Bet", embed=build_image_review_embed(self.parsed),
            view=ConfirmImageView(self.submission, self.owner_id, self.parsed, self.source_message_id),
        )


class AutoImageView(discord.ui.View):
    def __init__(self, submission: PlaySubmission, owner_id: int, parsed: dict, source_message_id: int):
        super().__init__(timeout=900)
        self.submission, self.owner_id, self.parsed = submission, owner_id, parsed
        self.source_message_id = source_message_id

    @discord.ui.button(label="Review image", style=discord.ButtonStyle.primary)
    async def review_image(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the original uploader can review this image.", ephemeral=True)
            return
        if self.parsed.get("units") is None:
            await interaction.response.send_modal(AutoUnitsModal(
                self.submission, self.owner_id, self.parsed, self.source_message_id, interaction.message,
            ))
        else:
            await interaction.response.edit_message(
                content="User Reviewing Bet", embed=build_image_review_embed(self.parsed),
                view=ConfirmImageView(self.submission, self.owner_id, self.parsed, self.source_message_id),
            )
