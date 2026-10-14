from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageDraw

from src.services.api_sports_multi_service import TRACKER_TIMEZONE
from src.services.data_model_service import ModelGame
from src.services.model_engine import ModelProjection
from src.services import model_presentation as text
from src.services.tracker_image_service import _font, _plain_text

WIDTH, HEIGHT = 1200, 800
PADDING = 64
WHITE = (248, 251, 255, 255)
MUTED = (176, 194, 208, 255)
ACCENT = (104, 229, 194, 255)
GOLD = (245, 200, 92, 255)
PURPLE = (145, 70, 191, 255)
PANEL = (16, 30, 44, 235)
OUTLINE = (255, 255, 255, 60)
LINE_WIDTH = WIDTH - PADDING * 2


def _fit(draw: ImageDraw.ImageDraw, value: str, size: int, max_width: int, bold: bool = True):
    while size > 16 and draw.textlength(value, font=_font(size, bold)) > max_width:
        size -= 1
    return _font(size, bold)


def _clock(value) -> str:
    return f"{value:%I:%M %p}".lstrip("0")


def _line(draw: ImageDraw.ImageDraw, y: int, value: str, size: int = 22, fill=MUTED, bold: bool = False) -> None:
    value = _plain_text(value)
    draw.text((PADDING, y), value, font=_fit(draw, value, size, LINE_WIDTH, bold), fill=fill)


def render_model_card(league: str, game: ModelGame, projection: ModelProjection | None,
                      backtest: dict | None = None) -> BytesIO:
    canvas = Image.new("RGB", (WIDTH, HEIGHT), (9, 18, 28))
    draw = ImageDraw.Draw(canvas, "RGBA")
    for y in range(HEIGHT):
        shade = int(28 * y / HEIGHT)
        draw.line((0, y, WIDTH, y), fill=(9 + shade // 2, 18 + shade // 3, 28 + shade))
    draw.rectangle((0, 0, WIDTH, 10), fill=PURPLE)

    start = game.start.astimezone(TRACKER_TIMEZONE)
    draw.text((PADDING, 40), f"{league.upper()} \u00b7 PLAYMAKER RATINGS MODEL", font=_font(26, True), fill=ACCENT)
    kickoff = f"{start:%a, %b} {start.day}, {start:%Y} \u00b7 {_clock(start)} ET"
    draw.text((PADDING, 80), kickoff, font=_font(24), fill=MUTED)

    away, home = _plain_text(game.away), _plain_text(game.home)
    column = (WIDTH - PADDING * 2 - 40) // 2
    for index, (label, name, score, probability) in enumerate((
        ("AWAY", away, projection and projection.away_score, projection and 1 - projection.home_probability),
        ("HOME", home, projection and projection.home_score, projection and projection.home_probability),
    )):
        x = PADDING + index * (column + 40)
        draw.rounded_rectangle((x, 140, x + column, 440), radius=24, fill=PANEL, outline=OUTLINE, width=2)
        draw.text((x + 28, 160), label, font=_font(20, True), fill=MUTED)
        draw.text((x + 28, 192), name, font=_fit(draw, name, 38, column - 56), fill=WHITE)
        if projection is None:
            continue
        draw.text((x + 28, 248), text.one_decimal(score), font=_font(96, True), fill=WHITE)
        draw.text((x + 28, 372), "WIN PROBABILITY", font=_font(18, True), fill=MUTED)
        bar_left, bar_right = x + 220, x + column - 28
        draw.rounded_rectangle((bar_left, 374, bar_right, 396), radius=11, fill=(255, 255, 255, 40))
        filled = bar_left + int((bar_right - bar_left) * probability)
        if filled > bar_left + 10:
            draw.rounded_rectangle((bar_left, 374, filled, 396), radius=11, fill=ACCENT)
        draw.text((x + 28, 398), text.percent(probability), font=_font(30, True), fill=ACCENT)

    if projection is None:
        _line(draw, 470, "No projection available", 30, WHITE, True)
        _line(draw, 514, "Requires 3+ prior scored games per team and 30+ league games. No probability was manufactured.")
        _line(draw, 560, text.track_record_line(backtest))
    else:
        _line(draw, 462, text.summary_line(game, projection), 28, WHITE, True)
        y = 506
        for index, value in enumerate(text.market_lines(game, projection)):
            _line(draw, y, value, 22, GOLD if index else WHITE, bool(index))
            y += 36
        for value in (text.factor_line(game, projection), text.conditions_line(game, projection)):
            if value:
                _line(draw, y, value)
                y += 36
        _line(draw, y, text.track_record_line(backtest))
        updated = projection.updated.astimezone(TRACKER_TIMEZONE)
        _line(draw, y + 36, f"{text.samples_line(game, projection)}   |   History synced {updated:%b} {updated.day}, {_clock(updated)} ET")

    _line(draw, HEIGHT - 50, text.METHOD, 19)
    draw.text((WIDTH - PADDING - draw.textlength("PLAYMAKER", font=_font(19, True)), 40), "PLAYMAKER",
              font=_font(19, True), fill=MUTED)

    output = BytesIO()
    canvas.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output
