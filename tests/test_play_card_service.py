import asyncio
import base64
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from PIL import Image, ImageDraw
import pytest
import requests

from src.services.play_card_service import PlayCardService, download_artwork, player_artwork, render_play_card, wrap_card_text
from src.services.tracker_image_service import _font


def png(size=(600, 250), color="#ffffff"):
    output = BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return output.getvalue()


def play(**changes):
    return {
        "id": 232, "status": "open", "tracking_scope": "official", "units": 1, "odds": -138,
        "stars": 3, "play_text": "Leg 1: Purdue -2.5 (-138)", **changes,
    }


@pytest.mark.parametrize("size", [(600, 250), (400, 1000), (1600, 200)])
def test_generated_card_preserves_whole_slip_above_details_and_badge(size):
    slip = Image.new("RGB", size, "#ffffff")
    slip.putpixel((0, 0), (255, 0, 0))
    original = BytesIO()
    slip.save(original, format="PNG")
    result = render_play_card(play(), original.getvalue(), png((120, 120), "#ff00ff"), "Server")
    with Image.open(result) as card:
        slip_height = round(size[1] * 1200 / size[0])
        assert card.width == 1200
        assert card.height > slip_height + 360
        assert card.getpixel((600, slip_height - 3)) == (255, 255, 255)
        assert card.getpixel((1040, slip_height + 160)) == (255, 0, 255)
        # Text and badge occupy the lower panel, not the original slip area.
        assert card.getpixel((600, 10)) == (255, 255, 255)
        assert card.crop((30, slip_height + 25, 850, card.height - 64)).getextrema()[0][0] < 30


@pytest.mark.parametrize("status", ["open", "win", "loss", "void", "partial", "regraded", "not_official"])
def test_every_result_renders_and_keeps_details_on_one_width(status):
    output = render_play_card(play(status=status), png(), png((100, 100)), "Playmaker Picks")
    with Image.open(output) as image:
        assert image.width == 1200 and image.height > 800


def test_long_multileg_selections_expand_card_instead_of_truncating():
    short = render_play_card(play(), None, png(), "Server")
    long = render_play_card(play(play_text="\n".join(f"Leg {index}: " + "Very long selection name " * 8 for index in range(1, 11))), None, png(), "Server")
    assert Image.open(long).height > Image.open(short).height + 1000


def test_stylized_server_name_renders_like_supported_plain_letters():
    stylized = "".join(chr(0x1D400 + ord(character) - ord("A")) if character != " " else " "
                       for character in "PLAYMAKER PICKS")
    plain = render_play_card(play(), png(), png(), "PLAYMAKER PICKS")
    normalized = render_play_card(play(), png(), png(), stylized)
    assert normalized.getvalue() == plain.getvalue()


def test_capper_card_branding_never_claims_official_team_tracking(monkeypatch):
    original = ImageDraw.ImageDraw.text
    drawn = []

    def capture(self, xy, text, *args, **kwargs):
        drawn.append(text)
        return original(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture)
    render_play_card(play(tracking_scope="capper", stars=None), png(), png(), "Server")
    assert "CAPPER PLAYS  |  CAPPER TRACKING" in drawn
    assert not any("OFFICIAL TEAM" in text or "Team rating" in text for text in drawn)


@pytest.mark.parametrize("stars", [3, 4, 5])
def test_rating_is_rendered_as_gold_stars(monkeypatch, stars):
    original = ImageDraw.ImageDraw.polygon
    drawn = []

    def capture(self, xy, *args, **kwargs):
        drawn.append((xy, kwargs.get("fill")))
        return original(self, xy, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "polygon", capture)
    render_play_card(play(stars=stars), png(), png(), "Server")
    assert len(drawn) == stars
    assert all(len(points) == 10 and color == "#fbbf24" for points, color in drawn)


def test_very_long_words_never_overflow_details_or_badge_panel():
    draw = ImageDraw.Draw(Image.new("RGB", (1200, 1)))
    font = _font(28)
    for width in (240, 790):
        lines = wrap_card_text(draw, "x" * 500, font, width)
        assert all(draw.textlength(line, font=font) <= width for line in lines)
        assert "".join(lines) == "x" * 500


def test_player_matching_requires_full_unambiguous_name_and_keeps_missing_photo_fallback():
    payload = [{"players": [
        {"player": {"name": "John Smith", "photo": "https://media.api-sports.io/john.png"}},
        {"player": {"name": "Jane Smith", "photo": None}},
    ]}]
    assert player_artwork(payload, "John Smith over 20.5 points") == {"john smith": "https://media.api-sports.io/john.png"}
    assert player_artwork(payload, "Smith over 20.5 points") == {}
    assert player_artwork(payload, "Jane Smith over 20.5 points") == {"jane smith": ""}


@pytest.mark.parametrize("url", [
    "http://media.api-sports.io/logo.png", "https://localhost/logo.png",
    "https://media.api-sports.io.evil.example/logo.png",
    "https://user:password@media.api-sports.io/logo.png", "https://media.api-sports.io:8443/logo.png",
])
def test_artwork_downloads_reject_non_provider_hosts_before_network(monkeypatch, url):
    get = Mock()
    monkeypatch.setattr("src.services.play_card_service.requests.get", get)
    with pytest.raises(ValueError, match="CDN"):
        download_artwork(url)
    get.assert_not_called()


@pytest.fixture
def cards():
    saved = {}
    db = Mock()
    db.select.side_effect = lambda table, columns, filters: SimpleNamespace(data=[saved[filters["play_id"]]] if filters["play_id"] in saved else [])
    db.upsert.side_effect = lambda table, record, keys: saved.update({record["play_id"]: record})
    service = PlayCardService(db)
    service.artwork = Mock(return_value=(None, "Server icon"))
    icon_bytes = png((120, 120), "#663399")
    guild = SimpleNamespace(name="Playmaker Server", icon=SimpleNamespace(read=AsyncMock(return_value=icon_bytes)), filesize_limit=10 * 1024 * 1024)
    return SimpleNamespace(service=service, saved=saved, guild=guild, icon_bytes=icon_bytes)


def test_original_slip_and_icon_are_saved_privately_and_reused_without_nesting(cards):
    original = png((600, 250))
    first = asyncio.run(cards.service.file(play(), cards.guild, slip=original))
    assert first.filename == "playmaker-card-232.png"
    first_bytes = first.fp.getvalue()
    first.close()
    assert base64.b64decode(cards.saved[232]["slip_base64"]) == original
    assert base64.b64decode(cards.saved[232]["badge_base64"]) == cards.icon_bytes
    assert cards.saved[232]["badge_label"] == "Playmaker Server"
    second = asyncio.run(cards.service.file(play(status="win"), cards.guild))
    assert second.fp.getvalue() != first_bytes
    assert Image.open(BytesIO(second.fp.getvalue())).size == Image.open(BytesIO(first_bytes)).size
    cards.guild.icon.read.assert_awaited_once()
    second.close()


def test_player_photo_failure_explicitly_logs_and_uses_server_icon(cards, monkeypatch, caplog):
    cards.service.artwork.return_value = ("https://media.api-sports.io/photo.png", "Jane Smith")
    monkeypatch.setattr("src.services.play_card_service.download_artwork", Mock(side_effect=requests.ConnectionError("Unavailable")))
    file = asyncio.run(cards.service.file(play(), cards.guild, slip=png()))
    assert cards.saved[232]["badge_label"] == "Playmaker Server"
    assert "using server icon" in caplog.text
    file.close()


def test_api_player_artwork_is_used_when_available(cards, monkeypatch):
    badge = png((120, 120), "#22ff22")
    cards.service.artwork.return_value = ("https://media.api-sports.io/photo.png", "Jane Smith")
    monkeypatch.setattr("src.services.play_card_service.download_artwork", Mock(return_value=badge))
    file = asyncio.run(cards.service.file(play(), cards.guild, slip=png()))
    assert base64.b64decode(cards.saved[232]["badge_base64"]) == badge
    assert cards.saved[232]["badge_label"] == "Jane Smith"
    cards.guild.icon.read.assert_not_awaited()
    file.close()


def test_legacy_original_attachment_can_be_migrated_into_generated_card(cards):
    source = SimpleNamespace(attachments=[SimpleNamespace(
        content_type="image/png", filename="slip.png", read=AsyncMock(return_value=png()),
    )], embeds=[])
    file = asyncio.run(cards.service.file(play(), cards.guild, source=source))
    assert cards.saved[232]["slip_base64"]
    source.attachments[0].read.assert_awaited_once()
    file.close()


def test_missing_original_assets_never_render_generated_card_as_an_original_slip(cards):
    source = SimpleNamespace(attachments=[SimpleNamespace(content_type="image/png", filename="playmaker-card-232.png")], embeds=[])
    with pytest.raises(ValueError, match="Original slip artwork is missing"):
        asyncio.run(cards.service.file(play(), cards.guild, source=source))
    cards.service.db.upsert.assert_not_called()


def test_embed_only_generated_card_cannot_be_used_as_original_slip(cards, monkeypatch):
    download = Mock()
    monkeypatch.setattr("src.services.play_card_service.download_artwork", download)
    source = SimpleNamespace(attachments=[], embeds=[SimpleNamespace(image=SimpleNamespace(
        url="https://cdn.discordapp.com/attachments/1/2/playmaker-card-232-stars.png?ex=123",
    ))])
    with pytest.raises(ValueError, match="Original slip artwork is missing"):
        asyncio.run(cards.service.file(play(), cards.guild, source=source))
    download.assert_not_called()
    cards.service.db.upsert.assert_not_called()


def test_missing_server_icon_and_oversized_cards_fail_explicitly(cards):
    cards.guild.icon = None
    with pytest.raises(ValueError, match="server has no icon"):
        asyncio.run(cards.service.file(play(), cards.guild, slip=png()))
    cards.guild.icon = SimpleNamespace(read=AsyncMock(return_value=cards.icon_bytes))
    cards.guild.filesize_limit = 100
    with pytest.raises(ValueError, match="upload limit"):
        asyncio.run(cards.service.file(play(), cards.guild, slip=png()))


@pytest.mark.parametrize("sport,flipped", [("basketball", False), ("basketball", True), ("nfl", False)])
def test_team_artwork_uses_matched_event_and_selected_side(sport, flipped):
    db = Mock()
    service = PlayCardService(db)
    service.features.find_event = Mock(return_value=({"id": "15", "home_name": "Purdue", "away_name": "Washington"}, flipped))
    db.select.side_effect = lambda table, columns, filters: SimpleNamespace(
        data=[] if table == "api_sports_player_game_stats" else [{columns: "https://media.api-sports.io/logo.png"}],
    )
    leg = {
        "selection": "Purdue -2.5", "details": {
            "sport": sport, "home_name": "Purdue", "away_name": "Washington", "side": "home",
            "market": "spread", "event_date": "2026-10-16",
        },
    }
    url, label = service.artwork(play(), [leg])
    assert url == "https://media.api-sports.io/logo.png"
    assert label == ("Washington" if flipped else "Purdue")
    assert db.select.call_args.args[1] == ("home_team_logo" if sport == "nfl" else "away_logo" if flipped else "home_logo")


def test_api_player_without_photo_uses_server_icon_instead_of_unrelated_team_logo():
    db = Mock()
    service = PlayCardService(db)
    service.features.find_event = Mock(return_value=({"id": "15"}, False))
    db.select.return_value = SimpleNamespace(data=[{"payload": [{"player": {"name": "Jane Smith", "photo": None}}]}])
    leg = {"selection": "Jane Smith over 20.5 points", "details": {
        "sport": "basketball", "home_name": "A", "away_name": "B", "event_date": "2026-10-16",
    }}
    assert service.artwork(play(), [leg]) == (None, "Server icon")
    db.select.assert_called_once()


def test_team_logo_can_resolve_without_a_current_matching_game():
    db = Mock()
    service = PlayCardService(db)
    service.features.find_event = Mock(return_value=None)
    db.select.return_value = SimpleNamespace(data=[{"home_logo": "https://media.api-sports.io/guardians.png",
                                                   "away_logo": "https://media.api-sports.io/guardians.png"}])
    leg = {"selection": "Cleveland Guardians moneyline", "details": {
        "sport": "baseball", "market": "moneyline", "side": "home", "home_name": "Cleveland Guardians",
    }}
    assert service.artwork(play(), [leg]) == ("https://media.api-sports.io/guardians.png", "Cleveland Guardians")
    assert all(call.args[2]["sport_slug"] == "baseball" for call in db.select.call_args_list)


def test_conflicting_cached_logos_are_not_guessed():
    db = Mock()
    service = PlayCardService(db)
    db.select.return_value = SimpleNamespace(data=[{"home_logo": "https://media.api-sports.io/a.png",
                                                   "away_logo": "https://media.api-sports.io/b.png"}])
    assert service.cached_team_artwork("baseball", "Team") == (None, "Server icon")


def test_artwork_recovers_ocr_sport_only_from_unique_full_matchup_without_changing_leg():
    db = Mock()
    service = PlayCardService(db)
    service.features.find_event = Mock(side_effect=lambda details, created: (
        ({"id": "15", "home_name": "Coastal Carolina", "away_name": "Appalachian State"}, False)
        if details["sport"] == "ncaa" else None
    ))
    db.select.side_effect = lambda table, columns, filters: SimpleNamespace(
        data=[] if table == "api_sports_player_game_stats" else [{"home_logo": "https://media.api-sports.io/logo.png"}],
    )
    leg = {"selection": "Coastal Carolina +7.5", "details": {
        "sport": "other", "home_name": "Coastal Carolina", "away_name": "Appalachian State",
        "side": "home", "market": "spread", "event_date": "2026-10-16",
    }}
    assert service.artwork(play(), [leg]) == ("https://media.api-sports.io/logo.png", "Coastal Carolina")
    assert leg["details"]["sport"] == "other"
    assert db.select.call_args.args[2]["sport_slug"] == "ncaa"


def test_multileg_badge_includes_each_unique_available_logo(cards, monkeypatch):
    cards.service.artwork.side_effect = [
        ("https://media.api-sports.io/a.png", "A"), ("https://media.api-sports.io/b.png", "B"),
    ]
    monkeypatch.setattr("src.services.play_card_service.download_artwork", Mock(return_value=png()))
    badge, label = asyncio.run(cards.service.badge(play(), [{"selection": "A"}, {"selection": "B"}]))
    assert label == "A / B"
    assert Image.open(BytesIO(badge)).size == (480, 240)


def test_stored_server_fallback_upgrades_without_losing_original_slip(cards, monkeypatch):
    original = png()
    file = asyncio.run(cards.service.file(play(), cards.guild, slip=original))
    file.close()
    cards.service.artwork.return_value = ("https://media.api-sports.io/a.png", "Team")
    monkeypatch.setattr("src.services.play_card_service.download_artwork", Mock(return_value=png(color="#00ff00")))
    file = asyncio.run(cards.service.file(play(), cards.guild, legs=[{"selection": "Team"}]))
    assert cards.saved[232]["badge_label"] == "Team"
    assert base64.b64decode(cards.saved[232]["slip_base64"]) == original
    file.close()
