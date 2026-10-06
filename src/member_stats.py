import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from src.config import WHOP_MEMBERSHIP_SYNC_ENABLED, MEMBER_STATS_REFRESH_ENABLED, LIVE_STATS_MOD_ROLE_IDS, GUILD_ID
from src.services.api_budget_service import ApiBudgetDenied
from src.services.membership_service import MembershipService
from src.services.member_stats_service import MemberStatsService, SPORTS
from src.services.player_stats_service import PlayerStatsService, PLAYER_SPORTS
from src.services.player_season_service import PlayerSeasonService

logger = logging.getLogger("member_stats")
SPORT_CHOICES = [app_commands.Choice(name=name, value=key) for key, name in SPORTS.items()]


def is_stats_moderator(interaction) -> bool:
    return (
        GUILD_ID is not None and getattr(interaction, "guild_id", None) == GUILD_ID
        and isinstance(interaction.user, discord.Member)
        and any(role.id in LIVE_STATS_MOD_ROLE_IDS for role in interaction.user.roles)
    )


class MemberStats(commands.Cog):
    def __init__(self, membership=None, stats=None, player_stats=None, season_stats=None):
        self.membership = membership or MembershipService()
        self.stats = stats or MemberStatsService()
        self.player_stats = player_stats or PlayerStatsService()
        self.season_stats = season_stats or PlayerSeasonService()

    async def respond(self, interaction, mode, sport, team=None, opponent=None, refresh=False, *, game_id=None, player=None, league=None):
        await interaction.response.defer(ephemeral=True)
        moderator = is_stats_moderator(interaction)
        if not moderator and not WHOP_MEMBERSHIP_SYNC_ENABLED:
            await interaction.followup.send("Membership verification is not enabled. Stats tools are unavailable.", ephemeral=True)
            return
        try:
            allowed = moderator or await asyncio.to_thread(self.membership.has_stats_access, interaction.user.id)
            if not allowed:
                await interaction.followup.send("Stats tools require a verified paid membership, an eligible seven-day trial, or an explicit owner/moderator grant.", ephemeral=True)
                return
            # Validate the report before spending a provider request.
            if mode == "playerstats":
                report = lambda: self.season_stats.report(sport, league, player)
                refresh_report = lambda: self.season_stats.refresh(sport, league, player, interaction.user.id)
            elif mode == "gamestats":
                report = lambda: self.player_stats.report(sport, game_id, player)
                refresh_report = lambda: self.player_stats.refresh(sport, game_id, interaction.user.id)
            else:
                report = lambda: self.stats.report(mode, sport, team, opponent)
                refresh_report = lambda: self.stats.refresh(sport, interaction.user.id)
            title, description = await asyncio.to_thread(report)
            notice = None
            if refresh:
                if not MEMBER_STATS_REFRESH_ENABLED:
                    await interaction.followup.send("On-demand refresh is not enabled yet. Use this command without refresh for cached data.", ephemeral=True)
                    return
                notice = await asyncio.to_thread(refresh_report)
                title, description = await asyncio.to_thread(report)
                description = notice + "\n\n" + description
            if len(description) > 4096:
                raise RuntimeError("Cached stats report exceeds Discord's message limit.")
            embed = discord.Embed(title=title, description=description, color=0x9146BF)
            footer = (
                "Current/previous season; previous season reused from cache" if mode == "playerstats" else
                "Game-specific player snapshot; see update time" if mode == "gamestats" else
                "Today's UTC date refreshed; other dates cached"
            )
            embed.set_footer(text=footer if notice else "Cached data only • No live API request")
            await interaction.followup.send(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        except (ValueError, ApiBudgetDenied) as exc:
            await interaction.followup.send(str(exc), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            logger.exception("member_stats_failed mode=%s user=%s", mode, interaction.user.id)
            await interaction.followup.send("Stats or membership verification is temporarily unavailable. Please retry later or contact support.", ephemeral=True)

    @app_commands.command(name="matchup", description="Paid stats: upcoming matchup; optional limited refresh of today's data")
    @app_commands.guild_only()
    @app_commands.choices(sport=SPORT_CHOICES)
    async def matchup(self, interaction: discord.Interaction, sport: str, team: str, opponent: str, refresh: bool = False):
        await self.respond(interaction, "matchup", sport, team, opponent, refresh)

    @app_commands.command(name="teamstats", description="Paid stats: recent team form; optional limited refresh of today's data")
    @app_commands.guild_only()
    @app_commands.choices(sport=SPORT_CHOICES)
    async def teamstats(self, interaction: discord.Interaction, sport: str, team: str, refresh: bool = False):
        await self.respond(interaction, "teamstats", sport, team, refresh=refresh)

    @app_commands.command(name="schedule", description="Paid stats: seven-day cached schedule; optional limited refresh of today")
    @app_commands.guild_only()
    @app_commands.choices(sport=SPORT_CHOICES)
    async def schedule(self, interaction: discord.Interaction, sport: str, team: str | None = None, refresh: bool = False):
        await self.respond(interaction, "schedule", sport, team, refresh=refresh)

    @app_commands.command(name="results", description="Paid stats: recent final scores; optional limited refresh of today's data")
    @app_commands.guild_only()
    @app_commands.choices(sport=SPORT_CHOICES)
    async def results(self, interaction: discord.Interaction, sport: str, team: str | None = None, refresh: bool = False):
        await self.respond(interaction, "results", sport, team, refresh=refresh)

    @app_commands.command(name="gamestats", description="Player/driver stats for a current or upcoming game")
    @app_commands.guild_only()
    @app_commands.describe(league="Choose a league", game="Choose a current or upcoming game", player="Choose a player/driver; omit to list everyone available")
    @app_commands.choices(sport=[app_commands.Choice(name=name, value=key) for key, name in PLAYER_SPORTS.items()])
    async def gamestats(self, interaction: discord.Interaction, sport: str, league: str, game: str, player: str | None = None, refresh: bool = False):
        if not game.isdecimal() or int(game) <= 0:
            await interaction.response.send_message("Pick a game from the list.", ephemeral=True)
            return
        await self.respond(interaction, "gamestats", sport, refresh=refresh, game_id=int(game), player=player)

    def autocomplete_allowed(self, interaction) -> bool:
        return bool(interaction.guild_id) and (WHOP_MEMBERSHIP_SYNC_ENABLED or is_stats_moderator(interaction))

    @gamestats.autocomplete("league")
    async def game_league_suggestions(self, interaction: discord.Interaction, current: str):
        if not self.autocomplete_allowed(interaction):
            return []
        sport = getattr(interaction.namespace, "sport", "")
        try:
            rows = await asyncio.wait_for(asyncio.to_thread(self.player_stats.leagues, sport, current), timeout=2)
            return [app_commands.Choice(name=row["name"][:100], value=row["league_id"]) for row in rows[:25]]
        except Exception:
            logger.exception("game_league_autocomplete_failed sport=%s", sport)
            return []

    @gamestats.autocomplete("game")
    async def game_suggestions(self, interaction: discord.Interaction, current: str):
        if not self.autocomplete_allowed(interaction):
            return []
        sport = getattr(interaction.namespace, "sport", "")
        league = getattr(interaction.namespace, "league", "")
        try:
            rows = await asyncio.wait_for(asyncio.to_thread(self.player_stats.games, sport, league, current), timeout=2)
            return [app_commands.Choice(name=row["label"][:100], value=str(row["id"])) for row in rows[:25]]
        except Exception:
            logger.exception("game_autocomplete_failed sport=%s league=%s", sport, league)
            return []

    @gamestats.autocomplete("player")
    async def game_player_suggestions(self, interaction: discord.Interaction, current: str):
        if not self.autocomplete_allowed(interaction):
            return []
        sport = getattr(interaction.namespace, "sport", "")
        game = str(getattr(interaction.namespace, "game", "") or "")
        if not game.isdecimal():
            return []
        try:
            rows = await asyncio.wait_for(asyncio.to_thread(self.player_stats.players, sport, int(game), current), timeout=2)
            return [app_commands.Choice(name=f"{row['name']} - {row['team']}"[:100], value=row["name"][:100]) for row in rows[:25]]
        except Exception:
            logger.exception("game_player_autocomplete_failed sport=%s", sport)
            return []

    @app_commands.command(name="playerstats", description="Player/driver current and previous season stats; filter sport, league and name")
    @app_commands.guild_only()
    @app_commands.describe(league="Choose a league for the selected sport", player="Type a name and select a suggestion")
    @app_commands.choices(sport=[app_commands.Choice(name=name, value=key) for key, name in PLAYER_SPORTS.items()])
    async def playerstats(self, interaction: discord.Interaction, sport: str, league: str, player: str, refresh: bool = False):
        await self.respond(interaction, "playerstats", sport, refresh=refresh, league=league, player=player)

    @playerstats.autocomplete("league")
    async def league_suggestions(self, interaction: discord.Interaction, current: str):
        if not interaction.guild_id or not (WHOP_MEMBERSHIP_SYNC_ENABLED or is_stats_moderator(interaction)):
            return []
        sport = getattr(interaction.namespace, "sport", "")
        try:
            rows = await asyncio.wait_for(asyncio.to_thread(self.season_stats.leagues, sport, current), timeout=2)
            return [app_commands.Choice(name=row["name"][:100], value=row["league_id"]) for row in rows[:25]]
        except Exception:
            logger.exception("player_league_autocomplete_failed sport=%s", sport)
            return []

    @playerstats.autocomplete("player")
    async def player_suggestions(self, interaction: discord.Interaction, current: str):
        if not interaction.guild_id or not (WHOP_MEMBERSHIP_SYNC_ENABLED or is_stats_moderator(interaction)):
            return []
        sport = getattr(interaction.namespace, "sport", "")
        league = getattr(interaction.namespace, "league", "")
        try:
            rows = await asyncio.wait_for(asyncio.to_thread(self.season_stats.players, sport, league, current), timeout=2)
            return [app_commands.Choice(
                name=(row["name"] + (f" - {row['team_name']}" if row.get("team_name") else ""))[:100],
                value=str(row["player_id"]),
            ) for row in rows[:25]]
        except Exception:
            logger.exception("player_name_autocomplete_failed sport=%s league=%s", sport, league)
            return []
