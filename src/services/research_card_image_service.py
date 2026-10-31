from __future__ import annotations

from io import BytesIO
import re
import textwrap

from PIL import Image, ImageDraw, ImageOps

from src.services.tracker_image_service import _font, _plain_text

WIDTH = 1200
PADDING = 64
WHITE = (248, 251, 255, 255)
MUTED = (176, 194, 208, 255)
ACCENT = (104, 229, 194, 255)
PURPLE = (145, 70, 191, 255)
PANEL = (16, 30, 44, 255)


def _wrapped(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    words = _plain_text(text).split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and draw.textlength(candidate, font=font) > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _logo(canvas: Image.Image, data: bytes | None, name: str, center: tuple[int, int]) -> None:
    diameter = 192
    left, top = center[0] - diameter // 2, center[1] - diameter // 2
    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.ellipse((left, top, left + diameter, top + diameter), fill=(255, 255, 255, 20),
                 outline=(255, 255, 255, 80), width=3)
    if data:
        with Image.open(BytesIO(data)) as source:
            image = ImageOps.contain(
                ImageOps.exif_transpose(source).convert("RGBA"),
                (diameter - 28, diameter - 28),
                Image.Resampling.LANCZOS,
            )
        canvas.alpha_composite(image, (center[0] - image.width // 2, center[1] - image.height // 2))
    else:
        initials = "".join(part[0] for part in _plain_text(name).split()[:2]).upper() or "?"
        font = _font(56, True)
        draw.text(
            (center[0] - draw.textlength(initials, font=font) / 2, center[1] - 34),
            initials, font=font, fill=WHITE,
        )


def render_research_card(
    *,
    away_name: str,
    home_name: str,
    heading: str,
    description: str,
    sections: list[tuple[str, str]],
    away_logo: bytes | None = None,
    home_logo: bytes | None = None,
) -> BytesIO:
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    body_font = _font(25)
    label_font = _font(20, True)
    wrapped_sections = []
    content_width = WIDTH - 2 * PADDING - 48
    for label, value in sections:
        lines = _wrapped(measure, value, body_font, content_width)
        wrapped_sections.append((label, lines or ["Unavailable"]))

    section_heights = [68 + 36 * len(lines) for _, lines in wrapped_sections]
    height = 510 + sum(section_heights) + max(0, len(section_heights) - 1) * 18
    height = min(max(height, 760), 1800)
    canvas = Image.new("RGBA", (WIDTH, height), (9, 18, 28, 255))
    draw = ImageDraw.Draw(canvas, "RGBA")
    for y in range(height):
        shade = int(25 * y / height)
        draw.line((0, y, WIDTH, y), fill=(9 + shade // 2, 18 + shade // 3, 28 + shade, 255))
    draw.rectangle((0, 0, WIDTH, 10), fill=PURPLE)

    draw.text((PADDING, 34), "PLAYMAKER PICKS  ·  RESEARCH", font=_font(23, True), fill=ACCENT)
    draw.text((PADDING, 72), _plain_text(heading)[:70], font=_font(40, True), fill=WHITE)
    desc_lines = _wrapped(draw, description, _font(20), WIDTH - 2 * PADDING)
    for index, line in enumerate(desc_lines[:2]):
        draw.text((PADDING, 126 + index * 30), line, font=_font(20), fill=MUTED)

    center_y = 292
    _logo(canvas, away_logo, away_name, (330, center_y))
    _logo(canvas, home_logo, home_name, (870, center_y))
    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.text((563, center_y - 20), "VS", font=_font(29, True), fill=ACCENT)
    for x, name in ((330, away_name), (870, home_name)):
        text = _plain_text(name)
        font = _font(28, True)
        while font.size > 18 and measure.textlength(text, font=font) > 400:
            font = _font(font.size - 1, True)
        draw.text((x - measure.textlength(text, font=font) / 2, center_y + 116),
                  text, font=font, fill=WHITE)

    y = 422
    for (label, lines), section_height in zip(wrapped_sections, section_heights):
        draw.rounded_rectangle(
            (PADDING, y, WIDTH - PADDING, y + section_height),
            radius=18, fill=PANEL, outline=(255, 255, 255, 40), width=2,
        )
        draw.text((PADDING + 24, y + 14), _plain_text(label)[:70],
                  font=label_font, fill=ACCENT)
        for index, line in enumerate(lines):
            draw.text((PADDING + 24, y + 42 + index * 36), line,
                      font=body_font, fill=WHITE)
        y += section_height + 18

    draw.text((PADDING, height - 46), "Cached research · not a prediction or betting advice",
              font=_font(18), fill=MUTED)
    output = BytesIO()
    canvas.convert("RGB").save(output, format="PNG", optimize=True)
    output.seek(0)
    return output


def player_report_sections(report: str) -> tuple[str, str, list[tuple[str, str]]]:
    lines = report.splitlines()
    player = re.sub(r"\*\*", "", lines[0]).strip() if lines else "Player research"
    description = lines[1] if len(lines) > 1 and lines[1].startswith("Sample:") else "Cached player research"
    sections = []
    current_label = None
    current_lines = []
    for line in lines[2:]:
        if line.startswith("**") and ":**" in line:
            if current_label is not None:
                sections.append((current_label, "\n".join(current_lines)))
            label, value = line.split(":**", 1)
            current_label = label.removeprefix("**")
            current_lines = [value.strip()]
        elif line.startswith("Available stats:"):
            if current_label is not None:
                sections.append((current_label, "\n".join(current_lines)))
                current_label, current_lines = None, []
            sections.append(("Available player statistics", line.removeprefix("Available stats:").strip()))
        elif line.startswith(("Selected game snapshot:", "<t:")):
            if current_label and current_label != "Recent observations":
                sections.append((current_label, "\n".join(current_lines)))
                current_label, current_lines = None, []
            if current_label is None:
                current_label, current_lines = "Recent observations", []
            current_lines.append(line)
    if current_label is not None:
        sections.append((current_label, "\n".join(current_lines)))
    if not sections:
        sections = [("Research", "\n".join(lines[2:]) or report or "No cached player stats available.")]
    return player, description, sections
