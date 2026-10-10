from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime

import discord

from src.services.play_features_service import (
    EASTERN, SETTLED_RESULTS, SPORT_NAMES, record_summary, settled_time, signed_units, unique_published,
)

PAGE_SIZE = 10
logger = logging.getLogger("official_play_bot")


def parse_results_dates(start: str | None, end: str | None) -> tuple[date | None, date | None]:
    try:
        first = date.fromisoformat(start) if start else None
        last = date.fromisoformat(end) if end else None
        if (start and first and first.isoformat() != start) or (end and last and last.isoformat() != end):
            raise ValueError()
    except ValueError as exc:
        raise ValueError("Dates must use YYYY-MM-DD, for example 2026-10-01.") from exc
    if first and last and first > last:
        raise ValueError("The start date must be on or before the end date.")
    return first, last


def filter_results(
    plays: list[dict], users: list[dict], sports: dict, *,
    operator_ids: set[int], start: date | None = None, end: date | None = None,
    sport: str | None = None, capper_id: int | None = None, result: str | None = None,
    now: datetime | None = None,
) -> list[dict]:
    now = now or datetime.now(EASTERN)
    authors = {str(user["id"]): str(user.get("discord_user_id") or "") for user in users}
    rows = []
    for play in unique_published(plays):
        author = authors.get(str(play["user_id"]), "")
        if not author.isdecimal() or int(author) not in operator_ids:
            continue
        if capper_id is not None and author != str(capper_id):
            continue
        if play.get("status") not in SETTLED_RESULTS or (result and play["status"] != result):
            continue
        slug = sports.get(play.get("sport_id"), "official")
        if sport and slug != sport:
            continue
        when = settled_time(play)
        if when > now or (start and when.date() < start) or (end and when.date() > end):
            continue
        rows.append(play)
    return sorted(rows, key=lambda play: (settled_time(play), int(play["id"])), reverse=True)


def build_results_embed(rows: list[dict], users: list[dict], sports: dict, label: str, page: int, links: dict[int, str]) -> discord.Embed:
    summary = record_summary(rows)
    embed = discord.Embed(title="Official Play Results", color=discord.Color.green())
    embed.description = (
        f"{label}\nCurrent OPERATOR authors · published official plays only\n**{len(rows)} settled plays** · "
        f"W{summary['wins']} L{summary['losses']} V{summary['voids']} P{summary['partials']}"
        f" · **{summary['net']:+.2f}u net**"
    )
    names = {str(user["id"]): user.get("display_name") or user.get("username") or str(user["id"]) for user in users}
    for play in rows[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]:
        play_id = int(play["id"])
        name = discord.utils.escape_markdown(str(names.get(str(play["user_id"]), "Unknown")))[:70]
        slug = sports.get(play.get("sport_id"), "official")
        sport = SPORT_NAMES.get(slug, "Unspecified")
        value = (
            f"{settled_time(play).strftime('%Y-%m-%d %H:%M')} ET · {sport}\n"
            f"{float(play['units']):g}u risked · {signed_units(play):+.2f}u net"
        )
        if play_id in links:
            value += f"\n[Original post]({links[play_id]})"
        embed.add_field(name=f"#{play_id} · {name} · {play['status'].upper()}", value=value, inline=False)
    if not rows:
        embed.description += "\nNo results match these filters."
    pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
    embed.set_footer(text=f"Page {page + 1}/{pages} • Eastern settlement dates • Snapshot; rerun /play_results for corrections")
    return embed


class ResultsView(discord.ui.View):
    def __init__(self, owner_id: int, rows: list[dict], users: list[dict], sports: dict, label: str, channels: list):
        super().__init__(timeout=600)
        self.owner_id, self.rows, self.users, self.sports = owner_id, rows, users, sports
        self.label, self.channels, self.page = label, channels, 0
        self.lock = asyncio.Lock()
        self.update_buttons()

    def update_buttons(self) -> None:
        self.previous.disabled = self.page == 0
        self.next_page.disabled = (self.page + 1) * PAGE_SIZE >= len(self.rows)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This results menu belongs to someone else. Run /play_results for your own.", ephemeral=True)
            return False
        return True

    async def embed(self, interaction: discord.Interaction) -> discord.Embed:
        links = {}
        for play in self.rows[self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE]:
            for channel in self.channels:
                permissions = channel.permissions_for(interaction.user)
                if not permissions.view_channel or not permissions.read_message_history:
                    continue
                try:
                    message = await channel.fetch_message(int(play["message_id"]))
                except (discord.NotFound, discord.Forbidden):
                    continue
                links[int(play["id"])] = message.jump_url
                break
        return build_results_embed(self.rows, self.users, self.sports, self.label, self.page, links)

    async def change_page(self, interaction: discord.Interaction, delta: int) -> None:
        await interaction.response.defer()
        async with self.lock:
            previous = self.page
            try:
                maximum = max(0, (len(self.rows) - 1) // PAGE_SIZE)
                self.page = min(maximum, max(0, self.page + delta))
                self.update_buttons()
                await interaction.edit_original_response(embed=await self.embed(interaction), view=self)
            except Exception:
                self.page = previous
                self.update_buttons()
                logger.exception("results_page_failed user=%s", interaction.user.id)
                await interaction.followup.send("Could not load that results page. Please retry.", ephemeral=True)

    @discord.ui.button(label="Newer", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.change_page(interaction, -1)

    @discord.ui.button(label="Older", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.change_page(interaction, 1)
