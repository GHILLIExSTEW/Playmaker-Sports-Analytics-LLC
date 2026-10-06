from __future__ import annotations

from functools import lru_cache
from io import BytesIO
from pathlib import Path
import re
import unicodedata

from PIL import Image, ImageDraw, ImageFont, ImageOps


BACKGROUND_PATH = Path(__file__).resolve().parents[1] / "Media" / "Growth.png"
IMAGE_WIDTH = 1200
HORIZONTAL_PADDING = 72
CARD_GAP = 24
DECORATIVE_MARKS = "\u20dd\u20de\u20df\u20e2\u20e3\u20e4\u20e5\u20e6"


@lru_cache(maxsize=8)
def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        (r"C:\Windows\Fonts\seguisb.ttf", r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\arialbd.ttf")
        if bold
        else (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\arial.ttf")
    )
    for font_path in candidates:
        if Path(font_path).exists():
            return ImageFont.truetype(font_path, size=size)

    linux_font = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    try:
        return ImageFont.truetype(linux_font, size=size)
    except OSError:
        return ImageFont.load_default(size=size)


def _plain_text(value: str) -> str:
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", value)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(character for character in text if character not in DECORATIVE_MARKS)
    text = unicodedata.normalize("NFC", text)
    return re.sub(r"\s+", " ", text).strip()


def render_tracker_image(description: str, fields: list[tuple[str, str]], footer: str) -> BytesIO:
    cards_top = 190
    cards_height = 164
    footer_height = 86
    image_height = cards_top + cards_height + footer_height + 40
    image_size = (IMAGE_WIDTH, image_height)

    source = Image.open(BACKGROUND_PATH).convert("RGBA")
    # Cover fills the frame, then the contained copy sits on top so the whole artwork stays visible.
    background = ImageOps.fit(source, image_size, method=Image.Resampling.LANCZOS)
    inset = ImageOps.contain(source, image_size, method=Image.Resampling.LANCZOS)
    background.alpha_composite(
        inset,
        ((image_size[0] - inset.width) // 2, (image_size[1] - inset.height) // 2),
    )
    background.putalpha(62)
    canvas = Image.new("RGBA", image_size, (9, 20, 31, 0))
    canvas.alpha_composite(background)
    canvas.alpha_composite(Image.new("RGBA", image_size, (8, 18, 28, 164)))
    draw = ImageDraw.Draw(canvas, "RGBA")

    white = (248, 251, 255, 255)
    muted = (196, 211, 222, 255)
    accent = (104, 229, 194, 255)
    draw.text((HORIZONTAL_PADDING, 54), "UNIT TRACKER", font=_font(27, bold=True), fill=accent)
    draw.text((HORIZONTAL_PADDING, 101), _plain_text(description), font=_font(36, bold=True), fill=white)

    card_width = (IMAGE_WIDTH - HORIZONTAL_PADDING * 2 - CARD_GAP * 2) // 3
    for index, (name, value) in enumerate(fields[:3]):
        x = HORIZONTAL_PADDING + index * (card_width + CARD_GAP)
        y = cards_top
        draw.rounded_rectangle(
            (x, y, x + card_width, y + cards_height),
            radius=22,
            fill=(8, 19, 29, 190),
            outline=(255, 255, 255, 92),
            width=2,
        )
        label = name.replace("⏳", "").replace("📅", "").replace("🗓️", "").strip().upper()
        draw.text((x + 22, y + 20), label, font=_font(21, bold=True), fill=muted)
        value_text = _plain_text(value)
        value_font_size = 51
        while value_font_size > 30 and draw.textbbox((0, 0), value_text, font=_font(value_font_size, bold=True))[2] > card_width - 44:
            value_font_size -= 2
        draw.text((x + 22, y + 69), value_text, font=_font(value_font_size, bold=True), fill=white)

    draw.text((HORIZONTAL_PADDING, image_height - 54), _plain_text(footer), font=_font(19), fill=muted)
    output = BytesIO()
    canvas.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output