from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx
from discord.ext import commands, tasks

from src.config import API_SPORTS_BUDGET_ENABLED
from src.services.api_budget_service import ApiBudgetDenied, member_request_batch, member_request_user
from src.services.api_sports_multi_service import ApiSportsMultiService, DATE_PRODUCTS, TRACKER_TIMEZONE, model_history_seasons, normalize_event
from src.services.api_sports_service import ApiSportsService, normalize_game
from src.services.data_model_service import COMPETITIONS
from src.services.player_stats_service import PlayerStatsService
from src.services.supabase_service import supabase_service

logger = logging.getLogger("official_play_bot")


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
        # Claim is intentionally not automatically retried after an uncertain commit.
        rows = self.db._execute_once(
            lambda: self.db._ensure_client().rpc("claim_research_refresh", {}).execute()).data
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
        updated = self.db._execute_once(lambda: self.db._ensure_client().table("research_refresh_requests").update({
            "state": state, "result": result, "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", request["id"]).eq("state", "running").execute()).data
        if not updated:
            raise RuntimeError("Research refresh tracking changed; inspect the request before retrying.")
        return True


class ResearchWorkspace(commands.Cog):
    def __init__(self, bot, *, staff_alert, service=None):
        self.bot = bot
        self.staff_alert = staff_alert
        self.service = service or ResearchRefreshService()
        self.transport_failed = False

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
                "Website research worker lost its Supabase connection. Connection reset; polling resumes in 15 seconds. "
                "The uncertain operation was not retried. Check logs for any job needing reconciliation.")
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
