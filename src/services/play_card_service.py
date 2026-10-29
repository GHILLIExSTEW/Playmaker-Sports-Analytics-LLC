from __future__ import annotations


import asyncio
import base64
from io import BytesIO
import logging
import math
import unicodedata
from urllib.parse import urlparse

import discord
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps, UnidentifiedImageError
import requests

from src.services.play_features_service import GRADABLE_SPORTS, PlayFeaturesService, clean_leg_details
from src.services.play_service import PlayService
from src.services.settlement_service import SettlementService
from src.services.supabase_service import supabase_service
from src.services.tracker_image_service import _font, _wrap_text

logger = logging.getLogger("official_play_bot")
MAX_ASSET_BYTES = 12 * 1024 * 1024
CARD_PREFIX = "playmaker-card-"


def wrap_card_text(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    lines = []
    for wrapped in _wrap_text(draw, text, font, width):
        current = ""
        for character in wrapped:
            if current and draw.textlength(current + character, font=font) > width:
                lines.append(current)
                current = ""
            current += character
        if current:
            lines.append(current)
    return lines


def image_bytes(data: bytes) -> bytes:
    if len(data) > MAX_ASSET_BYTES:
        raise ValueError("Play-card artwork exceeds the 12 MB limit.")
    with Image.open(BytesIO(data)) as image:
        if image.width * image.height > 24_000_000:
            raise ValueError("Play-card artwork exceeds the 24-megapixel limit.")
        image.verify()
    return data


def download_artwork(url: str) -> bytes:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in {None, 443}
        or parsed.hostname not in {"media.api-sports.io", "cdn.discordapp.com", "media.discordapp.net"}
    ):
        raise ValueError("Play-card artwork must use the API-Sports or Discord image CDN.")
    with requests.get(url, stream=True, timeout=(5, 15), allow_redirects=False) as response:
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError("Play-card artwork returned an unexpected HTTP status.")
        data = bytearray()
        for chunk in response.iter_content(65536):
            data.extend(chunk)
            if len(data) > MAX_ASSET_BYTES:
                raise ValueError("Play-card artwork exceeds the 12 MB limit.")
    return image_bytes(bytes(data))


def player_artwork(payload, selection: str) -> dict[str, str]:
    candidates: dict[str, str] = {}
    selection = " ".join(selection.casefold().split())

    def visit(value, depth=0):
        if depth > 8:
            return
        if isinstance(value, list):
            for child in value:
                visit(child, depth + 1)
        elif isinstance(value, dict):
            player = value.get("player")
            if isinstance(player, dict) and isinstance(player.get("name"), str):
                name = " ".join(player["name"].casefold().split())
                if len(name.split()) >= 2 and selection.startswith(name + " "):
                    photo = player.get("photo") or player.get("image")
                    candidates[name] = photo if isinstance(photo, str) else ""
            for key, child in value.items():
                if key != "player":
                    visit(child, depth + 1)

    visit(payload)
    return candidates


def render_matchup_badge(away: bytes, home: bytes, size: int = 480) -> bytes:
    """Away logo top-left, home logo bottom-right, split by a slash they fade into."""
    scale = 2
    side = size * scale
    gradient = Image.linear_gradient("L").resize((side, side), Image.Resampling.BILINEAR)
    # 0 at the top-left corner, ~128 on the slash, 255 at the bottom-right corner.
    diagonal = ImageChops.add(gradient, gradient.transpose(Image.Transpose.TRANSPOSE), scale=2)
    gap, fade = 4, 48

    def mask(distance):
        return max(0, min(255, round((distance - gap) * 255 / fade)))

    masks = (
        Image.eval(diagonal, lambda value: mask(127.5 - value)),
        Image.eval(diagonal, lambda value: mask(value - 127.5)),
    )
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    box = round(side * 0.66)
    for data, center, fade_mask in ((away, 0.34, masks[0]), (home, 0.66, masks[1])):
        with Image.open(BytesIO(data)) as image:
            logo = ImageOps.contain(ImageOps.exif_transpose(image).convert("RGBA"), (box, box), Image.Resampling.LANCZOS)
        layer = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        layer.paste(logo, (round(side * center - logo.width / 2), round(side * center - logo.height / 2)))
        layer.putalpha(ImageChops.multiply(layer.getchannel("A"), fade_mask))
        canvas.alpha_composite(layer)
    start, end = (round(side * 0.1), round(side * 0.9)), (round(side * 0.9), round(side * 0.1))
    glow = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    ImageDraw.Draw(glow).line((start, end), fill=(85, 184, 237, 200), width=round(side * 0.06))
    canvas.alpha_composite(glow.filter(ImageFilter.GaussianBlur(side * 0.02)))
    slash = ImageDraw.Draw(canvas)
    slash.line((start, end), fill=(245, 247, 251, 255), width=round(side * 0.03))
    for point in (start, end):
        radius = side * 0.015
        slash.ellipse((point[0] - radius, point[1] - radius, point[0] + radius, point[1] + radius), fill=(245, 247, 251, 255))
    output = BytesIO()
    canvas.resize((size, size), Image.Resampling.LANCZOS).save(output, format="PNG")
    return output.getvalue()


def render_play_card(play: dict, slip: bytes | None, badge: bytes | None, badge_label: str) -> BytesIO:
    width, padding, text_width = 1200, 40, 790
    background, panel, white, muted = "#101925", "#182737", "#f5f7fb", "#b9cadb"
    status = str(play["status"])
    accent = "#34d399" if status == "win" else "#fbbf24" if status in {"loss", "void", "partial", "regraded", "not_official"} else "#55b8ed"
    title_font, body_font, small_font = _font(38, True), _font(28), _font(24)
    canvas = Image.new("RGB", (width, 1), background)
    draw = ImageDraw.Draw(canvas)
    details = [
        (f"PLAY #{play['id']}  |  {status.replace('_', ' ').upper()}", title_font, white),
        (f"Stake: {float(play['units']):g}u    Odds: {int(play['odds']):+d}", body_font, white),
    ]
    if play.get("team_approval") and slip:
        details.append(("Original slip above: capper's personal wager, not the official team stake.", small_font, muted))
    amount = (
        f"Net profit: {SettlementService.tally_for_result(status, float(play['units']), play['odds']):+g}u"
        if status in {"win", "loss", "partial", "void"}
        else f"To win: {PlayService.calculate_to_win(float(play['units']), int(play['odds'])):g}u"
        if status in {"open", "regraded"} else "Not an official play"
    )
    details.append((amount, body_font, accent))
    if play.get("stars"):
        details.append(("Team rating:", body_font, "#fbbf24"))
    for selection in str(play.get("play_text") or "Selections are recorded in the original play.").splitlines():
        details.extend((line, body_font, white) for line in wrap_card_text(draw, selection, body_font, text_width))
    rows = [(line, font, color) for text, font, color in details
            for line in wrap_card_text(draw, text, font, text_width)]
    text_height = sum(font.size + 14 for _, font, _ in rows)
    slip_image = None
    slip_height = 140
    if slip:
        with Image.open(BytesIO(slip)) as image:
            original = ImageOps.exif_transpose(image).convert("RGB")
            slip_height = max(1, round(original.height * width / original.width))
            if slip_height > 10000:
                raise ValueError("The slip is too tall for a readable combined card. Staff review is required.")
            slip_image = original.resize((width, slip_height), Image.Resampling.LANCZOS)
    badge_lines = wrap_card_text(draw, unicodedata.normalize("NFKC", badge_label), small_font, 240)
    panel_height = max(360, text_height + padding * 2, 330 + len(badge_lines) * 30)
    canvas = Image.new("RGB", (width, slip_height + panel_height + 64), background)
    draw = ImageDraw.Draw(canvas)
    if slip_image:
        canvas.paste(slip_image, (0, 0))
    else:
        draw.text((padding, 50), "No original slip image available", font=body_font, fill=muted)
    draw.rectangle((0, slip_height, width, slip_height + panel_height), fill=panel)
    draw.rectangle((0, slip_height, width, slip_height + 5), fill=accent)
    y = slip_height + padding
    for text, font, color in rows:
        draw.text((padding, y), text, font=font, fill=color)
        if text == "Team rating:":
            start = padding + draw.textlength(text + " ", font=font)
            for index in range(int(play["stars"])):
                cx, cy = start + 14 + index * 30, y + font.size * 0.65
                points = [
                    (
                        cx + (13 if vertex % 2 == 0 else 6) * math.cos(-math.pi / 2 + vertex * math.pi / 5),
                        cy + (13 if vertex % 2 == 0 else 6) * math.sin(-math.pi / 2 + vertex * math.pi / 5),
                    )
                    for vertex in range(10)
                ]
                draw.polygon(points, fill=color)
        y += font.size + 14
    if badge:
        with Image.open(BytesIO(badge)) as image:
            logo = ImageOps.contain(ImageOps.exif_transpose(image).convert("RGBA"), (240, 240), Image.Resampling.LANCZOS)
        canvas.paste(logo, (920 + (240 - logo.width) // 2, slip_height + padding), logo)
    for index, line in enumerate(badge_lines):
        draw.text((920, slip_height + 290 + index * 30), line, font=small_font, fill=muted)
    branding = "CAPPER PLAYS  |  CAPPER TRACKING" if play.get("tracking_scope") == "capper" else "PLAYMAKER PICKS  |  OFFICIAL TEAM TRACKING"
    draw.text((padding, canvas.height - 46), branding, font=small_font, fill=muted)
    result = BytesIO()
    canvas.save(result, format="PNG", optimize=True)
    result.seek(0)
    return result


class PlayCardService:
    def __init__(self, database=None) -> None:
        self.db = database or supabase_service
        self.features = PlayFeaturesService(self.db)

    def load(self, play_id: int) -> dict | None:
        rows = self.db.select("play_card_assets", "*", {"play_id": play_id}).data or []
        return rows[0] if rows else None

    def artwork(self, play: dict, legs: list[dict]) -> tuple[str | None, str]:
        if len(legs) != 1:
            return None, "Playmaker Picks"
        leg = legs[0]
        details = clean_leg_details(leg.get("details") or {})
        if not details:
            return None, "Playmaker Picks"
        found = self.features.find_event(details, play.get("created_at", "")) if details.get("event_date") or play.get("created_at") else None
        if found is None and (details.get("event_date") or play.get("created_at")):
            # OCR can mislabel the sport. Artwork alone may recover it from one
            # unambiguous full matchup/date; do not change the grading metadata.
            matches = []
            for sport in sorted(GRADABLE_SPORTS - {details["sport"]}):
                candidate = {**details, "sport": sport}
                match = self.features.find_event(candidate, play.get("created_at", ""))
                if match is not None:
                    matches.append((candidate, match))
            if len(matches) == 1:
                details, found = matches[0]
        if found is None:
            # Artwork may use a unique cached team logo without a matching game;
            # settlement must still require its stricter event/date match.
            if details.get("market") in {"moneyline", "spread"} and details.get("side") in {"home", "away"}:
                url, label = self.cached_team_artwork(details["sport"], details.get(f"{details['side']}_name"))
                if url:
                    return url, label
            matchup = self.matchup_artwork(details)
            return matchup if matchup[0] else (None, "Playmaker Picks")
        event, flipped = found
        sport = details["sport"]
        players = self.db.select("api_sports_player_game_stats", "payload", {
            "sport_slug": sport, "game_id": int(event["id"]),
        }).data or []
        candidates = player_artwork([row["payload"] for row in players], leg["selection"])
        if len(candidates) == 1:
            name, photo = next(iter(candidates.items()))
            if photo:
                return photo, name.title()
        side = details.get("side")
        if candidates or details.get("market") not in {"moneyline", "spread"} or side not in {"home", "away"}:
            return self.matchup_artwork(details, event)
        if flipped:
            side = "away" if side == "home" else "home"
        if sport == "nfl":
            rows = self.db.select("api_sports_nfl_games", f"{side}_team_logo", {"game_id": int(event["id"])}).data or []
            url = rows[0].get(f"{side}_team_logo") if rows else None
        else:
            rows = self.db.select("api_sports_events", f"{side}_logo", {"sport_slug": sport, "event_id": event["id"]}).data or []
            url = rows[0].get(f"{side}_logo") if rows else None
        return url, event[f"{side}_name"] if url else "Server icon"

    def matchup_artwork(self, details: dict, event: dict | None = None) -> tuple[tuple[str, str] | None, str]:
        """Both team logos for bets that are not about one side (totals, props)."""
        sport = details["sport"]
        away_name = (event or {}).get("away_name") or details.get("away_name")
        home_name = (event or {}).get("home_name") or details.get("home_name")
        away = home = None
        if event is not None:
            if sport == "nfl":
                rows = self.db.select("api_sports_nfl_games", "away_team_logo,home_team_logo", {"game_id": int(event["id"])}).data or []
                fields = "away_team_logo", "home_team_logo"
            else:
                rows = self.db.select("api_sports_events", "away_logo,home_logo", {"sport_slug": sport, "event_id": event["id"]}).data or []
                fields = "away_logo", "home_logo"
            if rows:
                away, home = rows[0].get(fields[0]), rows[0].get(fields[1])
        if not (away and home):
            away = away or self.cached_team_artwork(sport, away_name)[0]
            home = home or self.cached_team_artwork(sport, home_name)[0]
        if away and home and away != home and away_name and home_name:
            return (away, home), f"{away_name} @ {home_name}"
        return None, "Server icon"

    def cached_team_artwork(self, sport: str, name: str | None) -> tuple[str | None, str]:
        if not name or sport not in {"nfl", "ncaa", "basketball", "baseball", "hockey", "football", "other"}:
            return None, "Server icon"
        logos = set()
        for side in ("home", "away"):
            table = "api_sports_nfl_games" if sport == "nfl" else "api_sports_events"
            field = f"{side}_team_logo" if sport == "nfl" else f"{side}_logo"
            filters = {f"{side}_team_name" if sport == "nfl" else f"{side}_name": name}
            if sport not in {"nfl", "other"}:
                filters["sport_slug"] = sport
            rows = self.db.select(table, field, filters).data or []
            logos.update(row[field] for row in rows if row.get(field))
        return (next(iter(logos)), name) if len(logos) == 1 else (None, "Server icon")

    async def badge(self, play: dict, legs: list[dict]) -> tuple[bytes | None, str]:
        images = []
        seen = set()
        for leg in legs[:10]:
            url, label = await asyncio.to_thread(self.artwork, play, [leg])
            if not url or url in seen:
                continue
            seen.add(url)
            try:
                if isinstance(url, tuple):
                    away, home = [await asyncio.to_thread(download_artwork, item) for item in url]
                    data = await asyncio.to_thread(render_matchup_badge, away, home)
                else:
                    data = await asyncio.to_thread(download_artwork, url)
                images.append((data, label))
            except (requests.RequestException, ValueError, UnidentifiedImageError, OSError):
                logger.warning("play_card_api_artwork_unavailable play=%s label=%s", play["id"], label, exc_info=True)
        if not images:
            return None, "Server icon"
        if len(images) == 1:
            return images[0]
        columns = 2
        rows = (len(images) + columns - 1) // columns
        canvas = Image.new("RGBA", (480, rows * 240), (0, 0, 0, 0))
        for index, (data, _) in enumerate(images):
            with Image.open(BytesIO(data)) as image:
                image = image.convert("RGBA")
                image.thumbnail((220, 220), Image.Resampling.LANCZOS)
                canvas.alpha_composite(image, ((index % columns) * 240 + (240 - image.width) // 2,
                                               (index // columns) * 240 + (240 - image.height) // 2))
        output = BytesIO()
        canvas.save(output, format="PNG")
        return output.getvalue(), " / ".join(label for _, label in images)

    async def file(
        self, play: dict, guild: discord.Guild, *, slip: bytes | None = None,
        legs: list[dict] | None = None, source: discord.Message | None = None,
    ) -> discord.File:
        assets = await asyncio.to_thread(self.load, int(play["id"]))
        if assets is not None and legs:
            badge, label = await self.badge(play, legs)
            if badge is not None:
                assets = {**assets, "badge_base64": base64.b64encode(badge).decode("ascii"), "badge_label": label}
                await asyncio.to_thread(self.db.upsert, "play_card_assets", assets, ["play_id"])
                assets = await asyncio.to_thread(self.load, int(play["id"]))
                if assets is None:
                    raise RuntimeError("Updated play-card artwork could not be read back.")
        if assets is None:
            if slip is None and source is not None:
                attachment = next((item for item in source.attachments if (item.content_type or "").startswith("image/")), None)
                if attachment is not None:
                    if attachment.filename.startswith(CARD_PREFIX):
                        raise ValueError("Original slip artwork is missing from storage; cannot regenerate from a previously generated card.")
                    slip = await attachment.read()
                else:
                    url = next((embed.image.url for embed in source.embeds if embed.image and embed.image.url), None)
                    if url:
                        if urlparse(url).path.rsplit("/", 1)[-1].startswith(CARD_PREFIX):
                            raise ValueError("Original slip artwork is missing from storage; cannot regenerate from a previously generated card.")
                        slip = await asyncio.to_thread(download_artwork, url)
            if slip:
                await asyncio.to_thread(image_bytes, slip)
            badge, label = None, "Server icon"
            try:
                if legs:
                    badge, label = await self.badge(play, legs)
                else:
                    url, label = await asyncio.to_thread(self.artwork, play, [])
                    if url:
                        badge = await asyncio.to_thread(download_artwork, url)
            except (requests.RequestException, ValueError, UnidentifiedImageError, OSError):
                logger.warning("play_card_api_artwork_unavailable play=%s; using server icon", play["id"], exc_info=True)
            if badge is None:
                if guild.icon is None:
                    raise ValueError("The server has no icon to use as play-card artwork.")
                badge = await guild.icon.read()
                await asyncio.to_thread(image_bytes, badge)
                label = guild.name
            assets = {
                "play_id": int(play["id"]),
                "slip_base64": base64.b64encode(slip).decode("ascii") if slip else None,
                "badge_base64": base64.b64encode(badge).decode("ascii"), "badge_label": label,
            }
            await asyncio.to_thread(self.db.upsert, "play_card_assets", assets, ["play_id"])
            assets = await asyncio.to_thread(self.load, int(play["id"]))
            if assets is None:
                raise RuntimeError("Saved play-card artwork could not be read back.")
        output = await asyncio.to_thread(
            render_play_card, play,
            base64.b64decode(assets["slip_base64"], validate=True) if assets["slip_base64"] else None,
            base64.b64decode(assets["badge_base64"], validate=True), assets["badge_label"],
        )
        if output.getbuffer().nbytes > guild.filesize_limit:
            output.close()
            raise ValueError("Generated play card exceeds this server's upload limit. Staff review is required.")
        return discord.File(
            output, filename=f"{CARD_PREFIX}{play['id']}.png",
            description=(
                f"Play #{play['id']} - {play['status']}; {float(play['units']):g}u at {int(play['odds']):+d}. "
                f"{play.get('play_text') or ''}"
            )[:1024],
        )


play_card_service = PlayCardService()
