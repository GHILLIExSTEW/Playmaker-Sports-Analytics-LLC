from functools import lru_cache
from html import escape
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from shutil import copyfile

from PIL import Image as PILImage, ImageDraw, ImageFont
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image as RLImage, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "Playmaker_Picks_User_Guide.pdf"
DOCS_OUTPUT = ROOT / "docs" / "Playmaker_Bot_User_Guide.pdf"
SOURCE = ROOT / "Playmaker_Picks_User_Guide.html"
GUIDE_MIGRATION = ROOT / "supabase" / "migrations" / "20261011230000_operator_user_guide.sql"
EMOJI_FONT = r"C:\Windows\Fonts\seguiemj.ttf"
# Segoe UI Emoji stores its color layers at 109px; other sizes render flat.
EMOJI_RENDER_PX = 109


@lru_cache(maxsize=32)
def _emoji_png(character: str) -> bytes:
    font = ImageFont.truetype(EMOJI_FONT, EMOJI_RENDER_PX)
    canvas = PILImage.new("RGBA", (EMOJI_RENDER_PX * 2, EMOJI_RENDER_PX * 2), (255, 255, 255, 0))
    draw = ImageDraw.Draw(canvas)
    draw.text((EMOJI_RENDER_PX // 4, EMOJI_RENDER_PX // 4), character, font=font, embedded_color=True)
    bounds = canvas.getbbox()
    if bounds is None:
        raise ValueError(f"Emoji {character!r} rendered blank")
    buffer = BytesIO()
    canvas.crop(bounds).save(buffer, "PNG")
    return buffer.getvalue()


def emoji(character: str, size: float) -> RLImage:
    image = RLImage(BytesIO(_emoji_png(character)))
    ratio = image.imageHeight / image.imageWidth
    image.drawWidth = size
    image.drawHeight = size * ratio
    return image


pdfmetrics.registerFont(TTFont("SegoeUI", r"C:\Windows\Fonts\segoeui.ttf"))
pdfmetrics.registerFont(TTFont("SegoeUIEmoji", r"C:\Windows\Fonts\seguiemj.ttf"))

styles = getSampleStyleSheet()
styles.add(ParagraphStyle(
    name="TitleCustom", parent=styles["Title"], fontName="SegoeUI", fontSize=22,
    leading=26, textColor=colors.HexColor("#243b53"), alignment=TA_CENTER, spaceAfter=8,
))
styles.add(ParagraphStyle(
    name="Subtitle", parent=styles["Normal"], fontName="SegoeUI", fontSize=10,
    leading=14, textColor=colors.HexColor("#52606d"), alignment=TA_CENTER, spaceAfter=16,
))
styles.add(ParagraphStyle(
    name="Section", parent=styles["Heading2"], fontName="SegoeUI", fontSize=13,
    leading=17, textColor=colors.HexColor("#0b7285"), spaceBefore=8, spaceAfter=5,
))
styles.add(ParagraphStyle(
    name="BodyCustom", parent=styles["BodyText"], fontName="SegoeUI", fontSize=9.5,
    leading=14, textColor=colors.HexColor("#263238"), spaceAfter=4,
))
styles.add(ParagraphStyle(
    name="Small", parent=styles["BodyText"], fontName="SegoeUI", fontSize=8,
    leading=11, textColor=colors.HexColor("#52606d"),
))
styles.add(ParagraphStyle(
    name="Icon", parent=styles["BodyText"], fontName="SegoeUIEmoji", fontSize=16,
    leading=18, textColor=colors.HexColor("#0b7285"), alignment=TA_CENTER,
))
styles.add(ParagraphStyle(
    name="CellIcon", parent=styles["BodyText"], fontName="SegoeUIEmoji", fontSize=12,
    leading=15, textColor=colors.HexColor("#263238"), alignment=TA_CENTER,
))


def p(text, style="BodyCustom"):
    return Paragraph(text, styles[style])


def section(icon, title, body):
    table = Table([[emoji(icon, 0.24 * inch), p(f"<b>{title}</b>", "Section")]], colWidths=[0.35 * inch, 6.6 * inch])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return [table, p(body)]


def command_table(rows, headers, col_widths, icon_column=False):
    data = [[p(f"<b>{header}</b>", "Small") for header in headers]]
    for row in rows:
        cells = [p(cell, "Small") for cell in row]
        if icon_column:
            cells[0] = emoji(row[0], 0.2 * inch)
        data.append(cells)
    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e3f2f5")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#0b7285")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (0, 1), (0, -1), "CENTER" if icon_column else "LEFT"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c5d3da")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f9fa")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return [table, Spacer(1, 8)]


class GuideNode:
    def __init__(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tag = tag
        self.attrs = dict(attrs)
        self.children: list[GuideNode | str] = []

    def text(self) -> str:
        return "".join(child if isinstance(child, str) else child.text() for child in self.children)

    def elements(self, tag: str) -> list["GuideNode"]:
        return [child for child in self.children if isinstance(child, GuideNode) and child.tag == tag]

    def inline(self) -> str:
        content = "".join(escape(child) if isinstance(child, str) else child.inline() for child in self.children)
        if self.tag == "br":
            return "<br/>"
        if self.tag in {"b", "i"}:
            return f"<{self.tag}>{content}</{self.tag}>"
        if self.tag == "a":
            return f'<a href="{escape(self.attrs["href"] or "", quote=True)}" color="#0b7285">{content}</a>'
        return content


class GuideParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.root = GuideNode("document", [])
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = GuideNode(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in {"meta", "br"}:
            self.stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        if self.stack[-1].tag != tag:
            raise ValueError(f"Unexpected closing HTML tag: {tag}")
        self.stack.pop()

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def build_pdf() -> None:
    parser = GuideParser()
    parser.feed(SOURCE.read_text(encoding="utf-8"))
    parser.close()
    main = parser.root.elements("html")[0].elements("body")[0].elements("main")[0]
    header = main.elements("header")[0]
    doc = SimpleDocTemplate(
        str(OUTPUT), pagesize=letter, rightMargin=0.6 * inch,
        leftMargin=0.6 * inch, topMargin=0.5 * inch, bottomMargin=0.45 * inch,
        title="Playmaker Picks User Guide",
    )
    story = [
        p(header.elements("h1")[0].inline(), "TitleCustom"),
        p(header.elements("p")[0].inline(), "Subtitle"),
    ]
    for block in main.elements("section"):
        heading = block.elements("h2")[0]
        icon = heading.elements("span")[0]
        title = heading.text().removeprefix(icon.text()).strip()
        heading_table = section(icon.text(), escape(title), "")[0]
        heading_table.keepWithNext = True
        story.append(heading_table)
        for node in block.children:
            if isinstance(node, str) or node.tag == "h2":
                continue
            if node.tag in {"p", "div"}:
                story.append(p(node.inline()))
            elif node.tag == "ol":
                for index, item in enumerate(node.elements("li"), start=1):
                    story.append(p(f"{index}. {item.inline()}"))
            elif node.tag == "table":
                headers = node.elements("thead")[0].elements("tr")[0].elements("th")
                rows = node.elements("tbody")[0].elements("tr")
                has_icons = headers[0].text() == "Icon"
                widths = [0.55 * inch, inch, 5.4 * inch] if has_icons else [2.1 * inch, 4.85 * inch]
                content = [[cell.text() if has_icons and index == 0 else cell.inline()
                            for index, cell in enumerate(row.elements("td"))] for row in rows]
                story += command_table(content, [cell.text() for cell in headers], widths, icon_column=has_icons)
            else:
                raise ValueError(f"Unsupported guide block: {node.tag}")
    story.append(p(main.elements("footer")[0].inline(), "Small"))

    def page_number(canvas, document) -> None:
        canvas.saveState()
        canvas.setFont("SegoeUI", 8)
        canvas.setFillColor(colors.HexColor("#52606d"))
        canvas.drawRightString(7.9 * inch, 0.22 * inch, f"Page {document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=page_number, onLaterPages=page_number)
    copyfile(OUTPUT, DOCS_OUTPUT)
    guide_html = SOURCE.read_text(encoding="utf-8").split("<main>", 1)[1].split("</main>", 1)[0]
    delimiter = "$playmaker_guide$"
    if delimiter in guide_html:
        raise ValueError("Guide HTML contains its SQL dollar-quote delimiter.")
    sql_literal = delimiter + guide_html + delimiter
    GUIDE_MIGRATION.write_text(
        "-- Generated from Playmaker_Picks_User_Guide.html by scripts/create_user_guide_pdf.py.\n"
        "begin;\n\n"
        "create table if not exists public.operator_user_guide_content (\n"
        "  id boolean primary key default true check (id),\n"
        "  html text not null check (length(html) > 0)\n"
        ");\n"
        "alter table public.operator_user_guide_content enable row level security;\n"
        "revoke all on table public.operator_user_guide_content from public, anon, authenticated;\n\n"
        f"insert into public.operator_user_guide_content (id, html) values (true, {sql_literal})\n"
        "on conflict (id) do update set html = excluded.html;\n\n"
        "create or replace function public.operator_user_guide()\n"
        "returns text language plpgsql stable security definer set search_path = ''\n"
        "as $$\n"
        "declare content text;\n"
        "begin\n"
        "  perform public.require_operator_research_access();\n"
        "  select html into strict content from public.operator_user_guide_content where id;\n"
        "  return content;\n"
        "end;\n"
        "$$;\n"
        "revoke all on function public.operator_user_guide() from public, anon, authenticated;\n"
        "grant execute on function public.operator_user_guide() to authenticated;\n\n"
        "commit;\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    build_pdf()
    print(OUTPUT)
    print(DOCS_OUTPUT)
    print(GUIDE_MIGRATION)
