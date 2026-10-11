from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from src.config import CONFIRMATION_CHANNEL_ID, GUILD_ID, TRACKER_ROLE_ID
from src.services.api_budget_service import ApiBudgetDenied
from src.services.data_model_service import COMPETITIONS, DataModelService, ModelGame, project_matchup
from src.services.api_sports_multi_service import TRACKER_TIMEZONE
from src.services.website_capper_service import WEBSITE_OWNER_ROLE_ID

logger = logging.getLogger("official_play_bot")


def access_error(interaction: discord.Interaction, user_id: int | None = None) -> str | None:
    if not GUILD_ID or interaction.guild_id != GUILD_ID:
        return "Models are only available in the configured server."
    if not CONFIRMATION_CHANNEL_ID or interaction.channel_id != CONFIRMATION_CHANNEL_ID:
        return "Use /model in the Laboratory channel configured by CONFIRMATION_CHANNEL_ID."
    if user_id is not None and interaction.user.id != user_id:
        return "This model picker belongs to another operator. Start your own /model."
    if not isinstance(interaction.user, discord.Member) or not any(
        role.id in {TRACKER_ROLE_ID, WEBSITE_OWNER_ROLE_ID} for role in interaction.user.roles
    ):
        return "A current OPERATOR or Owner role is required to use data models."
    return None


def safe(value: str, limit: int = 200) -> str:
    return discord.utils.escape_markdown(discord.utils.escape_mentions(value))[:limit]


async def component_error(interaction: discord.Interaction, error: Exception) -> None:
    logger.error("data_model_component_failed user=%s", interaction.user.id, exc_info=(type(error), error, error.__traceback__))
    message = "Model request failed. Please retry /model or contact support."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


def model_embed(competition: str, game: ModelGame, games: list[ModelGame], requests_used: int, refreshed: datetime) -> discord.Embed:
    projection = project_matchup(games, game)
    embed = discord.Embed(title=f"{COMPETITIONS[competition][0]} - score-history model", color=0x9146BF)
    embed.description = (
        f"**{safe(game.away)} at {safe(game.home)}**\n"
        f"Game ID: `{game.id}` | Start: <t:{int(game.start.timestamp())}:F>\n"
        f"Provider refreshed: <t:{int(refreshed.timestamp())}:F> | {requests_used} API requests"
    )
    if projection is None:
        embed.add_field(
            name="No projection available",
            value="Requires at least 3 prior scored games per team, 30 league games, and nonzero score-margin variation. No probability was manufactured.",
            inline=False,
        )
    else:
        embed.add_field(name="Projected score", value=f"{safe(game.away)}: {projection.away_score:.1f}\n{safe(game.home)}: {projection.home_score:.1f}", inline=False)
        embed.add_field(name="Estimated win probability", value=f"{safe(game.home)}: {projection.home_probability:.1%}\n{safe(game.away)}: {1 - projection.home_probability:.1%}", inline=False)
        embed.add_field(name="Prior-game samples", value=f"Home: {projection.home_sample} | Away: {projection.away_sample} | League: {projection.league_sample}", inline=False)
        embed.add_field(name="History updated", value=f"<t:{int(projection.updated.timestamp())}:F>", inline=False)
    embed.add_field(
        name="Experimental score-only baseline",
        value="Up to 10 prior games/team; current and previous season history. Excludes games at/after the selected start. No injury, lineup, odds, weather or EPA adjustment. Not calibrated as a betting model; historical views are not point-in-time backtests.",
        inline=False,
    )
    embed.set_footer(text="Live API-Sports snapshot, not a live score stream. No guaranteed outcome.")
    return embed


class ModelView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=180)
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        error = access_error(interaction, self.user_id)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return False
        return True

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        await component_error(interaction, error)


class ModelModal(discord.ui.Modal):
    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await component_error(interaction, error)


class LeagueSelect(discord.ui.Select):
    def __init__(self, cog: DataModels, user_id: int):
        super().__init__(placeholder="Choose a model league", options=[
            discord.SelectOption(label=name, value=key) for key, (name, _, _) in COMPETITIONS.items()
        ])
        self.cog, self.user_id = cog, user_id

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(GameFilterModal(self.cog, self.user_id, self.values[0]))


class GameFilterModal(ModelModal, title="Find a league game"):
    def __init__(self, cog: DataModels, user_id: int, competition: str):
        super().__init__(timeout=180)
        self.cog, self.user_id, self.competition = cog, user_id, competition
        self.day = discord.ui.TextInput(label="Game date (YYYY-MM-DD, Eastern time)", default=datetime.now(TRACKER_TIMEZONE).date().isoformat(), max_length=10)
        self.team = discord.ui.TextInput(label="Team name filter (optional)", required=False, max_length=100)
        self.add_item(self.day)
        self.add_item(self.team)

    async def on_submit(self, interaction: discord.Interaction):
        error = access_error(interaction, self.user_id)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        try:
            day = date.fromisoformat(str(self.day))
            if day.isoformat() != str(self.day):
                raise ValueError()
        except ValueError:
            await interaction.response.send_message("Enter a valid game date as YYYY-MM-DD.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        if self.cog.refresh_lock.locked():
            await interaction.followup.send("Another live model refresh is running. Please retry shortly.", ephemeral=True)
            return
        try:
            async with self.cog.refresh_lock:
                games, used, refreshed = await asyncio.to_thread(self.cog.service.refresh, self.competition)
            choices = self.cog.service.choices(games, day, str(self.team).strip())
            if not choices:
                await interaction.followup.send(
                    f"Fresh {COMPETITIONS[self.competition][0]} data has no matching game on {day}. {used} API requests used. Retry /model with another date or team filter.",
                    ephemeral=True,
                )
                return
            view = GameView(self.user_id, self.competition, games, choices, used, refreshed)
            await interaction.followup.send(view.content(), view=view, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        except (ValueError, ApiBudgetDenied) as exc:
            await interaction.followup.send(str(exc), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            logger.exception("data_model_refresh_failed league=%s user=%s", self.competition, self.user_id)
            await interaction.followup.send("Live model refresh failed. No cached fallback was posted. Check bot logs or retry later.", ephemeral=True)


class GameSelect(discord.ui.Select):
    def __init__(self, picker: GameView):
        self.picker = picker
        super().__init__(placeholder="Choose the game to model", options=[
            discord.SelectOption(
                label=f"{game.away} at {game.home}"[:100], value=game.id,
                description=f"{game.start.astimezone(TRACKER_TIMEZONE):%b %d %I:%M %p} ET | {game.status} | ID {game.id}"[:100],
            ) for game in picker.choices[picker.page * 25:(picker.page + 1) * 25]
        ])

    async def callback(self, interaction: discord.Interaction):
        game = next(game for game in self.picker.choices if game.id == self.values[0])
        await interaction.response.send_modal(PublishModelModal(self.picker, game))


class GameView(ModelView):
    def __init__(self, user_id: int, competition: str, games: list[ModelGame], choices: list[ModelGame], used: int, refreshed: datetime):
        super().__init__(user_id)
        self.competition, self.games, self.choices = competition, games, choices
        self.used, self.refreshed, self.page = used, refreshed, 0
        self.published = False
        self.update_select()

    def content(self) -> str:
        return f"Fresh {COMPETITIONS[self.competition][0]} games - page {self.page + 1}/{(len(self.choices) + 24) // 25}. Select a game, then confirm posting in Laboratory. {self.used} API requests used."

    def update_select(self):
        for child in list(self.children):
            if isinstance(child, GameSelect):
                self.remove_item(child)
        self.add_item(GameSelect(self))
        self.previous.disabled = self.page == 0
        self.next_page.disabled = (self.page + 1) * 25 >= len(self.choices)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, row=1)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self.update_select()
        await interaction.response.edit_message(content=self.content(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=1)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min((len(self.choices) - 1) // 25, self.page + 1)
        self.update_select()
        await interaction.response.edit_message(content=self.content(), view=self)


class PublishModelModal(ModelModal, title="Post model in Laboratory"):
    def __init__(self, picker: GameView, game: ModelGame):
        super().__init__(timeout=180)
        self.picker, self.game = picker, game
        self.confirm = discord.ui.TextInput(label="Type POST to share the selected model", placeholder=f"{game.away} at {game.home}"[:100], max_length=4)
        self.add_item(self.confirm)

    async def on_submit(self, interaction: discord.Interaction):
        error = access_error(interaction, self.picker.user_id)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        if str(self.confirm).strip().upper() != "POST":
            await interaction.response.send_message("Nothing posted. Type POST to confirm the model.", ephemeral=True)
            return
        if self.picker.published or (datetime.now(timezone.utc) - self.picker.refreshed).total_seconds() > 180:
            await interaction.response.send_message("This snapshot is expired or already posted. Run /model for current data.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        self.picker.published = True
        try:
            if interaction.channel is None:
                raise RuntimeError("Laboratory channel is unavailable.")
            await interaction.channel.send(
                embed=model_embed(self.picker.competition, self.game, self.picker.games, self.picker.used, self.picker.refreshed),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            self.picker.stop()
            await interaction.followup.send("Model posted in Laboratory.", ephemeral=True)
        except Exception:
            logger.exception("data_model_publish_failed user=%s game=%s", interaction.user.id, self.game.id)
            await interaction.followup.send("Model could not be confirmed as posted. Check Laboratory before retrying /model.", ephemeral=True)


class DataModels(commands.Cog):
    def __init__(self, service: DataModelService | None = None):
        self.service = service or DataModelService()
        self.refresh_lock = asyncio.Lock()

    @app_commands.command(name="model", description="Laboratory: live league/game score-history model for OPERATOR or Owner")
    @app_commands.guild_only()
    async def model(self, interaction: discord.Interaction):
        error = access_error(interaction)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        view = ModelView(interaction.user.id)
        view.add_item(LeagueSelect(self, interaction.user.id))
        await interaction.response.send_message(
            "Choose a league, filter games by date/team, then select and confirm a model. Live refresh uses the shared API-Sports allowance.",
            view=view, ephemeral=True,
        )
