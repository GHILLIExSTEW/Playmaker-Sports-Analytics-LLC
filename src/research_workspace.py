from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import discord
import httpx
from discord import app_commands
from discord.ext import commands, tasks
from postgrest.exceptions import APIError

from src.config import (
    API_SPORTS_BUDGET_ENABLED, GUILD_ID, LIVE_STATS_MOD_ROLE_IDS,
    OPERATOR_ROLE_IDS, TRACKER_ROLE_ID, WHOP_MEMBERSHIP_SYNC_ENABLED,
)
from src.member_stats import is_stats_moderator
from src.services.api_budget_service import ApiBudgetDenied, member_request_batch, member_request_user
from src.services.api_sports_multi_service import ApiSportsMultiService, DATE_PRODUCTS, TRACKER_TIMEZONE, model_history_seasons, normalize_event
from src.services.api_sports_service import ApiSportsService, normalize_game
from src.services.data_model_service import COMPETITIONS
from src.services.membership_service import MembershipService
from src.services.player_stats_service import PlayerStatsService, athlete_records
from src.services.supabase_service import supabase_service
from src.services.website_capper_service import WEBSITE_OWNER_ROLE_ID
from src.datetime_utils import parse_iso_datetime

logger = logging.getLogger("official_play_bot")
RESEARCH_STALE_AFTER = timedelta(minutes=60)


class ResearchRefreshService:
    def __init__(self, database=None, api=None, multi=None, players=None):
        self.db = database or supabase_service
        self.api = api or ApiSportsService()
        self.multi = multi or ApiSportsMultiService()
        self.players = players or PlayerStatsService()

    def run(self, request: dict) -> str:
        if not API_SPORTS_BUDGET_ENABLED:
            raise ApiBudgetDenied("Live refresh is disabled until the shared API budget is enabled.")
        authorized = self.db._ensure_client().rpc("research_refresh_authorized", {
            "p_user_id": request["user_id"], "p_discord_user_id": request["discord_user_id"],
            "p_guild_id": request["guild_id"], "p_role_id": request["role_id"],
        }).execute().data
        if authorized is not True:
            raise PermissionError("Research access or verified Discord identity is no longer active.")
        competition = request["competition"]
        if competition not in COMPETITIONS:
            raise ValueError("Unsupported research competition.")
        _, sport, league = COMPETITIONS[competition]
        game_id = request["game_id"]
        games = self.db.select("research_games", "*", {"competition": competition, "game_id": game_id}).data
        if not games:
            raise ValueError("The selected game is no longer in this competition.")
        kind = request["kind"]
        if kind == "players":
            return self.players.refresh(sport, int(game_id), int(request["discord_user_id"]))
        if kind not in {"game", "odds"}:
            raise ValueError("Unsupported refresh type.")
        token = member_request_user.set(request["discord_user_id"])
        try:
            now = datetime.now(timezone.utc)
            if kind == "game":
                if sport in {"nfl", "ncaa"}:
                    rows = self.api._request("games", {"id": game_id})
                else:
                    base, endpoint = DATE_PRODUCTS[sport]
                    rows = self.multi._request(base, endpoint, {"id": game_id})
                normalized = [
                    normalize_game(row, now) if sport == "nfl" else normalize_event(sport, row, now)
                    for row in rows
                ]
                matching = [
                    row for row in normalized
                    if str(row.get("game_id") if sport == "nfl" else row["event_id"]) == game_id
                    and str(row["league_id"]) == str(league)
                ]
                if len(matching) != 1:
                    raise ValueError("Provider did not return the selected game in its configured league.")
                table, keys = ("api_sports_nfl_games", ["game_id"]) if sport == "nfl" else ("api_sports_events", ["sport_slug", "event_id"])
                self.db.upsert(table, matching[0], keys)
            else:
                base = self.api.base_url if sport in {"nfl", "ncaa"} else DATE_PRODUCTS[sport][0]
                if sport in {"nfl", "ncaa"}:
                    rows = self.multi._request(base, "odds", {"game": game_id})
                else:
                    season = model_history_seasons(sport, now.astimezone(TRACKER_TIMEZONE).date())[1]
                    rows = []
                    # These feeds use league/season pagination, not an assumed game filter.
                    with member_request_batch(base, int(request["discord_user_id"]), 5):
                        for page in range(1, 6):
                            payload, total = self.multi._request_page(base, "odds", {
                                "league": league, "season": season, **({"page": page} if page > 1 else {}),
                            })
                            rows.extend(payload)
                            if any(str((row.get("game") or {}).get("id")) == game_id for row in payload) or page >= total:
                                break
                matching = [row for row in rows if str((row.get("game") or {}).get("id")) == game_id]
                if not matching:
                    raise ValueError("No sportsbook odds were supplied for this game; cached data was not replaced.")
                books = [book for row in matching for book in row.get("bookmakers", [])]
                self.db.upsert("research_odds", {
                    "competition": competition, "game_id": game_id, "bookmakers": books,
                    "fetched_at": now.isoformat(),
                }, ["competition", "game_id"])
            return f"Refreshed {kind}. Odds outside football reserve five requests for bounded league pagination; other refreshes reserve one. Provider coverage may be incomplete."
        finally:
            member_request_user.reset(token)

    def process_next(self) -> bool:
        # The worker-chosen token makes a replayed claim/finish idempotent, so transport drops can retry.
        # Provider work in run() is still never retried.
        token = str(uuid4())
        rows = self.db._execute(lambda: self.db._ensure_client().rpc(
            "claim_research_refresh", {"p_claim_token": token}).execute()).data
        if not rows:
            return False
        request = rows[0]
        try:
            result = self.run(request)
            state = "completed"
        except Exception as exc:
            logger.exception("research_refresh_failed request=%s", request["id"])
            result = str(exc)[:1000]
            state = "failed"
        saved = self.db._execute(lambda: self.db._ensure_client().rpc("finish_research_refresh", {
            "p_id": request["id"], "p_claim_token": token, "p_state": state, "p_result": result,
        }).execute()).data
        if saved is not True:
            raise RuntimeError("Research refresh tracking changed; inspect the request before retrying.")
        return True


def _numeric_score(value) -> float | None:
    if isinstance(value, dict):
        value = value.get("total")
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


class ResearchWorkspaceData:
    def __init__(self, database=None):
        self.db = database or supabase_service

    def games(self, competition: str, current: str = "") -> list[dict]:
        if competition not in COMPETITIONS:
            return []
        now = datetime.now(timezone.utc)
        query = self.db._ensure_client().table("research_games").select(
            "competition,sport,game_id,start_at,home_id,home_name,away_id,away_name,"
            "home_score,away_score,status,synced_at",
        ).eq("competition", competition).gte(
            "start_at", (now - timedelta(days=7)).isoformat(),
        ).lte("start_at", (now + timedelta(days=14)).isoformat()).order(
            "start_at",
        ).limit(200)
        rows = self.db._execute(query.execute).data
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise RuntimeError("Research game cache returned an invalid response.")
        text = current.strip().casefold()
        return [
            row for row in rows
            if not text or text in " ".join(
                str(row.get(key) or "") for key in ("home_name", "away_name", "game_id", "status")
            ).casefold()
        ][:25]

    def matchup(self, competition: str, game_id: str) -> dict:
        if competition not in COMPETITIONS:
            raise ValueError("Select a supported research league.")
        games = self.db.select("research_games", "*", {
            "competition": competition, "game_id": game_id,
        }).data
        if not isinstance(games, list) or any(not isinstance(row, dict) for row in games):
            raise RuntimeError("Selected research game returned an invalid response.")
        if len(games) != 1:
            raise ValueError("Select a game from the cached list for this league.")
        game = games[0]
        history: dict[str, dict] = {}
        team_ids = {str(game.get(key)) for key in ("home_id", "away_id") if game.get(key)}
        for team_id in team_ids:
            if not team_id.isdecimal():
                raise RuntimeError("Cached research game has an invalid team identity.")
            for side in ("home_id", "away_id"):
                query = self.db._ensure_client().table("research_games").select("*").eq(
                    "competition", competition,
                ).eq(side, team_id).lt("start_at", game["start_at"]).in_(
                    "status", ["FT", "AOT", "AP", "COMPLETED", "FINISHED"],
                ).order("start_at", desc=True).limit(100)
                rows = self.db._execute(query.execute).data
                if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                    raise RuntimeError("Research history returned an invalid response.")
                history.update({str(row["game_id"]): row for row in rows})
        ordered_history = sorted(
            history.values(), key=lambda row: (row["start_at"], row["game_id"]), reverse=True,
        )[:100]
        return {"game": game, "history": ordered_history}

    def players(self, competition: str, game_id: str, workspace: dict | None = None) -> list[dict]:
        workspace = workspace or self.matchup(competition, game_id)
        game = workspace["game"]
        if game["sport"] in {"baseball", "hockey"}:
            return []
        history = workspace["history"][:10]
        sport = game["sport"]
        selected_id = str(game["game_id"])
        game_ids = [selected_id, *(str(row["game_id"]) for row in history)]
        if not history:
            game_ids = [selected_id]
        snapshots = self.db._execute(
            lambda: self.db._ensure_client().table("api_sports_player_game_stats").select(
                "game_id,payload,synced_at",
            ).eq("sport_slug", sport).in_("game_id", game_ids).execute(),
        ).data
        if not isinstance(snapshots, list) or any(not isinstance(row, dict) for row in snapshots):
            raise RuntimeError("Player research cache returned an invalid response.")
        game_by_id = {str(row["game_id"]): row for row in history}
        game_by_id[selected_id] = game
        records: dict[tuple[str, str], dict] = {}
        for snapshot in snapshots:
            snapshot_id = str(snapshot.get("game_id", ""))
            historic_game = game_by_id.get(snapshot_id)
            if historic_game is None or not isinstance(snapshot.get("payload"), list):
                raise RuntimeError("Player snapshot does not match the selected matchup history.")
            try:
                parsed_id = int(snapshot_id)
                parsed = athlete_records(sport, snapshot["payload"], parsed_id, historic_game)
            except (TypeError, ValueError, KeyError) as exc:
                raise RuntimeError("Cached player statistics could not be parsed.") from exc
            for entry in parsed:
                key = (str(entry["id"]), entry["team"])
                record = records.setdefault(key, {
                    "id": str(entry["id"]), "name": entry["name"], "team": entry["team"], "logs": [],
                })
                values = {}
                for group in entry["groups"]:
                    for stat in group["statistics"]:
                        number = _numeric_score(stat.get("value"))
                        if number is not None:
                            name = " ".join(
                                value for value in (str(group.get("name") or ""), str(stat.get("name") or ""))
                                if value
                            )
                            if name:
                                values[name] = number
                record["logs"].append({
                    "game": snapshot_id, "date": historic_game["start_at"],
                    "updated": snapshot.get("synced_at"), "stats": values,
                    "current": snapshot_id == selected_id,
                    "home_id": historic_game.get("home_id"), "away_id": historic_game.get("away_id"),
                    "home_name": historic_game.get("home_name"), "away_name": historic_game.get("away_name"),
                })
        return list(records.values())

    def odds(self, competition: str, game_id: str) -> dict | None:
        rows = self.db.select("research_odds", "bookmakers,fetched_at", {
            "competition": competition, "game_id": game_id,
        }).data
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise RuntimeError("Cached odds returned an invalid response.")
        return rows[0] if rows else None

    def player_snapshot_updated(self, sport: str, game_id: str) -> str | None:
        rows = self.db._ensure_client().table("api_sports_player_game_stats").select(
            "synced_at",
        ).eq("sport_slug", sport).eq("game_id", int(game_id)).limit(1).execute().data
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise RuntimeError("Player snapshot freshness returned an invalid response.")
        return rows[0].get("synced_at") if rows else None

    def model(self, competition: str) -> tuple[list[dict], dict | None]:
        if competition not in COMPETITIONS:
            raise ValueError("Select a supported model league.")
        now = datetime.now(timezone.utc)

        def projections():
            return self.db._ensure_client().table("sport_model_projections").select(
                "game_id,start_at,home_name,away_name,home_score,away_score,home_probability,"
                "home_sample,away_sample,league_sample,market_home_probability,market_spread,"
                "market_total,market_books,market_fetched_at,market_edge,cover_probability,"
                "over_probability,details,model_version,data_updated_at,generated_at",
            ).eq("competition", competition).gt(
                "start_at", (now - timedelta(hours=1)).isoformat(),
            ).lte("start_at", (now + timedelta(days=14)).isoformat()).order(
                "start_at",
            ).limit(25).execute()

        rows = self.db._execute(projections).data
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise RuntimeError("Model projection cache returned an invalid response.")
        backtests = self.db.select("sport_model_backtests", "report", {
            "competition": competition,
        }).data
        if not isinstance(backtests, list) or any(not isinstance(row, dict) for row in backtests):
            raise RuntimeError("Model backtest cache returned an invalid response.")
        return rows, backtests[0].get("report") if backtests else None

    def performance(self, discord_user_id: int) -> list[dict]:
        rows = self.db._ensure_client().rpc("discord_operator_capper_performance", {
            "p_discord_user_id": str(discord_user_id),
        }).execute().data
        if not isinstance(rows, list) or any(
            not isinstance(row, dict) or not all(
                isinstance(row.get(key), str) for key in ("settled_at", "sport", "capper", "market", "status")
            ) or not all(
                isinstance(row.get(key), (int, float)) and math.isfinite(row[key])
                for key in ("odds", "units", "net_units")
            ) for row in rows
        ):
            raise RuntimeError("Capper performance returned an invalid response.")
        return rows

    def operator_matchup_fields(self, game: dict) -> list[tuple[str, str]]:
        projection_rows = self.db.select("sport_model_projections", "*", {
            "competition": game["competition"], "game_id": game["game_id"],
        }).data
        team_ids = [str(value) for value in (game.get("home_id"), game.get("away_id")) if value]
        injuries = []
        if team_ids:
            injury_query = self.db._ensure_client().table("sport_model_injuries").select(
                "team_id,player_name,status,description,fetched_at",
            ).eq("competition", game["competition"]).in_("team_id", team_ids).limit(50)
            injuries = self.db._execute(injury_query.execute).data
        if not isinstance(projection_rows, list) or any(not isinstance(row, dict) for row in projection_rows):
            raise RuntimeError("Model projection cache returned an invalid response.")
        if not isinstance(injuries, list) or any(not isinstance(row, dict) for row in injuries):
            raise RuntimeError("Injury cache returned an invalid response.")
        fields = []
        if projection_rows:
            row = projection_rows[0]
            fields.append(("Model snapshot", (
                f"Projected score {row['away_score']:.1f}–{row['home_score']:.1f} · "
                f"home win probability {row['home_probability'] * 100:.1f}%\n"
                f"Sample: {row['away_sample']} away / {row['home_sample']} home / {row['league_sample']} league games\n"
                f"Model {row['model_version']} · generated {ResearchWorkspace._time_tag(row['generated_at'])}"
            )))
        else:
            fields.append(("Model snapshot", "No cached model projection for this game."))
        detail = "\n".join(
            f"{row.get('player_name') or 'Player'} · {row.get('status') or 'Unknown'}"
            + (f" — {row['description']}" if row.get("description") else "")
            for row in injuries[:10]
        )
        fields.append(("Cached injuries", detail or "No current cached injury reports; no records does not mean a healthy roster."))
        return fields

    def request_refresh(
        self, discord_user_id: int, competition: str, game_id: str, kind: str,
        guild_id: int, role_id: int,
    ) -> str:
        result = self.db._ensure_client().rpc("request_discord_research_refresh", {
            "p_discord_user_id": str(discord_user_id), "p_competition": competition,
            "p_game_id": game_id, "p_kind": kind,
            "p_guild_id": str(guild_id), "p_role_id": str(role_id),
        }).execute().data
        if not isinstance(result, str):
            raise RuntimeError("Research refresh was not acknowledged.")
        return result

    def refresh_status(self, discord_user_id: int, request_id: str) -> dict | None:
        rows = self.db._ensure_client().table("research_refresh_requests").select(
            "id,competition,game_id,kind,state,requested_at,updated_at,result",
        ).eq("id", request_id).eq("discord_user_id", str(discord_user_id)).limit(1).execute().data
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise RuntimeError("Research refresh status returned an invalid response.")
        return rows[0] if rows else None


RESEARCH_CHOICES = [
    app_commands.Choice(name=name, value=key) for key, (name, _, _) in COMPETITIONS.items()
]
REFRESH_CHOICES = [
    app_commands.Choice(name="Game and scores", value="game"),
    app_commands.Choice(name="Player statistics", value="players"),
    app_commands.Choice(name="Sportsbook odds", value="odds"),
]


class ResearchWorkspace(commands.GroupCog, group_name="research", group_description="Game, player, odds, model, and performance research"):
    def __init__(self, bot, *, staff_alert, service=None, data=None, membership=None):
        self.bot = bot
        self.staff_alert = staff_alert
        self.service = service or ResearchRefreshService()
        self.data = data or ResearchWorkspaceData()
        self.membership = membership or MembershipService()
        self.transport_failed = False

    async def _is_operator(self, interaction: discord.Interaction) -> bool:
        member = interaction.user
        if not isinstance(member, discord.Member) or (
            GUILD_ID is not None and interaction.guild_id != GUILD_ID
        ):
            return False
        if any(role.id in OPERATOR_ROLE_IDS | {WEBSITE_OWNER_ROLE_ID} for role in member.roles):
            return True
        try:
            authorized = await asyncio.to_thread(
                lambda: self.data.db._ensure_client().rpc("discord_operator_research_access", {
                    "p_discord_user_id": str(member.id),
                }).execute().data,
            )
        except Exception:
            logger.exception("research_operator_access_check_failed user=%s", member.id)
            raise
        if not isinstance(authorized, bool):
            raise RuntimeError("Research access lookup returned an invalid response.")
        return authorized

    async def _member_allowed(self, interaction: discord.Interaction) -> bool:
        if await self._is_operator(interaction):
            return True
        if not interaction.guild_id:
            return False
        if not WHOP_MEMBERSHIP_SYNC_ENABLED:
            return False
        if is_stats_moderator(interaction):
            return True
        return await asyncio.to_thread(self.membership.has_stats_access, interaction.user.id)

    def _is_capper(self, interaction: discord.Interaction) -> bool:
        member = interaction.user
        return (
            isinstance(member, discord.Member)
            and interaction.guild_id is not None
            and (GUILD_ID is None or interaction.guild_id == GUILD_ID)
            and TRACKER_ROLE_ID > 0
            and any(role.id == TRACKER_ROLE_ID for role in member.roles)
        )

    async def _gate(self, interaction: discord.Interaction, *, operator_only: bool = False) -> bool:
        try:
            allowed = await self._is_operator(interaction) if operator_only else await self._member_allowed(interaction)
        except Exception:
            logger.exception("research_access_check_failed user=%s", interaction.user.id)
            await interaction.response.send_message(
                "Research access could not be verified right now. Please retry later or contact support.",
                ephemeral=True,
            )
            return False
        if not allowed:
            message = (
                "This command requires verified OPERATOR/Owner research access."
                if operator_only else
                "Research requires verified paid membership, an eligible trial, or OPERATOR/Owner research access."
            )
            await interaction.response.send_message(message, ephemeral=True)
            return False
        return True

    async def _capper_refresh_gate(self, interaction: discord.Interaction) -> bool:
        if self._is_capper(interaction):
            return True
        await interaction.response.send_message(
            "Live research refresh is reserved for verified cappers with the tracker role. "
            "You can still use cached research reports.",
            ephemeral=True,
        )
        return False

    async def _refresh_if_stale(
        self, interaction: discord.Interaction, game: dict, kind: str, updated_at: str | None,
    ) -> str | None:
        if not self._is_capper(interaction):
            return None
        stale = True
        if updated_at:
            stale = datetime.now(timezone.utc) - parse_iso_datetime(updated_at) >= RESEARCH_STALE_AFTER
        if not stale:
            return None
        if kind == "players" and game["sport"] in {"baseball", "hockey"}:
            return "Live player-stat refresh is unsupported for this league; cached data shown."
        if not API_SPORTS_BUDGET_ENABLED:
            logger.info("research_auto_refresh_disabled user=%s game=%s kind=%s", interaction.user.id, game["game_id"], kind)
            return "Live refresh is disabled; cached data shown."
        try:
            request_id = await asyncio.to_thread(
                self.data.request_refresh, interaction.user.id, game["competition"], game["game_id"],
                kind, interaction.guild_id, TRACKER_ROLE_ID,
            )
        except APIError:
            logger.info(
                "research_auto_refresh_not_queued user=%s competition=%s game=%s kind=%s",
                interaction.user.id, game["competition"], game["game_id"], kind, exc_info=True,
            )
            return "Refresh not queued (cooldown or access check); cached data shown."
        except Exception:
            logger.exception(
                "research_auto_refresh_failed user=%s competition=%s game=%s kind=%s",
                interaction.user.id, game["competition"], game["game_id"], kind,
            )
            return "Refresh could not be queued; cached data shown."
        logger.info(
            "research_auto_refresh_queued user=%s competition=%s game=%s kind=%s request=%s",
            interaction.user.id, game["competition"], game["game_id"], kind, request_id,
        )
        return f"Refresh queued (`{request_id}`); showing cached data while it runs."

    async def _game_choices(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        try:
            if not await self._member_allowed(interaction):
                return []
            competition = getattr(interaction.namespace, "competition", "")
            rows = await asyncio.wait_for(
                asyncio.to_thread(self.data.games, competition, current), timeout=2,
            )
            return [
                app_commands.Choice(
                    name=f"{row.get('away_name') or 'TBD'} at {row.get('home_name') or 'TBD'} · "
                    f"{parse_iso_datetime(row['start_at']):%b %d %H:%M} UTC"[:100],
                    value=str(row["game_id"]),
                ) for row in rows
            ]
        except Exception:
            logger.exception("research_game_autocomplete_failed user=%s", interaction.user.id)
            return []

    @app_commands.command(name="matchup", description="Compare cached form, recent games, and head-to-head")
    @app_commands.guild_only()
    @app_commands.choices(competition=RESEARCH_CHOICES)
    async def research_matchup(self, interaction: discord.Interaction, competition: str, game: str):
        if not await self._gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            workspace = await asyncio.to_thread(self.data.matchup, competition, game)
            refresh_note = await self._refresh_if_stale(
                interaction, workspace["game"], "game", workspace["game"].get("synced_at"),
            )
            is_operator = await self._is_operator(interaction)
            operator_fields = await asyncio.to_thread(
                self.data.operator_matchup_fields, workspace["game"],
            ) if is_operator else []
            embed = self._matchup_embed(workspace, operator_fields=operator_fields)
            if refresh_note:
                embed.add_field(name="Data freshness", value=refresh_note, inline=False)
            elif not is_operator:
                embed.set_footer(text="Cached data only · refreshed automatically for cappers when over 60 minutes old")
            await interaction.followup.send(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            logger.exception("research_matchup_failed user=%s competition=%s game=%s", interaction.user.id, competition, game)
            await interaction.followup.send(
                "The cached matchup report is temporarily unavailable. Please retry or contact support.",
                ephemeral=True,
            )

    @research_matchup.autocomplete("game")
    async def matchup_game_choices(self, interaction: discord.Interaction, current: str):
        return await self._game_choices(interaction, current)

    @app_commands.command(name="players", description="Compare cached player logs and evaluate a threshold")
    @app_commands.guild_only()
    @app_commands.choices(competition=RESEARCH_CHOICES)
    @app_commands.describe(
        game="Choose a cached game", player="Player name or ID; omit to list available players",
        statistic="Statistic name exactly as listed in the cached snapshot",
        threshold="Optional number to count over/under/push observations",
        split="Limit historical observations by venue or head-to-head",
    )
    @app_commands.choices(split=[
        app_commands.Choice(name="All cached games", value="all"),
        app_commands.Choice(name="Home games", value="home"),
        app_commands.Choice(name="Away games", value="away"),
        app_commands.Choice(name="Head-to-head games", value="head-to-head"),
    ])
    async def research_players(
        self, interaction: discord.Interaction, competition: str, game: str,
        player: str | None = None, statistic: str | None = None, threshold: float | None = None,
        split: str = "all",
    ):
        if not await self._gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            workspace = await asyncio.to_thread(self.data.matchup, competition, game)
            records = await asyncio.to_thread(self.data.players, competition, game, workspace)
            updated_at = await asyncio.to_thread(
                self.data.player_snapshot_updated, workspace["game"]["sport"], game,
            )
            refresh_note = await self._refresh_if_stale(
                interaction, workspace["game"], "players", updated_at,
            )
            text = self._player_report(workspace["game"], records, player, statistic, threshold, split)
            embed = discord.Embed(
                title=f"{workspace['game']['away_name']} at {workspace['game']['home_name']} · Player research",
                description=text[:4096],
                color=0x9146BF,
            )
            if refresh_note:
                embed.add_field(name="Data freshness", value=refresh_note, inline=False)
            embed.set_footer(text="Cached game logs only · threshold is user-entered, not a sportsbook line")
            await interaction.followup.send(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            logger.exception("research_players_failed user=%s competition=%s game=%s", interaction.user.id, competition, game)
            await interaction.followup.send(
                "Cached player research is temporarily unavailable. Please retry or contact support.",
                ephemeral=True,
            )

    @research_players.autocomplete("game")
    async def player_game_choices(self, interaction: discord.Interaction, current: str):
        return await self._game_choices(interaction, current)

    @app_commands.command(name="odds", description="Compare cached sportsbook lines for a game")
    @app_commands.guild_only()
    @app_commands.choices(competition=RESEARCH_CHOICES)
    async def research_odds(
        self, interaction: discord.Interaction, competition: str, game: str, market: str | None = None,
    ):
        if not await self._gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            odds = await asyncio.to_thread(self.data.odds, competition, game)
            game_rows = await asyncio.to_thread(self.data.matchup, competition, game)
            selected_game = game_rows["game"]
            refresh_note = await self._refresh_if_stale(
                interaction, selected_game, "odds", odds.get("fetched_at") if odds else None,
            )
            if odds is None:
                embed = discord.Embed(
                    title="Sportsbook lines",
                    description="No cached sportsbook snapshot is available.",
                    color=0x9146BF,
                )
            else:
                embed = self._odds_embed(odds, competition, game, market)
            if refresh_note:
                embed.add_field(name="Data freshness", value=refresh_note, inline=False)
            embed.set_footer(text="Cached quotes only · implied probability includes bookmaker margin")
            await interaction.followup.send(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            logger.exception("research_odds_failed user=%s competition=%s game=%s", interaction.user.id, competition, game)
            await interaction.followup.send(
                "Cached odds are temporarily unavailable. Please retry or contact support.",
                ephemeral=True,
            )

    @research_odds.autocomplete("game")
    async def odds_game_choices(self, interaction: discord.Interaction, current: str):
        return await self._game_choices(interaction, current)

    @app_commands.command(name="model", description="View cached model projections and backtest summary")
    @app_commands.guild_only()
    @app_commands.choices(competition=RESEARCH_CHOICES)
    async def research_model(self, interaction: discord.Interaction, competition: str):
        if not await self._gate(interaction, operator_only=True):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            projections, backtest = await asyncio.to_thread(self.data.model, competition)
            embed = self._model_embed(competition, projections, backtest)
            await interaction.followup.send(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            logger.exception("research_model_failed user=%s competition=%s", interaction.user.id, competition)
            await interaction.followup.send(
                "Cached model research is temporarily unavailable. Please retry or contact support.",
                ephemeral=True,
            )

    @app_commands.command(name="performance", description="Review settled capper performance")
    @app_commands.guild_only()
    @app_commands.choices(days=[
        app_commands.Choice(name="7 days", value=7), app_commands.Choice(name="30 days", value=30),
        app_commands.Choice(name="90 days", value=90), app_commands.Choice(name="All cached results", value=0),
    ])
    @app_commands.describe(
        days="Settled results window", sport="Optional exact sport filter",
        capper="Optional capper-name filter", market="Optional market-name filter",
    )
    async def research_performance(
        self, interaction: discord.Interaction, days: int = 30,
        sport: str | None = None, capper: str | None = None, market: str | None = None,
    ):
        if not await self._gate(interaction, operator_only=True):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            rows = await asyncio.to_thread(self.data.performance, interaction.user.id)
            embed = self._performance_embed(rows, days, sport, capper, market)
            await interaction.followup.send(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            logger.exception("research_performance_failed user=%s", interaction.user.id)
            await interaction.followup.send(
                "Capper performance is temporarily unavailable. Please retry or contact support.",
                ephemeral=True,
            )

    @app_commands.command(name="refresh", description="Queue a budgeted live refresh for a cached game")
    @app_commands.guild_only()
    @app_commands.choices(competition=RESEARCH_CHOICES, kind=REFRESH_CHOICES)
    async def research_refresh(
        self, interaction: discord.Interaction, competition: str, game: str, kind: str,
    ):
        if not await self._capper_refresh_gate(interaction):
            return
        if not API_SPORTS_BUDGET_ENABLED:
            await interaction.response.send_message(
                "Live research refresh is disabled until the shared API budget is enabled. Cached reports remain available.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            request_id = await asyncio.to_thread(
                self.data.request_refresh, interaction.user.id, competition, game, kind,
                interaction.guild_id, TRACKER_ROLE_ID,
            )
            embed = discord.Embed(
                title="Research refresh queued",
                description=(
                    f"**{competition}** · `{game}` · {kind}\n"
                    f"Request ID: `{request_id}`\n"
                    "Use `/research refresh_status` to check its result."
                ),
                color=0x9146BF,
            )
            embed.set_footer(text="Shared API budget and per-user/event cooldowns apply")
            await interaction.followup.send(embed=embed, ephemeral=True)
        except (ApiBudgetDenied, ValueError, APIError) as exc:
            logger.exception("research_refresh_request_failed user=%s competition=%s game=%s kind=%s",
                             interaction.user.id, competition, game, kind)
            await interaction.followup.send(
                str(exc), ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception:
            logger.exception("research_refresh_request_failed user=%s competition=%s game=%s kind=%s",
                             interaction.user.id, competition, game, kind)
            await interaction.followup.send(
                "Refresh could not be queued. Please retry or contact support.",
                ephemeral=True,
            )

    @app_commands.command(name="refresh_status", description="Check one of your research refresh requests")
    @app_commands.guild_only()
    async def research_refresh_status(self, interaction: discord.Interaction, request_id: str):
        if not await self._capper_refresh_gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            row = await asyncio.to_thread(self.data.refresh_status, interaction.user.id, request_id)
            if row is None:
                await interaction.followup.send(
                    embed=discord.Embed(
                        title="Refresh not found",
                        description="No refresh request with that ID belongs to your account.",
                        color=0x9146BF,
                    ),
                    ephemeral=True,
                )
                return
            result = f"\n{row['result']}" if row.get("result") else ""
            embed = discord.Embed(
                title="Research refresh status",
                description=f"**{row['kind']}** · **{row['state']}**\n{row['competition']} · `{row['game_id']}`{result}",
                color=0x9146BF,
            )
            embed.set_footer(text=f"Requested {self._time_tag(row['requested_at'])}")
            await interaction.followup.send(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            logger.exception("research_refresh_status_failed user=%s", interaction.user.id)
            await interaction.followup.send(
                "Refresh status is temporarily unavailable. Please retry or contact support.",
                ephemeral=True,
            )

    @research_refresh.autocomplete("game")
    async def refresh_game_choices(self, interaction: discord.Interaction, current: str):
        return await self._game_choices(interaction, current)

    @staticmethod
    def _time_tag(value: str) -> str:
        return f"<t:{int(parse_iso_datetime(value).timestamp())}:f>"

    @classmethod
    def _matchup_embed(cls, workspace: dict, *, operator_fields: list[tuple[str, str]]) -> discord.Embed:
        game, history = workspace["game"], workspace["history"]
        escape = discord.utils.escape_markdown
        embed = discord.Embed(
            title=f"{escape(str(game['away_name']))} at {escape(str(game['home_name']))}",
            description=f"{cls._time_tag(game['start_at'])} · {escape(str(game['status']))} · Cached data only",
            color=0x9146BF,
        )
        for team_id, team_name in ((game.get("home_id"), game["home_name"]), (game.get("away_id"), game["away_name"])):
            scores = []
            for row in history:
                if str(team_id) not in {str(row.get("home_id")), str(row.get("away_id"))}:
                    continue
                home_score, away_score = _numeric_score(row.get("home_score")), _numeric_score(row.get("away_score"))
                if home_score is None or away_score is None:
                    continue
                own, opponent = (home_score, away_score) if str(row.get("home_id")) == str(team_id) else (away_score, home_score)
                scores.append((row, own, opponent))
            recent = scores[:10]
            wins = sum(own > opponent for _, own, opponent in recent)
            losses = sum(own < opponent for _, own, opponent in recent)
            ties = len(recent) - wins - losses
            points_for = sum(own for _, own, _ in recent) / len(recent) if recent else None
            points_against = sum(other for _, _, other in recent) / len(recent) if recent else None
            home_record = [item for item in recent if str(item[0].get("home_id")) == str(team_id)]
            away_record = [item for item in recent if str(item[0].get("away_id")) == str(team_id)]
            last_five = recent[:5]
            last_five_wins = sum(own > opponent for _, own, opponent in last_five)
            last_five_losses = sum(own < opponent for _, own, opponent in last_five)
            last_five_ties = len(last_five) - last_five_wins - last_five_losses
            split_text = " · ".join(
                f"{label} {sum(own > opponent for _, own, opponent in rows)}-"
                f"{sum(own < opponent for _, own, opponent in rows)}-"
                f"{sum(own == opponent for _, own, opponent in rows)}"
                for label, rows in (("Home", home_record), ("Away", away_record))
            )
            value = (
                f"**Last {len(recent)} scored games:** {wins}-{losses}-{ties}\n"
                f"Last 5: {last_five_wins}-{last_five_losses}-{last_five_ties}\n"
                f"Scored {points_for:.1f} / allowed {points_against:.1f} per game\n{split_text}"
                if recent else "No cached scored history."
            )
            embed.add_field(name=escape(team_name), value=value, inline=True)
        h2h = [
            row for row in history
            if game.get("home_id") and game.get("away_id")
            and {str(row.get("home_id")), str(row.get("away_id"))} == {
                str(game.get("home_id")), str(game.get("away_id")),
            }
        ][:5]
        embed.add_field(
            name="Recent head-to-head",
            value="\n".join(
                f"{cls._time_tag(row['start_at'])}: {escape(str(row['away_name']))} "
                f"{_numeric_score(row.get('away_score')) if _numeric_score(row.get('away_score')) is not None else 'Unavailable'} – "
                f"{_numeric_score(row.get('home_score')) if _numeric_score(row.get('home_score')) is not None else 'Unavailable'} "
                f"{escape(str(row['home_name']))}"
                for row in h2h
            ) or "No cached head-to-head games before this event.",
            inline=False,
        )
        for name, value in operator_fields:
            embed.add_field(name=name, value=value[:1024], inline=False)
        updated = min([game["synced_at"], *(row["synced_at"] for row in history[:10])])
        embed.set_footer(text=f"History may be incomplete or corrected later · oldest displayed sync {cls._time_tag(updated)}")
        return embed

    @staticmethod
    def _player_report(
        game: dict, records: list[dict], player: str | None, statistic: str | None,
        threshold: float | None, split: str = "all",
    ) -> str:
        if threshold is not None and not statistic:
            raise ValueError("Choose a statistic when providing a threshold.")
        if split not in {"all", "home", "away", "head-to-head"}:
            raise ValueError("Select a supported player-log split.")
        if not records:
            if game.get("sport") in {"baseball", "hockey"}:
                return "Individual player logs are unavailable for this provider/league adapter."
            return "No cached player logs were found for the selected matchup's prior team games. Use `/research refresh` for a budgeted player refresh."
        matchup_teams = {str(game.get("home_id")), str(game.get("away_id"))}

        def in_split(log: dict, team_name: str) -> bool:
            if split == "all":
                return True
            if split == "home":
                return team_name == log.get("home_name")
            if split == "away":
                return team_name == log.get("away_name")
            return bool(game.get("home_id") and game.get("away_id")) and {
                str(log.get("home_id")), str(log.get("away_id")),
            } == matchup_teams

        records = [
            {**record, "logs": [log for log in record["logs"] if in_split(log, record["team"])]}
            for record in records
        ]
        records = [record for record in records if record["logs"]]
        if not records:
            return f"No player logs match the {split} split in the cached sample."
        escape = discord.utils.escape_markdown
        if not player:
            choices = [f"{escape(row['name'])} — {escape(row['team'])}" for row in records[:20]]
            return (
                f"**Cached player records before {ResearchWorkspace._time_tag(game['start_at'])}**\n"
                + ("\n".join(choices) if choices else "No player records available.")
                + "\nRun `/research players` again with a player name. Samples use at most ten prior team games."
            )
        matched = [
            row for row in records
            if player.casefold() in {row["name"].casefold(), row["id"].casefold()}
            or player.casefold() in f"{row['name']} — {row['team']}".casefold()
        ]
        if len(matched) > 1:
            options = ", ".join(f"{row['name']} — {row['team']}" for row in matched[:5])
            raise ValueError(f"Player is ambiguous; use a name and team: {options}")
        if not matched:
            raise ValueError("No matching player was found in the cached game logs.")
        record = matched[0]
        observations = [
            (row["date"], row["stats"][statistic])
            for row in record["logs"]
            if not row.get("current") and statistic and statistic in row["stats"]
        ]
        available = sorted({key for row in record["logs"] for key in row["stats"]})
        prior_logs = [row for row in record["logs"] if not row.get("current")]
        current_log = next((row for row in record["logs"] if row.get("current")), None)
        if statistic and not observations and not (current_log and statistic in current_log["stats"]):
            choices = ", ".join(available[:20]) or "none"
            raise ValueError(f"Statistic not found. Available cached statistics: {choices}")
        lines = [
            f"**{escape(record['name'])} — {escape(record['team'])}**",
            f"Sample: {len(prior_logs)} prior game logs, up to ten; only pre-match data is included.",
        ]
        if statistic:
            values = [value for _, value in observations]
            line = (
                f"**{escape(statistic)}:** average {sum(values) / len(values):.2f} "
                f"across {len(values)} prior observations."
                if values else f"**{escape(statistic)}:** no prior observations in the cached sample."
            )
            if threshold is not None and values:
                line += (
                    f" At {threshold:g}: {sum(value > threshold for value in values)} over · "
                    f"{sum(value < threshold for value in values)} under · {sum(value == threshold for value in values)} push."
                )
            lines.append(line)
            if current_log and statistic in current_log["stats"]:
                lines.append(f"Selected game snapshot: {current_log['stats'][statistic]:g} (not included in the prior-game sample).")
            lines.extend(
                f"{ResearchWorkspace._time_tag(date)}: {value:g}" for date, value in observations[:10]
            )
        else:
            lines.append("Available stats: " + ", ".join(available[:30]))
            lines.append("Run again with a statistic name; thresholds are user-entered and are not sportsbook lines.")
        lines.append("Cached samples may be incomplete; this is descriptive research, not a prediction or betting advice.")
        return "\n".join(lines)[:3900]

    @staticmethod
    def _odds_embed(
        odds: dict, competition: str, game_id: str, market_filter: str | None = None,
    ) -> discord.Embed:
        embed = discord.Embed(
            title=f"{competition} · Sportsbook lines",
            description=f"Game `{game_id}` · updated {ResearchWorkspace._time_tag(odds['fetched_at'])}",
            color=0x9146BF,
        )
        added = 0
        for book in odds.get("bookmakers", []):
            if not isinstance(book, dict):
                raise RuntimeError("Cached sportsbook data has an invalid bookmaker.")
            for market in book.get("bets", []):
                if not isinstance(market, dict):
                    raise RuntimeError("Cached sportsbook data has an invalid market.")
                if market_filter and market_filter.casefold() not in str(market.get("name") or "").casefold():
                    continue
                for selection in market.get("values", []):
                    if not isinstance(selection, dict):
                        raise RuntimeError("Cached sportsbook data has an invalid selection.")
                    decimal = _numeric_score(selection.get("odd"))
                    if decimal is not None and decimal > 1:
                        embed.add_field(
                            name=f"{discord.utils.escape_markdown(str(book.get('name') or 'Book'))} · "
                            f"{discord.utils.escape_markdown(str(market.get('name') or 'Market'))}"[:256],
                            value=f"{discord.utils.escape_markdown(str(selection.get('value') or 'Selection'))}\n"
                            f"**{decimal:.2f}** decimal · {100 / decimal:.1f}% implied",
                            inline=True,
                        )
                        added += 1
                    if added >= 20:
                        break
                if added >= 20:
                    break
            if added >= 20:
                break
        if not added:
            embed.description += "\nNo usable odds lines were included in the cached snapshot."
        return embed

    @staticmethod
    def _model_embed(competition: str, projections: list[dict], backtest: dict | None) -> discord.Embed:
        name = COMPETITIONS[competition][0]
        embed = discord.Embed(title=f"{name} · model research", color=0x9146BF)
        if projections:
            for row in projections[:10]:
                edge = row.get("market_edge")
                field_value = (
                    f"Projected {row['away_score']:.1f}–{row['home_score']:.1f} · "
                    f"home probability {row['home_probability'] * 100:.1f}%\n"
                    f"Samples {row['away_sample']} away / {row['home_sample']} home / {row['league_sample']} league"
                )
                if edge is not None:
                    field_value += f"\nConsensus market edge {edge * 100:.1f} pp"
                field_value += f"\n{ResearchWorkspace._time_tag(row['generated_at'])} · {row['model_version']}"
                embed.add_field(
                    name=f"{row['away_name']} at {row['home_name']}"[:256],
                    value=field_value[:1024], inline=False,
                )
        else:
            embed.description = "No cached projections are available in the next 14 days."
        if isinstance(backtest, dict) and backtest.get("status") == "ok":
            model, baseline = backtest.get("model") or {}, backtest.get("baseline") or {}
            embed.add_field(
                name=f"Backtest · {backtest.get('games', 0)} games",
                value=(
                    f"Model Brier {model.get('brier', '—')} · baseline {baseline.get('brier', '—')}\n"
                    f"Model margin MAE {model.get('margin_mae', '—')} · total MAE {model.get('total_mae', '—')}"
                )[:1024], inline=False,
            )
        embed.set_footer(text="Cached model output, not a guarantee or betting recommendation.")
        return embed

    @staticmethod
    def _performance_embed(
        rows: list[dict], days: int, sport: str | None = None,
        capper: str | None = None, market: str | None = None,
    ) -> discord.Embed:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days) if days else None
        filtered = [
            row for row in rows if cutoff is None
            or parse_iso_datetime(row["settled_at"]) >= cutoff
        ]
        if sport:
            filtered = [row for row in filtered if sport.casefold() in row["sport"].casefold()]
        if capper:
            filtered = [row for row in filtered if capper.casefold() in row["capper"].casefold()]
        if market:
            filtered = [row for row in filtered if market.casefold() in row["market"].casefold()]
        net = sum(float(row["net_units"]) for row in filtered)
        stake = sum(float(row["units"]) for row in filtered if row["status"] != "void")
        embed = discord.Embed(title="Settled capper performance", color=0x9146BF)
        embed.description = (
            f"{len(filtered)} settled bets · {stake:.2f}u stake · {net:.2f}u net · "
            f"{(100 * net / stake):.1f}% ROI" if stake else
            f"{len(filtered)} settled bets · no non-void stake in this window"
        )
        grouped: dict[str, list[dict]] = {}
        for row in filtered:
            grouped.setdefault(row["capper"], []).append(row)
        for capper, bets in sorted(
            grouped.items(),
            key=lambda item: sum(float(row["net_units"]) for row in item[1]),
            reverse=True,
        )[:15]:
            units = sum(float(row["units"]) for row in bets if row["status"] != "void")
            result = sum(float(row["net_units"]) for row in bets)
            wlt = "/".join(str(sum(row["status"] == status for row in bets)) for status in ("win", "loss", "partial", "void"))
            embed.add_field(
                name=f"{discord.utils.escape_markdown(capper)} · W/L/P/V {wlt}"[:256],
                value=f"{len(bets)} bets · {units:.2f}u stake · {result:.2f}u net · "
                f"{(100 * result / units):.1f}% ROI" if units else f"{len(bets)} bets · {result:.2f}u net · no stake",
                inline=False,
            )
        recent = sorted(filtered, key=lambda row: row["settled_at"], reverse=True)[:5]
        if recent:
            embed.add_field(
                name="Recent trend",
                value="\n".join(
                    f"{row['settled_at'][:10]} · {discord.utils.escape_markdown(row['capper'])} · "
                    f"{discord.utils.escape_markdown(row['sport'])}/{discord.utils.escape_markdown(row['market'])} · "
                    f"{row['status']} · {float(row['net_units']):+.2f}u"
                    for row in recent
                )[:1024],
                inline=False,
            )
        window = f"{days}-day window" if days else "all cached results"
        embed.set_footer(text=f"Published settled plays · latest 10,000 cached results · {window}")
        return embed

    async def cog_load(self):
        if self.bot.is_ready() and not self.refresh_requests.is_running():
            self.refresh_requests.start()

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.refresh_requests.is_running():
            self.refresh_requests.start()

    def cog_unload(self):
        self.refresh_requests.cancel()

    @tasks.loop(seconds=15)
    async def refresh_requests(self):
        try:
            await asyncio.to_thread(self.service.process_next)
        except httpx.TransportError:
            self.transport_failed = True
            logger.exception("research_refresh_transport_failed")
            await self.staff_alert(
                "research_refresh_transport",
                "Website research worker could not reach Supabase after 3 connection attempts; polling resumes in 15 seconds. "
                "Queue claims are replay-safe and no provider request was retried. Check logs if this repeats.")
        except Exception:
            logger.exception("research_refresh_worker_failed")
            await self.staff_alert("research_refresh_worker", "Website research refresh worker failed. Check bot logs and the migration.")
        else:
            if self.transport_failed:
                self.transport_failed = False
                logger.info("research_refresh_connection_recovered")
                await self.staff_alert("research_refresh_recovered", "Website research worker Supabase connection recovered; polling is working again.")

    @refresh_requests.before_loop
    async def before_refresh(self):
        await self.bot.wait_until_ready()
