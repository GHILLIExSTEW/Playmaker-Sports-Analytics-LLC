import asyncio
from io import BytesIO
from types import SimpleNamespace

import discord
from PIL import Image

import src.bot as bot_module
from src.services.tracker_image_service import _plain_text, render_tracker_image
from src.reporting import Reporting
from unittest.mock import AsyncMock, Mock


def test_decorative_display_names_render_as_readable_text():
    assert _plain_text("𝓡⃞  o⃞  B⃞  i⃞  N⃞📚") == "R o B i N📚"
    assert _plain_text("José") == "José"


def test_tracker_image_has_transparency_and_contains_a_breakdown_section():
    image_data = render_tracker_image(
        "Results for September 27, 2026",
        [
            ("⏳ Pending Bets", "2 bets"),
            ("📅 Monthly Units", "+8u"),
            ("🗓️ Yearly Units", "+21u"),
            ("🏆 Playmaker Breakdown", "**Capper** · 4-1 · +3u · 80%\n\n**Another** · 2-2 · +1u · 50%"),
        ],
        "Auto-updates hourly • Eastern Time",
    )

    rendered = Image.open(BytesIO(image_data.getvalue()))
    alpha = rendered.getchannel("A")
    assert rendered.size[0] == 1200
    assert alpha.getpixel((0, 0)) < 255
    assert alpha.getextrema()[1] == 255


def test_tracker_refresh_replaces_image_on_existing_summary_message():
    message = SimpleNamespace(
        author=bot_module.bot.user,
        embeds=[discord.Embed(title="Playmaker Picks | Unit Summary")],
    )

    async def edit(**kwargs):
        message.edit_kwargs = kwargs

    message.edit = edit

    class Channel:
        async def history(self, limit):
            yield message

    embed = discord.Embed(title="Playmaker Picks | Unit Summary", description="Results for today")
    embed.add_field(name="Pending Bets", value="3 bets", inline=True)
    embed.add_field(name="Monthly Units", value="-3u", inline=True)
    embed.set_footer(text="Auto-updates hourly")
    cog = Reporting(
        bot_module.bot, features=Mock(), rankings=Mock(),
        get_tracker_start=Mock(return_value=None), settlement_channels=Mock(return_value=[]),
        reconcile_reactions=AsyncMock(), tracked_operators=AsyncMock(), resolve_channel=AsyncMock(),
        can_manage_plays=Mock(), staff_alert=AsyncMock(),
    )
    asyncio.run(cog.update_or_post_tracker_embed(Channel(), embed, BytesIO(b"png data")))

    assert message.edit_kwargs["embed"].image.url == "attachment://unit-summary.png"
    assert message.edit_kwargs["embed"].description == "Results for today"
    assert [(field.name, field.value) for field in message.edit_kwargs["embed"].fields] == [
        ("Pending Bets", "3 bets"),
        ("Monthly Units", "-3u"),
    ]
    assert message.edit_kwargs["embed"].footer.text == "Auto-updates hourly"
    assert len(message.edit_kwargs["attachments"]) == 1
    assert message.edit_kwargs["attachments"][0].filename == "unit-summary.png"