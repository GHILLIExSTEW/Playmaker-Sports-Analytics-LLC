from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import date

import discord
from discord import app_commands
from discord.ext import commands

from src.config import GUILD_ID, OFFICIAL_ROLE_IDS, OPERATOR_ROLE_IDS
from src.services.diagnostic_service import DiagnosticService, diagnostic_service
from src.services.image_play_service import ImagePlayService, image_play_service
from src.services.play_service import PlayService
from src.services.team_api_service import TEAM_SPORTS, TeamApiService, TeamRefreshFailed, team_api_service
from src.services.website_capper_service import WEBSITE_OWNER_ROLE_ID

logger = logging.getLogger("official_play_bot")


def can_refresh_api(interaction: discord.Interaction) -> bool:
    return bool(
        interaction.guild and interaction.guild.id == GUILD_ID
        and not getattr(interaction.user, "bot", False)
        and any(role.id == WEBSITE_OWNER_ROLE_ID for role in getattr(interaction.user, "roles", []))
    )


def can_change_settings(interaction: discord.Interaction) -> bool:
    is_operator = any(role.id in OPERATOR_ROLE_IDS for role in getattr(interaction.user, "roles", []))
    is_manager = bool(interaction.guild and interaction.user.guild_permissions.manage_guild)
    return is_operator or is_manager


def build_image_test_embed(parsed: dict) -> discord.Embed:
    legs = parsed["legs"]
    odds = PlayService.combine_american_odds([int(leg["odds"]) for leg in legs])
    embed = discord.Embed(
        title="Image Test Result",
        description="This image was parsed successfully. Nothing was recorded.",
        color=discord.Color.blue(),
    )
    embed.add_field(name="Units", value=str(parsed.get("units") or "Not visible"), inline=True)
    embed.add_field(name="Legs", value=str(len(legs)), inline=True)
    embed.add_field(name="Combined odds", value=f"{odds:+d}", inline=True)
    embed.add_field(
        name="Selections",
        value="\n".join(f"{index}. {leg['selection']} ({int(leg['odds']):+d})" for index, leg in enumerate(legs, start=1))[:1024],
        inline=False,
    )
    return embed


class TestUnitsModal(discord.ui.Modal, title="Enter Units for Image Test"):
    units = discord.ui.TextInput(label="Units", placeholder="Example: 2", required=True, max_length=20)

    def __init__(self, parsed: dict):
        super().__init__()
        self.parsed = parsed

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            units = float(self.units.value)
            if units <= 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("Units must be a number greater than zero.", ephemeral=True)
            return
        self.parsed["units"] = units
        await interaction.response.edit_message(embed=build_image_test_embed(self.parsed), view=TestFlowView(self.parsed))


class TestFlowView(discord.ui.View):
    def __init__(self, parsed: dict):
        super().__init__(timeout=900)
        self.parsed = parsed

    @discord.ui.button(label="Enter units", style=discord.ButtonStyle.primary)
    async def enter_units(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.parsed.get("units") is not None:
            await interaction.response.send_message("Units are already available.", ephemeral=True)
            return
        await interaction.response.send_modal(TestUnitsModal(self.parsed))

    @discord.ui.button(label="Complete test", style=discord.ButtonStyle.success)
    async def complete_test(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.parsed.get("units") is None:
            await interaction.response.send_message("Enter units before completing the test.", ephemeral=True)
            return
        await interaction.response.edit_message(content="Test complete. Nothing was recorded.", embed=None, view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_test(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Test cancelled.", embed=None, view=None)


class TestImageView(discord.ui.View):
    def __init__(self, parsed: dict):
        super().__init__(timeout=900)
        self.parsed = parsed
        if parsed.get("units") is not None:
            self.clear_items()

    @discord.ui.button(label="Enter units", style=discord.ButtonStyle.primary)
    async def enter_units(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(TestUnitsModal(self.parsed))


class ApiDirectorySelect(discord.ui.Select):
    def __init__(self, picker: ApiPickerView):
        self.picker = picker
        start = picker.page * 25
        super().__init__(
            placeholder="Choose a league" if picker.league is None else "Choose a team",
            options=[discord.SelectOption(label=name[:100], value=value) for name, value in picker.rows[start:start + 25]],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        picker = self.picker
        selected = self.values[0]
        if selected not in {value for _, value in picker.rows}:
            await interaction.response.send_message("This selection is no longer valid. Run /api again.", ephemeral=True)
            return
        if picker.league is None:
            await interaction.response.defer()
            try:
                rows = await asyncio.to_thread(
                    picker.administration.api_service.picker_directory, picker.sport, picker.season, selected,
                )
                if not rows:
                    await interaction.edit_original_response(content="No teams were supplied for this league/season. Run /api again with another season.", view=None)
                else:
                    await interaction.edit_original_response(
                        content=f"Choose a team for {TEAM_SPORTS[picker.sport]}, season {picker.season}. Picker expires after 3 minutes; run /api again if needed.",
                        view=ApiPickerView(picker.administration, picker.user_id, picker.sport, picker.season, rows, selected),
                    )
            except Exception:
                logger.exception("owner_api_team_picker_failed user=%s league=%s", interaction.user.id, selected)
                await interaction.edit_original_response(content="Team list failed to load. Check provider coverage, quota and bot logs; run /api to retry.", view=None)
            picker.stop()
        else:
            await interaction.response.edit_message(content="Refreshing selected team; this can take several minutes...", view=None)
            picker.stop()
            await picker.administration.run_api_refresh(interaction, picker.sport, picker.league, selected, picker.season)


class ApiPickerView(discord.ui.View):
    def __init__(self, administration: Administration, user_id, sport, season, rows, league=None):
        super().__init__(timeout=180)
        self.administration = administration
        self.user_id, self.sport, self.season = user_id, sport, season
        self.rows, self.league, self.page = rows, league, 0
        self.render_page()

    def render_page(self):
        self.clear_items()
        self.add_item(ApiDirectorySelect(self))
        self.previous.disabled = self.page == 0
        self.next.disabled = (self.page + 1) * 25 >= len(self.rows)
        self.add_item(self.previous)
        self.add_item(self.next)

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id == self.user_id and can_refresh_api(interaction):
            return True
        await interaction.response.send_message("Only the requesting Owner can use this picker. Run /api for your own list.", ephemeral=True)
        return False

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item):
        logger.error("owner_api_picker_failed", exc_info=(type(error), error, error.__traceback__))
        message = "The picker failed. Run /api again; check bot logs if it repeats."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self.render_page()
        await interaction.response.edit_message(content=f"Choose {'league' if self.league is None else 'team'} — page {self.page + 1} of {(len(self.rows) + 24) // 25}.", view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min((len(self.rows) - 1) // 25, self.page + 1)
        self.render_page()
        await interaction.response.edit_message(content=f"Choose {'league' if self.league is None else 'team'} — page {self.page + 1} of {(len(self.rows) + 24) // 25}.", view=self)


class Administration(commands.Cog):
    def __init__(
        self,
        *,
        set_testing: Callable[[bool], None],
        get_tracker_start: Callable[[], date | None],
        set_tracker_start: Callable[[date | None], None],
        api_service: TeamApiService | None = None,
        image_service: ImagePlayService | None = None,
        diagnostics: DiagnosticService | None = None,
    ) -> None:
        self.set_testing = set_testing
        self.get_tracker_start = get_tracker_start
        self.set_tracker_start = set_tracker_start
        self.api_service = api_service if api_service is not None else team_api_service
        self.image_service = image_service if image_service is not None else image_play_service
        self.diagnostics = diagnostics if diagnostics is not None else diagnostic_service

    @app_commands.command(name="api", description="Owner: choose a league and team by name to refresh the season cache")
    @app_commands.guild_only()
    @app_commands.describe(
        sport="Choose the sport; league and team dropdowns appear next",
        season="Provider season, e.g. 2026 or 2026-2027",
    )
    @app_commands.choices(sport=[
        app_commands.Choice(name=name, value=slug) for slug, name in TEAM_SPORTS.items()
    ])
    async def api_command(self, interaction: discord.Interaction, sport: str, season: str):
        if not can_refresh_api(interaction):
            await interaction.response.send_message("Only the Discord Owner role in this server can refresh API caches.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            rows = await asyncio.to_thread(self.api_service.picker_directory, sport, season)
            if not rows:
                await interaction.followup.send("No leagues were supplied for this sport. Check provider subscription coverage.", ephemeral=True)
                return
            await interaction.followup.send(
                f"Choose a league for {TEAM_SPORTS[sport]}, season {season}. Lists use the shared API budget. Picker expires after 3 minutes; run /api again if needed.",
                view=ApiPickerView(self, interaction.user.id, sport, season, rows), ephemeral=True,
            )
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
        except Exception:
            logger.exception("owner_api_league_picker_failed user=%s sport=%s", interaction.user.id, sport)
            await interaction.followup.send("League list failed to load. Check API quota, subscription and bot logs; run /api to retry.", ephemeral=True)

    async def run_api_refresh(self, interaction: discord.Interaction, sport: str, league: str, team: str, season: str):
        if not can_refresh_api(interaction):
            await interaction.followup.send("Your Owner role is required to refresh API caches.", ephemeral=True)
            return
        try:
            report = await asyncio.to_thread(self.api_service.refresh, sport, league, team, season, interaction.user.id)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except TeamRefreshFailed as exc:
            report = exc.report
            logger.exception("owner_api_refresh_partial user=%s sport=%s league=%s team=%s season=%s report=%s",
                             interaction.user.id, sport, league, team, season, report)
        except Exception:
            logger.exception("owner_api_refresh_failed user=%s", interaction.user.id)
            await interaction.followup.send("API refresh failed. Check bot logs and database migrations; no complete refresh is claimed.", ephemeral=True)
            return
        state = "Completed available-data refresh" if report["complete"] else "PARTIAL / FAILED refresh"
        coverage = (
            f"Player game snapshots: {report['player_games']} ({report['empty_player_games']} had no supplied player records)."
            if report["player_supported"] else "Player stats are unsupported for this sport's current adapter; no player refresh was performed."
        )
        message = (
            f"**{state}**\nGames cached: {report['games']}; team/schedule snapshots: {report['snapshots']}.\n"
            f"{coverage}\nProvider requests attempted: {report['requests']}. "
            "All returned fields are cached; missing statistics are not zero. "
            "Only started, non-canceled games receive per-game stat requests. No season player totals are invented."
        )
        if report.get("empty_team_summary"):
            message += "\nNo season team summary was supplied by the provider; this is unavailable data, not zero."
        if report.get("season_summary_supported") is False:
            message += "\nNFL/NCAA provider season team summaries are unavailable. Team/player statistics are cached per game, not invented season totals."
        if report["complete"] and not report["games"]:
            message += "\nNo games were supplied for this scope; no per-game statistics were refreshed."
        if not report["complete"]:
            message += "\nAlready saved components remain cached. Check bot logs for the failed endpoint, provider quota or database error. Retry does not resume automatically and consumes quota again."
            if report.get("quota_denied"):
                message += "\nThe shared API request budget denied another request. No quota bypass was attempted."
        else:
            logger.info("owner_api_refresh_complete user=%s sport=%s league=%s team=%s season=%s report=%s",
                        interaction.user.id, sport, league, team, season, report)
        await interaction.followup.send(message, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="test", description="Test an image or run play-system diagnostics")
    @app_commands.describe(image="Optional betting-slip image to parse without recording")
    async def test_command(self, interaction: discord.Interaction, image: discord.Attachment | None = None):
        if OFFICIAL_ROLE_IDS and not any(role.id in OFFICIAL_ROLE_IDS for role in getattr(interaction.user, "roles", [])):
            await interaction.response.send_message("Only officials can run diagnostics.", ephemeral=True)
            return
        if image is not None:
            if not image.content_type or not image.content_type.startswith("image/"):
                await interaction.response.send_message("Attach an image file.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            try:
                parsed = await asyncio.to_thread(self.image_service.extract_play, image.url)
                await interaction.followup.send(embed=build_image_test_embed(parsed), view=TestImageView(parsed), ephemeral=True)
            except Exception as exc:
                logger.exception("test_image_failed interaction=%s", interaction.id)
                await interaction.followup.send(f"Image test failed: {exc}", ephemeral=True)
            return
        try:
            checks = self.diagnostics.run_checks()
            passed = sum(check["passed"] for check in checks)
            embed = discord.Embed(
                title="Play System Diagnostics",
                description=f"{passed}/{len(checks)} checks passed. No database records were changed.",
                color=discord.Color.green() if passed == len(checks) else discord.Color.red(),
            )
            for check in checks:
                marker = "PASS" if check["passed"] else "FAIL"
                embed.add_field(name=f"{marker} • {check['name']}", value=check["detail"][:1024], inline=False)
        except Exception:
            logger.exception("diagnostics_failed interaction=%s", interaction.id)
            await interaction.response.send_message("Diagnostics failed. Check bot logs before retrying.", ephemeral=True)
            return
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="testing", description="Enable or disable the image test channel")
    @app_commands.describe(enabled="True enables test mode; false disables it")
    async def testing_command(self, interaction: discord.Interaction, enabled: bool):
        if not can_change_settings(interaction):
            await interaction.response.send_message("Only operators or server managers can change testing mode.", ephemeral=True)
            return
        self.set_testing(enabled)
        state = "enabled" if enabled else "disabled"
        await interaction.response.send_message(f"Image testing is now **{state}**.", ephemeral=True)
        logger.info("testing_mode_set enabled=%s user=%s", enabled, interaction.user.id)

    @app_commands.command(name="tracker_start", description="Only count plays settled on or after a date")
    @app_commands.describe(date_value="YYYY-MM-DD, or 'clear' to count every play, or 'show' to view the current setting")
    async def tracker_start_command(self, interaction: discord.Interaction, date_value: str):
        requested = date_value.strip().lower()
        if requested == "show":
            cutoff = self.get_tracker_start()
            current = cutoff.isoformat() if cutoff else "none (counting all plays)"
            await interaction.response.send_message(f"Tracker start date: **{current}**", ephemeral=True)
            return
        if not can_change_settings(interaction):
            await interaction.response.send_message("Only operators or server managers can change the tracker start date.", ephemeral=True)
            return
        if requested in {"clear", "none", "off"}:
            self.set_tracker_start(None)
            await interaction.response.send_message("Tracker start date cleared. All plays now count.", ephemeral=True)
            logger.info("tracker_start_date_cleared user=%s", interaction.user.id)
            return
        try:
            parsed = date.fromisoformat(requested)
        except ValueError:
            await interaction.response.send_message("Use the format YYYY-MM-DD, or 'clear' / 'show'.", ephemeral=True)
            return
        self.set_tracker_start(parsed)
        await interaction.response.send_message(
            f"Tracker now counts plays settled on or after **{parsed.isoformat()}** (Eastern). Run /update_tracker to refresh.",
            ephemeral=True,
        )
        logger.info("tracker_start_date_set value=%s user=%s", parsed.isoformat(), interaction.user.id)
