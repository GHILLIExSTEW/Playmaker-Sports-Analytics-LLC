from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import discord
from discord.ext import commands

from src.config import FREE_CHAT_CHANNEL_ID, GUILD_ID, HIGHROLLER_ROLE_ID, ROOKIE_ROLE_ID, TEST_CHANNEL_ID, VIP_CHAT_CHANNEL_ID
from src.play_settlement import SETTLEABLE_STATUSES
from src.services import bang_service
from src.services.official_play_service import OfficialPlayService
from src.services.play_features_service import PlayFeaturesService
from src.services.play_service import PlayService

logger = logging.getLogger("official_play_bot")


def build_play_embed(payload: dict) -> discord.Embed:
    embed = discord.Embed(title=f"Play #{payload['play_id']} • Open", description=payload["summary"], color=discord.Color.blurple())
    embed.add_field(name="Units", value=f"{float(payload['units']):g}u", inline=True)
    embed.add_field(name="Odds", value=f"{int(payload['odds']):+d}", inline=True)
    embed.add_field(name="To win", value=f"{float(payload['to_win']):g}u", inline=True)
    if payload.get("play_text"):
        embed.add_field(name="Selections", value=payload["play_text"][:1024], inline=False)
    return embed


def build_settled_play_embed(message: discord.Message, play_id: int, result: str) -> discord.Embed:
    embed = discord.Embed.from_dict(message.embeds[0].to_dict()) if message.embeds else discord.Embed(description=f"Bet result: **{result.upper()}**")
    for index in reversed(range(len(embed.fields))):
        field = embed.fields[index]
        if field.name.casefold() == "team":
            embed.remove_field(index)
        elif field.name.casefold() == "notes":
            embed.set_field_at(index, name="Selections", value=field.value, inline=field.inline)
    colors = {
        "win": discord.Color.green(), "loss": discord.Color.red(), "void": discord.Color.dark_grey(),
        "partial": discord.Color.orange(), "regraded": discord.Color.blurple(),
    }
    embed.title = f"Play #{play_id} • {result.title()}"
    embed.color = colors.get(result, discord.Color.blurple())
    return embed


def build_play_card_embed(message: discord.Message, play: dict) -> discord.Embed:
    embed = build_settled_play_embed(message, int(play["id"]), play["status"])
    units, odds = float(play["units"]), int(play["odds"])
    values = {
        "units": f"{units:g}u", "odds": f"{odds:+d}",
        "to win": f"{float(PlayService.calculate_to_win(units, odds)):g}u",
    }
    if play.get("play_text"):
        values["selections"] = str(play["play_text"])[:1024]
    for index, field in enumerate(embed.fields):
        key = field.name.casefold()
        if key in values:
            embed.set_field_at(index, name=field.name, value=values[key], inline=field.inline)
    return embed


def build_engagement_view(play_id: int, tails: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label=f"Tail ({tails})", emoji="🎯", style=discord.ButtonStyle.success, custom_id=f"pm:tail:{play_id}"))
    view.stop()
    return view


class PlayPresentation(commands.Cog):
    def __init__(
        self, bot: commands.Bot, *, official: OfficialPlayService, features: PlayFeaturesService,
        get_testing: Callable[[], bool], play_post_target: Callable[[], tuple[str, int | None]],
        resolve_channel: Callable[..., Awaitable], staff_alert: Callable[[str, str], Awaitable[None]],
    ) -> None:
        self.bot, self.official, self.features = bot, official, features
        self.get_testing, self.play_post_target = get_testing, play_post_target
        self.resolve_channel, self.staff_alert = resolve_channel, staff_alert

    async def publish_play_message(
        self, interaction: discord.Interaction, payload: dict,
        target: tuple[str, int | None] | None = None,
    ) -> discord.Message:
        setting_name, channel_id = target or self.play_post_target()
        channel = await self.resolve_channel(channel_id, setting_name, required=True)
        if channel is None:
            raise RuntimeError(f"{setting_name} is unavailable.")
        embed = build_play_embed(payload)
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        if payload.get("image_url"):
            embed.set_image(url=payload["image_url"])
        return await channel.send(embed=embed)

    async def update_play_message(self, message: discord.Message | None, play_id: int, result: str) -> None:
        if message is None:
            return
        try:
            await message.edit(embed=build_settled_play_embed(message, play_id, result))
        except discord.HTTPException:
            logger.warning("play_card_edit_failed play=%s message=%s", play_id, message.id, exc_info=True)
            await self.staff_alert(f"play_card:{play_id}", f"Play #{play_id} is recorded as {result}, but its card could not be updated.")

    async def fetch_guild_message(self, guild: discord.Guild | None, message_id: int) -> discord.Message | None:
        if guild is None:
            return None
        for channel in guild.text_channels:
            try:
                return await channel.fetch_message(message_id)
            except (discord.NotFound, discord.Forbidden):
                continue
        return None

    async def refresh_play_card(self, guild: discord.Guild | None, play_id: int) -> None:
        try:
            play = await asyncio.to_thread(self.official._fetch_play, int(play_id))
            if not play.get("message_id"):
                return
            message = await self.fetch_guild_message(guild, int(play["message_id"]))
            if message is None or self.bot.user is None or message.author.id != self.bot.user.id:
                return
            await message.edit(embed=build_play_card_embed(message, play))
        except Exception:
            logger.warning("play_card_refresh_failed play=%s", play_id, exc_info=True)
            await self.staff_alert(f"play_card:{play_id}", f"Could not refresh the card for Play #{play_id}. Check bot logs; its saved record may already be updated.")

    async def repost_official_play(self, channel, source_message_id: int, payload: dict, operator) -> discord.Message | None:
        file = None
        try:
            source = await channel.fetch_message(int(source_message_id))
            image = next((item for item in source.attachments if (item.content_type or "").startswith("image/")), None)
            embed = build_play_embed(payload)
            if source.content.strip():
                embed.description = source.content.strip()[:4000]
            embed.set_author(name=operator.display_name, icon_url=operator.display_avatar.url)
            kwargs = {}
            if image is not None:
                # Re-upload before deleting the source attachment.
                file = await image.to_file()
                embed.set_image(url=f"attachment://{file.filename}")
                kwargs["file"] = file
            repost = await channel.send(embed=embed, view=build_engagement_view(int(payload["play_id"]), 0), **kwargs)
        except Exception:
            logger.warning("official_repost_failed play=%s source=%s", payload.get("play_id"), source_message_id, exc_info=True)
            await self.staff_alert(
                f"official_repost:{payload.get('play_id')}",
                f"Could not repost Play #{payload.get('play_id')}. The original slip was not deleted; check bot logs.",
            )
            return None
        finally:
            if file is not None:
                file.close()
        try:
            await source.delete()
        except discord.HTTPException:
            logger.warning("official_source_delete_failed play=%s source=%s", payload.get("play_id"), source_message_id, exc_info=True)
            await self.staff_alert(
                f"official_delete:{payload.get('play_id')}",
                f"Play #{payload.get('play_id')} was reposted, but the original slip could not be deleted.",
            )
        return repost

    async def announce_new_play(self, payload: dict, capper_name: str, card: discord.Message, tracked: discord.Message | None) -> None:
        play_id = int(payload["play_id"])
        view = build_engagement_view(play_id, 0)
        tracked = tracked or card
        try:
            if tracked.id == card.id:
                await card.edit(view=view)
            else:
                await tracked.reply("Tailing this play? Tap below.", view=view, mention_author=False)
        except discord.HTTPException:
            logger.warning("engagement_bar_failed play=%s", play_id, exc_info=True)
            await self.staff_alert(f"engagement:{play_id}", f"Play #{play_id} was recorded, but its Tail button could not be attached.")

    async def handle_tail_click(self, interaction: discord.Interaction, play_id: int) -> None:
        await interaction.response.defer()
        play = await asyncio.to_thread(self.official._fetch_play, play_id)
        if play.get("status") not in SETTLEABLE_STATUSES:
            await interaction.followup.send(f"Play #{play_id} is already settled — tails are closed.", ephemeral=True)
            return
        tailing, count = await asyncio.to_thread(self.features.toggle_tail, play_id, interaction.user.id)
        if interaction.message is not None:
            view = discord.ui.View.from_message(interaction.message, timeout=None)
            for item in view.children:
                if isinstance(item, discord.ui.Button) and item.custom_id == f"pm:tail:{play_id}":
                    item.label = f"Tail ({count})"
            view.stop()
            await interaction.edit_original_response(view=view)
        message = (
            f"🎯 You're tailing Play #{play_id}. It counts toward your /mystats once settled."
            if tailing else f"Removed your tail on Play #{play_id}."
        )
        await interaction.followup.send(message, ephemeral=True)

    @commands.Cog.listener("on_interaction")
    async def on_play_feature_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type != discord.InteractionType.component:
            return
        custom_id = str((interaction.data or {}).get("custom_id") or "")
        if not custom_id.startswith(("pm:tail:", "pm:follow:")):
            return
        try:
            parts = custom_id.split(":")
            if parts[1] == "follow":
                await interaction.response.send_message("Follow alerts have been retired.", ephemeral=True)
            else:
                if len(parts) != 3 or not parts[2].isdecimal() or int(parts[2]) <= 0:
                    raise ValueError("Invalid Tail button.")
                await self.handle_tail_click(interaction, int(parts[2]))
        except Exception:
            logger.exception("play_feature_interaction_failed custom_id=%s user=%s", custom_id, interaction.user.id)
            try:
                if interaction.response.is_done():
                    await interaction.followup.send("Something went wrong. Please try again.", ephemeral=True)
                else:
                    await interaction.response.send_message("Something went wrong. Please try again.", ephemeral=True)
            except discord.HTTPException:
                logger.warning("play_feature_error_response_failed user=%s", interaction.user.id, exc_info=True)

    async def send_bang_notifications(self, source: discord.Message, play_id: int) -> None:
        if source.guild is None or source.guild.id != GUILD_ID:
            return
        targets = (
            [("TEST_CHANNEL_ID", TEST_CHANNEL_ID, None)]
            if self.get_testing() else [
                ("VIP_CHAT_CHANNEL_ID", VIP_CHAT_CHANNEL_ID, HIGHROLLER_ROLE_ID),
                ("FREE_CHAT_CHANNEL_ID", FREE_CHAT_CHANNEL_ID, ROOKIE_ROLE_ID),
            ]
        )
        image = next((attachment for attachment in source.attachments if (attachment.content_type or "").startswith("image/")), None)
        image_url = next((embed.image.url for embed in source.embeds if embed.image and embed.image.url), None)
        if image is None and image_url is None:
            logger.warning("bang_slip_missing play=%s message=%s", play_id, source.id)
            await self.staff_alert(f"bang_image:{play_id}", f"BANG for Play #{play_id} has no slip image. Text/link only.")
        for name, channel_id, role_id in targets:
            file = None
            try:
                channel = await self.resolve_channel(channel_id, name, required=True)
                if channel is None:
                    raise RuntimeError(f"{name} is unavailable.")
                embed = discord.Embed(title=f"💰 BANG! 💸 Play #{play_id} won 🤑", color=discord.Color.green())
                embed.set_footer(text="💵 Playmaker Picks • Official play WIN ✅")
                embed.description = f"[Original play]({source.jump_url})"
                if source.embeds and source.embeds[0].author.name:
                    embed.set_author(name=source.embeds[0].author.name, icon_url=source.embeds[0].author.icon_url)
                files = {}
                if image is not None:
                    file = await image.to_file()
                    files["file"] = file
                    embed.set_image(url=f"attachment://{file.filename}")
                elif image_url:
                    embed.set_image(url=image_url)
                else:
                    embed.description += "\nNo slip image is available for this play."
                if not await asyncio.to_thread(bang_service.claim, play_id, channel.id):
                    continue
                sent = await channel.send(
                    content=f"## 💰 BANG! 💸 🤑\n<@&{role_id}>" if role_id else "## 💰 BANG! 💸 🤑",
                    embed=embed, **files,
                    allowed_mentions=discord.AllowedMentions(
                        roles=[discord.Object(id=role_id)] if role_id else False, users=False, everyone=False,
                    ),
                )
                await asyncio.to_thread(bang_service.complete, play_id, channel.id, sent.id)
            except Exception:
                logger.exception("bang_notification_failed play=%s channel=%s", play_id, channel_id)
                await self.staff_alert(
                    f"bang:{play_id}:{channel_id}",
                    f"BANG delivery/tracking failed for Play #{play_id} in {name}. "
                    "Inspect logs, the destination and play_bang_notifications before retrying; a claim may remain.",
                )
            finally:
                if file is not None:
                    file.close()
