from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from src.config import CONFIRMATION_CHANNEL_ID, GUILD_ID, TRACKER_ROLE_ID
from src.services.api_budget_service import ApiBudgetDenied
from src.services.data_model_service import COMPETITIONS, DataModelService, ModelGame
from src.services.api_sports_multi_service import TRACKER_TIMEZONE
from src.services.model_card_image_service import render_model_card
from src.services import model_presentation as text
from src.services.model_engine import ModelProjection
from src.services.model_projection_service import ModelProjectionService, ModelRun, model_projection_service
from src.services.website_capper_service import WEBSITE_OWNER_ROLE_ID

logger = logging.getLogger("official_play_bot")
DISCORD_FILE_LIMIT = 10


def access_error(interaction: discord.Interaction, user_id: int | None = None) -> str | None:
    if not GUILD_ID or interaction.guild_id != GUILD_ID:
        return "Models are only available in the configured server."
    if not CONFIRMATION_CHANNEL_ID or interaction.channel_id != CONFIRMATION_CHANNEL_ID:
        return "Use model commands in the Laboratory channel configured by CONFIRMATION_CHANNEL_ID."
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


def model_embed(competition: str, game: ModelGame, projection: ModelProjection | None, backtest: dict | None,
                requests_used: int, refreshed: datetime) -> discord.Embed:
    embed = discord.Embed(title=f"{COMPETITIONS[competition][0]} - Playmaker ratings model", color=0x9146BF)
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
        embed.add_field(name="Projected score", value=f"{safe(game.away)}: {text.one_decimal(projection.away_score)}\n{safe(game.home)}: {text.one_decimal(projection.home_score)}\n{safe(text.summary_line(game, projection))}", inline=False)
        embed.add_field(name="Estimated win probability", value=f"{safe(game.home)}: {text.percent(projection.home_probability)}\n{safe(game.away)}: {text.percent(1 - projection.home_probability)}", inline=False)
        embed.add_field(name="Market comparison", value=safe("\n".join(text.market_lines(game, projection)), 1024), inline=False)
        details = [text.factor_line(game, projection), text.conditions_line(game, projection)]
        embed.add_field(name="Inputs", value=safe("\n".join(value for value in details if value), 1024), inline=False)
        embed.add_field(name="Prior-game samples", value=safe(text.samples_line(game, projection)), inline=False)
        embed.add_field(name="History updated", value=f"<t:{int(projection.updated.timestamp())}:F>", inline=False)
    embed.add_field(name="Track record", value=safe(text.track_record_line(backtest), 1024), inline=False)
    embed.add_field(name="Method", value=text.METHOD, inline=False)
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
                run = await asyncio.to_thread(self.cog.projections.project, self.competition, games)
            choices = self.cog.service.choices(games, day, str(self.team).strip())
            if not choices:
                await interaction.followup.send(
                    f"Fresh {COMPETITIONS[self.competition][0]} data has no matching game on {day}. {used} API requests used. Retry /model with another date or team filter.",
                    ephemeral=True,
                )
                return
            view = GameView(self.user_id, self.competition, run, choices, used, refreshed)
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
    def __init__(self, user_id: int, competition: str, run: ModelRun, choices: list[ModelGame], used: int, refreshed: datetime):
        super().__init__(user_id)
        self.competition, self.run, self.choices = competition, run, choices
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
                embed=model_embed(self.picker.competition, self.game, self.picker.run.projections.get(self.game.id),
                                  self.picker.run.backtest, self.picker.used, self.picker.refreshed),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            self.picker.stop()
            await interaction.followup.send("Model posted in Laboratory.", ephemeral=True)
        except Exception:
            logger.exception("data_model_publish_failed user=%s game=%s", interaction.user.id, self.game.id)
            await interaction.followup.send("Model could not be confirmed as posted. Check Laboratory before retrying /model.", ephemeral=True)


class DataModels(commands.Cog):
    def __init__(self, service: DataModelService | None = None, projections: ModelProjectionService | None = None):
        self.service = service or DataModelService()
        self.projections = projections or model_projection_service
        self.refresh_lock = asyncio.Lock()

    @app_commands.command(name="model", description="Laboratory: live league/game ratings model for OPERATOR or Owner")
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

    @app_commands.command(name="league_model", description="Laboratory: post model cards for every game in a league on a date")
    @app_commands.guild_only()
    @app_commands.describe(league="League to model", day="Game date (Eastern). Pick a suggestion or type YYYY-MM-DD")
    @app_commands.rename(day="date")
    @app_commands.choices(league=[app_commands.Choice(name=name, value=key) for key, (name, _, _) in COMPETITIONS.items()])
    async def league_model(self, interaction: discord.Interaction, league: app_commands.Choice[str], day: str):
        error = access_error(interaction)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        try:
            selected = date.fromisoformat(day.strip())
            if selected.isoformat() != day.strip():
                raise ValueError()
        except ValueError:
            await interaction.response.send_message("Choose a date from the list or enter it as YYYY-MM-DD.", ephemeral=True)
            return
        if league.value not in COMPETITIONS:
            await interaction.response.send_message("Select a supported model league.", ephemeral=True)
            return
        name = COMPETITIONS[league.value][0]
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            run = await asyncio.to_thread(self.projections.project, league.value)
            matchups = sorted(self.service.choices(run.games, selected, ""), key=lambda game: (game.start, game.away))
            if not matchups:
                await interaction.followup.send(
                    f"No cached {name} games are scheduled on {selected:%a, %b} {selected.day}, {selected.year}. Nothing was posted.",
                    ephemeral=True,
                )
                return
            cards = await asyncio.to_thread(
                lambda: [render_model_card(name, game, run.projections.get(game.id), run.backtest) for game in matchups]
            )
            if interaction.channel is None:
                raise RuntimeError("Laboratory channel is unavailable.")
            heading = f"**{safe(name)} ratings models · {selected:%a, %b} {selected.day}, {selected.year} (ET)** · {len(matchups)} games"
            for offset in range(0, len(cards), DISCORD_FILE_LIMIT):
                files = [
                    discord.File(card, filename=f"{league.value.replace(':', '-')}-{selected}-{offset + index + 1:02d}.png")
                    for index, card in enumerate(cards[offset:offset + DISCORD_FILE_LIMIT])
                ]
                await interaction.channel.send(
                    content=heading if offset == 0 else None, files=files,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            await interaction.followup.send(f"Posted {len(cards)} {name} model cards.", ephemeral=True)
        except Exception:
            logger.exception("league_model_failed league=%s day=%s user=%s", league.value, selected, interaction.user.id)
            await interaction.followup.send("League models could not be posted. Check Laboratory before retrying.", ephemeral=True)

    @league_model.autocomplete("day")
    async def league_model_day(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        today = datetime.now(TRACKER_TIMEZONE).date()
        labels = {-1: "Yesterday", 0: "Today", 1: "Tomorrow"}
        options = []
        for offset in range(-1, 14):
            value = today + timedelta(days=offset)
            label = f"{labels[offset]} · " if offset in labels else ""
            options.append(app_commands.Choice(name=f"{label}{value:%a, %b} {value.day}, {value.year}", value=value.isoformat()))
        text = current.strip().casefold()
        return [option for option in options if not text or text in option.name.casefold() or text in option.value][:25]
