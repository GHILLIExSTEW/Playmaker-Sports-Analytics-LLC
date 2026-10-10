from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from src.config import CONFIRMATION_CHANNEL_ID
from src.services.official_play_service import OfficialPlayService
from src.services.play_features_service import PlayFeaturesService
from src.services.play_service import PlayService
from src.services.settlement_service import SettlementService

logger = logging.getLogger("official_play_bot")
SETTLEABLE_STATUSES = {"open", "regraded"}
PLAY_PICKER_PAGE_SIZE = 10
REGRADE_LOOKBACK = timedelta(days=2)
UNSETTLE_LOOKBACK = timedelta(days=7)
RESULT_COLORS = {
    "win": discord.Color.green(), "loss": discord.Color.red(),
    "void": discord.Color.dark_grey(), "partial": discord.Color.orange(),
}
SETTLE_RESULTS = (
    ("win", "Win", discord.ButtonStyle.success), ("loss", "Loss", discord.ButtonStyle.danger),
    ("void", "Void", discord.ButtonStyle.secondary), ("partial", "Partial", discord.ButtonStyle.primary),
)
PICKER_MODES = {
    "settle": ("Select an open play to settle", "Choose a play to settle", "There are no open plays to settle."),
    "regrade": ("Select a play from the last 2 days to regrade", "Choose a play to regrade", "There are no plays from the last 2 days to regrade."),
    "unsettle": ("Select a play settled in the last 7 days to reopen", "Choose a play to reopen", "There are no plays settled in the last 7 days."),
    "edit": ("Select an open play to edit", "Choose a play to edit", "There are no open plays to edit."),
}


def build_suggestion_view(play_id: int, result: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label=f"Confirm {result.upper()}", style=discord.ButtonStyle.success, custom_id=f"pm:as:{play_id}:{result}"))
    view.add_item(discord.ui.Button(label="Dismiss", style=discord.ButtonStyle.secondary, custom_id=f"pm:asx:{play_id}"))
    view.stop()
    return view


def build_suggestion_embed(play: dict, suggestion: dict) -> discord.Embed:
    result = suggestion["result"]
    embed = discord.Embed(
        title=f"🤖 Play #{play['id']} looks like a {result.upper()}",
        description="\n".join(suggestion["notes"])[:4000] or "All legs graded from final scores.",
        color=discord.Color.gold(),
    )
    embed.add_field(name="Play", value=OfficialPlayService.open_play_label(play), inline=False)
    embed.set_footer(text="Suggestion from final scores — confirm to settle, or dismiss and settle manually.")
    return embed


class PlaySettlement(commands.Cog):
    def __init__(
        self, bot: commands.Bot, *, official: OfficialPlayService, features: PlayFeaturesService,
        is_official: Callable[[discord.abc.User], bool],
        can_manage_plays: Callable[[discord.abc.User, discord.Guild | None], bool],
        fetch_message: Callable[[discord.Guild | None, int], Awaitable[discord.Message | None]],
        update_message: Callable[[discord.Message | None, int, str], Awaitable[None]],
        refresh_card: Callable[[discord.Guild | None, int], Awaitable[None]],
        resolve_channel: Callable[..., Awaitable],
        staff_alert: Callable[[str, str], Awaitable[None]],
        reaction_results: dict[str, str],
        channel_settings: Callable[[], list[tuple[str, int | None]]],
        user_can_settle: Callable[[discord.abc.User, str, discord.Guild | None], bool],
        bang_notifications: Callable[[discord.Message, int], Awaitable[None]],
    ) -> None:
        self.bot, self.official, self.features = bot, official, features
        self.is_official, self.can_manage_plays = is_official, can_manage_plays
        self.fetch_message, self.update_message, self.refresh_card = fetch_message, update_message, refresh_card
        self.resolve_channel, self.staff_alert = resolve_channel, staff_alert
        self.reaction_results, self.channel_settings = reaction_results, channel_settings
        self.user_can_settle, self.bang_notifications = user_can_settle, bang_notifications

    async def handle_reaction(self, payload: discord.RawReactionActionEvent) -> None:
        if self.bot.user is not None and payload.user_id == self.bot.user.id:
            return
        if payload.member is not None and getattr(payload.member, "bot", False):
            return
        result = self.reaction_results.get(str(payload.emoji))
        if result is None or payload.channel_id not in {channel_id for _, channel_id in self.channel_settings()}:
            return
        try:
            play = await asyncio.to_thread(self.official.get_play_for_message, payload.message_id)
            if not play or play.get("status") not in SETTLEABLE_STATUSES:
                return
            channel = self.bot.get_channel(payload.channel_id) or await self.bot.fetch_channel(payload.channel_id)
            reactor = payload.member or self.bot.get_user(payload.user_id) or await self.bot.fetch_user(payload.user_id)
            if not self.user_can_settle(reactor, str(play.get("discord_user_id")), getattr(channel, "guild", None)):
                return
            await asyncio.to_thread(self.official.settle_play, int(play["id"]), result)
            message = await channel.fetch_message(payload.message_id)
            await self.update_message(message, int(play["id"]), result)
            if result == "win":
                await self.bang_notifications(message, int(play["id"]))
        except Exception as exc:
            logger.exception("reaction_settlement_failed message=%s user=%s", payload.message_id, payload.user_id)
            await self.staff_alert("reaction_settlement", f"Reaction settlement failed for message {payload.message_id}: `{str(exc)[:1500]}`")

    async def reconcile_reactions(self, plays: list[dict], users: list[dict], channels: list) -> int:
        discord_user_ids = {str(user["id"]): str(user.get("discord_user_id")) for user in users}
        settled_count = 0
        for play in plays:
            if play.get("status") not in SETTLEABLE_STATUSES or not play.get("message_id"):
                continue
            owner_id = discord_user_ids.get(str(play["user_id"]))
            if not owner_id or owner_id == "None":
                continue
            message = None
            for channel in channels:
                try:
                    message = await channel.fetch_message(int(play["message_id"]))
                    break
                except (discord.NotFound, discord.Forbidden):
                    continue
            if message is None:
                continue
            result = None
            for reaction in message.reactions:
                reaction_result = self.reaction_results.get(str(reaction.emoji))
                if reaction_result is None:
                    continue
                async for user in reaction.users():
                    if self.user_can_settle(user, owner_id, message.guild):
                        result = reaction_result
                        break
                if result:
                    break
            if result:
                await asyncio.to_thread(self.official.settle_play, int(play["id"]), result)
                await self.update_message(message, int(play["id"]), result)
                if result == "win":
                    await self.bang_notifications(message, int(play["id"]))
                settled_count += 1
        return settled_count

    async def cog_load(self) -> None:
        if self.bot.is_ready():
            self.start_jobs()

    def cog_unload(self) -> None:
        self.auto_settle_suggestions.cancel()

    def start_jobs(self) -> None:
        if not self.auto_settle_suggestions.is_running():
            self.auto_settle_suggestions.start()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        self.start_jobs()

    def load_play_page(self, mode: str, page: int) -> tuple[list[dict], bool]:
        now = datetime.now(timezone.utc)
        if mode in {"settle", "edit"}:
            return self.official.list_plays(page, PLAY_PICKER_PAGE_SIZE, statuses=sorted(SETTLEABLE_STATUSES))
        if mode == "unsettle":
            return self.official.list_plays(
                page, PLAY_PICKER_PAGE_SIZE, statuses=["win", "loss", "void", "partial"],
                since=now - UNSETTLE_LOOKBACK, since_column="settled_at",
            )
        return self.official.list_plays(page, PLAY_PICKER_PAGE_SIZE, since=now - REGRADE_LOOKBACK)

    async def show_play_picker(self, interaction: discord.Interaction, mode: str, page: int, edit: bool = False) -> None:
        try:
            plays, has_more = await asyncio.to_thread(self.load_play_page, mode, page)
        except Exception as exc:
            logger.exception("play_picker_load_failed mode=%s page=%s", mode, page)
            if edit:
                await interaction.edit_original_response(content=f"Could not load plays: {exc}", view=None)
            else:
                await interaction.followup.send(f"Could not load plays: {exc}", ephemeral=True)
            return
        if not plays:
            if page > 0:
                await self.show_play_picker(interaction, mode, 0, edit=edit)
                return
            content = PICKER_MODES[mode][2]
            if edit:
                await interaction.edit_original_response(content=content, view=None)
            else:
                await interaction.followup.send(content, ephemeral=True)
            return
        content = f"{PICKER_MODES[mode][0]} (page {page + 1}):"
        view = PlayPickerView(self, interaction.user.id, mode, plays, page, has_more)
        if edit:
            await interaction.edit_original_response(content=content, view=view)
        else:
            await interaction.followup.send(content, view=view, ephemeral=True)

    async def open_picker(self, interaction: discord.Interaction, mode: str, denial: str) -> None:
        if not self.is_official(interaction.user):
            await interaction.response.send_message(denial, ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await self.show_play_picker(interaction, mode, 0)

    @app_commands.command(name="settle", description="Settle an official play from a list of open plays")
    async def settle_command(self, interaction: discord.Interaction) -> None:
        await self.open_picker(interaction, "settle", "Only officials can settle plays.")

    @app_commands.command(name="regrade", description="Regrade an official play from the last 2 days")
    async def regrade_command(self, interaction: discord.Interaction) -> None:
        await self.open_picker(interaction, "regrade", "Only officials can regrade plays.")

    @app_commands.command(name="unsettle", description="Reopen an official play settled in the last 7 days")
    async def unsettle_command(self, interaction: discord.Interaction) -> None:
        await self.open_picker(interaction, "unsettle", "Only officials can reopen plays.")

    @app_commands.command(name="edit_play", description="Edit the units, team, or legs of an open official play")
    async def edit_play_command(self, interaction: discord.Interaction) -> None:
        await self.open_picker(interaction, "edit", "Only officials can edit plays.")

    def edit_view(self, play_id: int, owner_id: int, payload: dict) -> EditBetView:
        return EditBetView(self, play_id, owner_id, payload)

    async def handle_suggestion_click(self, interaction: discord.Interaction, play_id: int, result: str | None) -> None:
        if not self.can_manage_plays(interaction.user, interaction.guild):
            await interaction.response.send_message("Only officials or moderators can act on suggestions.", ephemeral=True)
            return
        await interaction.response.defer()
        embed = interaction.message.embeds[0] if interaction.message is not None and interaction.message.embeds else discord.Embed()
        if result is None:
            embed.color = discord.Color.dark_grey()
            embed.set_footer(text=f"Dismissed by {interaction.user.display_name}")
            await interaction.edit_original_response(embed=embed, view=None)
            return
        current = await asyncio.to_thread(self.official._fetch_play, play_id)
        if current.get("status") not in SETTLEABLE_STATUSES:
            embed.set_footer(text=f"Already settled as {str(current.get('status')).upper()}")
            await interaction.edit_original_response(embed=embed, view=None)
            return
        await asyncio.to_thread(self.official.settle_play, play_id, result)
        message = await self.fetch_message(interaction.guild, int(current["message_id"])) if current.get("message_id") else None
        await self.update_message(message, play_id, result)
        embed.color = RESULT_COLORS.get(result, discord.Color.blurple())
        embed.set_footer(text=f"Settled as {result.upper()} by {interaction.user.display_name}")
        await interaction.edit_original_response(embed=embed, view=None)
        logger.info("auto_settle_confirmed play=%s result=%s user=%s", play_id, result, interaction.user.id)

    @commands.Cog.listener("on_interaction")
    async def on_suggestion_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type != discord.InteractionType.component:
            return
        custom_id = str((interaction.data or {}).get("custom_id") or "")
        if not custom_id.startswith(("pm:as:", "pm:asx:")):
            return
        try:
            parts = custom_id.split(":")
            result = None
            if parts[1] == "as":
                if len(parts) != 4 or parts[3] not in RESULT_COLORS:
                    raise ValueError("Invalid suggestion result.")
                result = parts[3]
            elif len(parts) != 3:
                raise ValueError("Invalid dismiss button.")
            if not parts[2].isdecimal() or int(parts[2]) <= 0:
                raise ValueError("Invalid suggestion play ID.")
            await self.handle_suggestion_click(interaction, int(parts[2]), result)
        except Exception:
            logger.exception("settlement_interaction_failed custom_id=%s user=%s", custom_id, interaction.user.id)
            try:
                if interaction.response.is_done():
                    await interaction.followup.send("Something went wrong. Please try again.", ephemeral=True)
                else:
                    await interaction.response.send_message("Something went wrong. Please try again.", ephemeral=True)
            except discord.HTTPException:
                logger.warning("settlement_error_response_failed user=%s", interaction.user.id, exc_info=True)

    @tasks.loop(minutes=15)
    async def auto_settle_suggestions(self) -> None:
        try:
            channel = await self.resolve_channel(CONFIRMATION_CHANNEL_ID, "CONFIRMATION_CHANNEL_ID")
            if channel is None:
                return
            candidates = await asyncio.to_thread(self.features.suggestion_candidates)
            for play in candidates:
                suggestion = await asyncio.to_thread(self.features.suggest, play)
                if suggestion is None:
                    continue
                await channel.send(
                    embed=build_suggestion_embed(play, suggestion),
                    view=build_suggestion_view(int(play["id"]), suggestion["result"]),
                )
                await asyncio.to_thread(self.features.mark_suggested, int(play["id"]))
                logger.info("auto_settle_suggested play=%s result=%s", play["id"], suggestion["result"])
        except Exception as exc:
            logger.exception("auto_settle_suggestions_failed")
            await self.staff_alert("auto_settle", f"Auto-settle suggestions failed: `{str(exc)[:1500]}`")

    @auto_settle_suggestions.before_loop
    async def before_auto_settle_suggestions(self) -> None:
        await self.bot.wait_until_ready()


class OwnerOnlyView(discord.ui.View):
    def __init__(self, settlement: PlaySettlement, owner_id: int):
        super().__init__(timeout=600)
        self.settlement, self.owner_id = settlement, owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This menu belongs to someone else.", ephemeral=True)
            return False
        if not self.settlement.is_official(interaction.user):
            await interaction.response.send_message("Your official role is required to use this menu.", ephemeral=True)
            return False
        return True


class PlayPickerView(OwnerOnlyView):
    def __init__(self, settlement: PlaySettlement, owner_id: int, mode: str, plays: list[dict], page: int, has_more: bool):
        super().__init__(settlement, owner_id)
        self.mode, self.page = mode, page
        self.plays = {str(play["id"]): play for play in plays}
        select = discord.ui.Select(
            placeholder=PICKER_MODES[mode][1],
            options=[discord.SelectOption(label=settlement.official.open_play_label(play), value=str(play["id"])) for play in plays],
        )
        select.callback = self.on_select
        self.select = select
        self.add_item(select)
        previous = discord.ui.Button(label="◀ Newer", style=discord.ButtonStyle.secondary, disabled=page == 0)
        previous.callback = lambda interaction: self.change_page(interaction, page - 1)
        self.add_item(previous)
        next_button = discord.ui.Button(label="Older ▶", style=discord.ButtonStyle.secondary, disabled=not has_more)
        next_button.callback = lambda interaction: self.change_page(interaction, page + 1)
        self.add_item(next_button)

    async def change_page(self, interaction: discord.Interaction, page: int) -> None:
        await interaction.response.defer()
        await self.settlement.show_play_picker(interaction, self.mode, max(0, page), edit=True)

    async def on_select(self, interaction: discord.Interaction) -> None:
        play = self.plays[self.select.values[0]]
        if self.mode == "regrade":
            await interaction.response.send_modal(RegradeModal(self.settlement, self.owner_id, play))
            return
        if self.mode == "edit":
            try:
                legs = await asyncio.to_thread(self.settlement.official.get_play_legs, int(play["id"]))
            except Exception as exc:
                logger.exception("edit_picker_legs_failed play=%s", play["id"])
                await interaction.response.send_message(f"Could not load play #{play['id']}: {exc}", ephemeral=True)
                return
            payload = {"units": play.get("units"), "team_name": play.get("team_name"), "leg_records": legs}
            await interaction.response.send_modal(EditBetModal(self.settlement, int(play["id"]), self.owner_id, payload, require_official=True))
            return
        if self.mode == "unsettle":
            await interaction.response.edit_message(
                content=f"Reopen **{self.settlement.official.open_play_label(play)}**? Its result will be removed from the tracker.",
                view=UnsettleConfirmView(self.settlement, self.owner_id, play, self.page),
            )
            return
        await interaction.response.edit_message(
            content=f"Settle **{self.settlement.official.open_play_label(play)}** as:",
            view=SettleResultView(self.settlement, self.owner_id, play, self.page),
        )


class UnsettleConfirmView(OwnerOnlyView):
    def __init__(self, settlement: PlaySettlement, owner_id: int, play: dict, page: int):
        super().__init__(settlement, owner_id)
        self.play, self.page = play, page

    @discord.ui.button(label="Reopen play", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        play_id = int(self.play["id"])
        try:
            outcome = await asyncio.to_thread(self.settlement.official.unsettle_play, play_id)
        except ValueError as exc:
            await interaction.edit_original_response(content=str(exc), view=None)
            return
        except Exception as exc:
            logger.exception("unsettle_failed play=%s user=%s", play_id, interaction.user.id)
            await interaction.edit_original_response(content=f"Could not reopen play #{play_id}: {exc}", view=None)
            return
        await self.settlement.refresh_card(interaction.guild, play_id)
        await interaction.edit_original_response(
            content=f"Play #{play_id} reopened (was **{outcome['previous'].upper()}**). Settle it again with /settle.", view=None,
        )
        logger.info("play_unsettled play=%s previous=%s user=%s", play_id, outcome["previous"], interaction.user.id)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        await self.settlement.show_play_picker(interaction, "unsettle", self.page, edit=True)


class SettleResultView(OwnerOnlyView):
    def __init__(self, settlement: PlaySettlement, owner_id: int, play: dict, page: int):
        super().__init__(settlement, owner_id)
        self.play, self.page = play, page
        for value, label, style in SETTLE_RESULTS:
            button = discord.ui.Button(label=label, style=style)
            button.callback = lambda interaction, result=value: self.settle(interaction, result)
            self.add_item(button)
        back = discord.ui.Button(label="Back", style=discord.ButtonStyle.secondary)
        back.callback = self.back
        self.add_item(back)

    async def back(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await self.settlement.show_play_picker(interaction, "settle", self.page, edit=True)

    async def settle(self, interaction: discord.Interaction, result: str) -> None:
        await interaction.response.defer()
        play_id = int(self.play["id"])
        try:
            current = await asyncio.to_thread(self.settlement.official._fetch_play, play_id)
            if current.get("status") not in SETTLEABLE_STATUSES:
                await interaction.edit_original_response(content=f"Play #{play_id} is already settled as **{current.get('status')}**.", view=None)
                return
            outcome = await asyncio.to_thread(self.settlement.official.settle_play, play_id, result)
            message = await self.settlement.fetch_message(interaction.guild, int(current["message_id"])) if current.get("message_id") else None
            await self.settlement.update_message(message, play_id, outcome["result"])
        except Exception as exc:
            logger.exception("settle_picker_failed play=%s user=%s", play_id, interaction.user.id)
            await interaction.edit_original_response(content=f"Could not settle play #{play_id}: {exc}", view=None)
            return
        await interaction.edit_original_response(content=f"Play #{play_id} settled as **{result.upper()}**.", view=None)


class RegradeModal(discord.ui.Modal):
    def __init__(self, settlement: PlaySettlement, owner_id: int, play: dict):
        super().__init__(title=f"Regrade Play #{play['id']}")
        self.settlement, self.owner_id, self.play = settlement, owner_id, play
        try:
            odds_default = f"{int(play.get('odds')):+d}"
        except (TypeError, ValueError):
            odds_default = ""
        self.legs_left = discord.ui.TextInput(label="Legs left", default=str(play.get("legs") or ""), max_length=3)
        self.odds = discord.ui.TextInput(label="New American odds", default=odds_default, max_length=8)
        self.note = discord.ui.TextInput(label="Note (optional)", style=discord.TextStyle.paragraph, required=False, max_length=500)
        for item in (self.legs_left, self.odds, self.note):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner_id or not self.settlement.is_official(interaction.user):
            await interaction.response.send_message("Only the requesting official can regrade this play.", ephemeral=True)
            return
        play_id = int(self.play["id"])
        try:
            values = SettlementService.validate_regrade(self.legs_left.value.strip(), self.odds.value.strip())
            outcome = await asyncio.to_thread(
                self.settlement.official.regrade_play, play_id,
                values["legs_left"], values["odds"], self.note.value.strip(),
            )
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        except Exception:
            logger.exception("regrade_failed play=%s user=%s", play_id, interaction.user.id)
            await interaction.response.send_message("Could not regrade the play. Check bot logs before retrying.", ephemeral=True)
            return
        await interaction.response.edit_message(
            content=f"Play #{play_id} regraded to {outcome['legs_left']}-leg at {outcome['odds']:+d}.", view=None,
        )
        await self.settlement.refresh_card(interaction.guild, play_id)


class EditBetModal(discord.ui.Modal, title="Edit Recorded Bet"):
    units = discord.ui.TextInput(label="Units", required=True, max_length=20)
    team = discord.ui.TextInput(label="Team", required=False, max_length=100)
    selections = discord.ui.TextInput(label="Selections, one per line", style=discord.TextStyle.paragraph, required=True, max_length=2000)
    odds = discord.ui.TextInput(label="Odds, one per line", style=discord.TextStyle.paragraph, required=True, max_length=500)
    notes = discord.ui.TextInput(label="Notes", style=discord.TextStyle.paragraph, required=False, max_length=1000)

    def __init__(self, settlement: PlaySettlement, play_id: int, owner_id: int, payload: dict, *, require_official: bool = False):
        super().__init__()
        self.settlement, self.play_id, self.owner_id, self.payload = settlement, play_id, owner_id, payload
        self.require_official = require_official
        self.units.default = str(payload.get("units", ""))
        self.team.default = payload.get("team_name") or ""
        legs = payload.get("leg_records") or []
        self.selections.default = "\n".join(leg["selection"] for leg in legs)
        self.odds.default = "\n".join(str(leg["odds"]) for leg in legs)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the original user can edit this bet.", ephemeral=True)
            return
        if self.require_official and not self.settlement.is_official(interaction.user):
            await interaction.response.send_message("Your official role is required to edit this play.", ephemeral=True)
            return
        selections = [line.strip() for line in self.selections.value.splitlines() if line.strip()]
        try:
            odds = [PlayService.normalize_odds(line) for line in self.odds.value.splitlines() if line.strip()]
            units = float(self.units.value)
            await asyncio.to_thread(
                self.settlement.official.edit_play_record, self.play_id, units, self.team.value, selections, odds, self.notes.value,
            )
        except (ValueError, TypeError) as exc:
            await interaction.response.send_message(f"Could not update bet: {exc}", ephemeral=True)
            return
        except Exception:
            logger.exception("edit_play_failed play=%s user=%s", self.play_id, interaction.user.id)
            await interaction.response.send_message("Could not update the bet. Check bot logs before retrying.", ephemeral=True)
            return
        await interaction.response.edit_message(content=f"Bet {self.play_id} updated.", embed=None, view=None)
        self.stop()
        await self.settlement.refresh_card(interaction.guild, self.play_id)


class EditBetView(discord.ui.View):
    def __init__(self, settlement: PlaySettlement, play_id: int, owner_id: int, payload: dict):
        super().__init__(timeout=900)
        self.settlement, self.play_id, self.owner_id, self.payload = settlement, play_id, owner_id, payload

    @discord.ui.button(label="Edit bet", style=discord.ButtonStyle.secondary)
    async def edit_bet(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the original user can edit this bet.", ephemeral=True)
            return
        await interaction.response.send_modal(EditBetModal(self.settlement, self.play_id, self.owner_id, self.payload))
