import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

import src.bot as bot_module
from src.member_bet_vault import MemberBetVault, card_embed, can_moderate, review_embed, ticket_file
from src.services.member_bet_service import (
    MemberBetService, MemberImageService, accepted_photo, grade_leg, sanitize_photo,
    validate_ticket,
)

@pytest.fixture(autouse=True)
def paid_membership_default(monkeypatch):
    membership = Mock()
    membership.has_vault_access.return_value = True
    monkeypatch.setattr("src.member_bet_vault.MembershipService", lambda: membership)
    return membership


def ticket(**overrides):
    row = {
        "id": 1, "owner_id": "10", "source_message_id": "100", "card_message_id": "200",
        "channel_id": "300", "status": "open", "units": 2, "odds": -110,
        "created_at": "2026-10-03T00:00:00+00:00",
        "updated_at": "2026-10-03T00:00:00+00:00",
        "confirmed_at": "2026-10-03T00:01:00+00:00",
        "attempts": 0, "verification": None, "review_reason": None,
        "event": {
            "sport": "nfl", "id": "123", "home_name": "Home", "away_name": "Away",
            "start_at": datetime.now(timezone.utc).isoformat(),
        },
        "details": {
            "game": "Home vs Away",
            "legs": [{
                "selection": "SECRET Over 45.5", "odds": -110, "sport": "nfl",
                "market": "total", "side": "over", "line": 45.5,
                "scope": "full_game_including_overtime",
                "event_date": "2026-10-03", "home_name": "Home", "away_name": "Away",
            }],
        },
    }
    return {**row, **overrides}


def message(attachments=None, channel=300):
    return SimpleNamespace(
        id=100, guild=SimpleNamespace(id=400), webhook_id=None,
        channel=SimpleNamespace(id=channel, send=AsyncMock()),
        author=SimpleNamespace(id=10, bot=False, display_name="Member", mention="<@10>", send=AsyncMock()),
        content="2u", attachments=attachments or [], delete=AsyncMock(),
    )


def photo_attachment(mime="image/png", size=100):
    return SimpleNamespace(content_type=mime, size=size, read=AsyncMock(return_value=photo_bytes()))


def photo_bytes():
    output = BytesIO()
    Image.new("RGB", (10, 10), "white").save(output, format="PNG")
    return output.getvalue()


@pytest.mark.parametrize("mime,expected", [
    ("image/png", True), ("image/jpeg", True), ("image/webp", True),
    ("image/gif", False), ("application/pdf", False), ("image/svg+xml", False), (None, False),
])
def test_accept_only_supported_photo_attachments(mime, expected):
    assert accepted_photo(photo_attachment(mime)) is expected


def test_photo_size_and_actual_content_validation():
    assert not accepted_photo(photo_attachment(size=0))
    assert not accepted_photo(photo_attachment(size=10 * 1024 * 1024 + 1))
    with pytest.raises(ValueError, match="readable photo"):
        sanitize_photo(b"not really a PNG")
    result = sanitize_photo(photo_bytes())
    with Image.open(BytesIO(result)) as image:
        assert image.format == "PNG"
        assert image.size == (10, 10)


def test_animation_is_not_a_photo():
    output = BytesIO()
    Image.new("RGB", (10, 10), "white").save(
        output, format="PNG", save_all=True,
        append_images=[Image.new("RGB", (10, 10), "black")],
    )
    with pytest.raises(ValueError, match="static"):
        sanitize_photo(output.getvalue())


def test_photo_metadata_is_removed():
    output = BytesIO()
    metadata = PngInfo()
    metadata.add_text("Private information", "Account owner")
    exif = Image.Exif()
    exif[0x010E] = "Private ticket metadata"
    Image.new("RGB", (10, 10), "white").save(output, format="PNG", pnginfo=metadata, exif=exif)
    with Image.open(BytesIO(sanitize_photo(output.getvalue()))) as sanitized:
        assert not sanitized.getexif()
        assert "Private information" not in sanitized.info


@pytest.mark.parametrize("units,odds", [
    (float("nan"), -110), (float("inf"), -110), (0, -110), (1, 0), (1, 50),
    (True, -110), (1, 2_147_483_648), (1e308, 1000),
])
def test_invalid_ticket_values_are_rejected(units, odds):
    with pytest.raises(ValueError):
        validate_ticket(units, odds)


def test_caption_cannot_convert_cash_into_units():
    parsed = {"units": None, "game": "Home vs Away", "legs": [{"selection": "Over 45.5", "odds": -110}]}
    MemberImageService._validate(parsed)
    assert parsed["units"] is None
    assert parsed["ticket_odds"] == -110


@pytest.mark.parametrize("state", ["processing", "draft", "open", "review"])
def test_unsettled_cards_never_expose_selection_or_photo(state):
    row = ticket(status=state)
    row["details"]["image_url"] = "https://example.com/private.png"
    embed = card_embed(row).to_dict()
    assert "SECRET" not in str(embed)
    assert "private.png" not in str(embed)
    assert "image" not in embed
    assert "Hidden until settled" in str(embed)


def test_draft_game_is_private_until_confirmed():
    row = ticket(status="draft", confirmed_at=None)
    row["details"]["game"] = "OCR MIGHT LEAK A SELECTION"
    assert "OCR MIGHT LEAK" not in str(card_embed(row).to_dict())


@pytest.mark.parametrize("result,net", [("win", "+1.82u"), ("loss", "-2u"), ("void", "+0u")])
def test_settled_cards_reveal_only_sanitized_selection(result, net):
    embed = card_embed(ticket(status=result, verification="Moderator-settled"))
    assert "SECRET" in str(embed.to_dict())
    assert any(field.name == "Net units" and field.value == net for field in embed.fields)
    assert any(field.name == "Verification" and field.value == "Moderator-settled" for field in embed.fields)
    assert not embed.image.url


@pytest.mark.parametrize("market,side,line,home,away,result", [
    ("total", "over", 45.5, 24, 23, "win"),
    ("total", "under", 45.5, 24, 23, "loss"),
    ("total", "over", 47, 24, 23, "void"),
    ("spread", "home", -3.5, 24, 23, "loss"),
    ("spread", "away", 3.5, 24, 23, "win"),
    ("spread", "home", -1, 24, 23, "void"),
    ("moneyline", "home", None, 24, 23, "win"),
    ("moneyline", "away", None, 24, 23, "loss"),
    ("moneyline", "home", None, 24, 24, None),
])
def test_supported_market_grading(market, side, line, home, away, result):
    leg = {**ticket()["details"]["legs"][0], "market": market, "side": side, "line": line}
    assert grade_leg(leg, {"status_code": "AOT", "home_score": home, "away_score": away}) == result


@pytest.mark.parametrize("change,event", [
    ({"scope": "unknown"}, {"status_code": "FT", "home_score": 30, "away_score": 20}),
    ({"market": "prop"}, {"status_code": "FT", "home_score": 30, "away_score": 20}),
    ({"sport": "hockey"}, {"status_code": "FT", "home_score": 30, "away_score": 20}),
    ({}, {"status_code": "CANC", "home_score": 0, "away_score": 0}),
    ({}, {"status_code": "Q4", "home_score": 30, "away_score": 20}),
    ({}, {"status_code": "FT", "home_score": None, "away_score": 20}),
])
def test_never_guess_an_unsupported_result(change, event):
    assert grade_leg({**ticket()["details"]["legs"][0], **change}, event) is None


def test_dict_scores_are_supported():
    assert grade_leg(ticket()["details"]["legs"][0], {
        "status_code": "FT", "home_score": {"total": 30}, "away_score": {"total": 20},
    }) == "win"


def test_no_photo_is_deleted_and_member_notified_in_channel_not_dm():
    service = Mock()
    vault = MemberBetVault(Mock(), 300, service)
    msg = message()
    asyncio.run(vault.handle_message(msg))
    msg.delete.assert_awaited_once()
    msg.channel.send.assert_awaited_once()
    msg.author.send.assert_not_awaited()
    assert "removed" in msg.channel.send.call_args.args[0]
    service.save_submission.assert_not_called()


def test_invalid_photo_bytes_deleted_and_member_notified():
    attachment = photo_attachment()
    attachment.read.return_value = b"fake image"
    vault = MemberBetVault(Mock(), 300, Mock())
    msg = message([attachment])
    asyncio.run(vault.handle_message(msg))
    msg.delete.assert_awaited_once()
    assert "rejected" in msg.channel.send.call_args.args[0]


def test_valid_photo_saved_before_deletion_and_card_contains_no_original():
    events = []
    service = Mock()

    def save(msg, data):
        events.append("saved")
        return ticket(status="processing")

    async def delete():
        events.append("deleted")

    async def publish(row):
        events.append("published")

    service.save_submission.side_effect = save
    service.update.return_value = ticket(status="processing", original_removed=True)
    vault = MemberBetVault(Mock(), 300, service)
    vault.publish = AsyncMock(side_effect=publish)
    msg = message([photo_attachment()])
    msg.delete.side_effect = delete
    asyncio.run(vault.handle_message(msg))
    assert events == ["saved", "deleted", "published"]


def test_notice_failure_is_logged(caplog):
    vault = MemberBetVault(Mock(), 300, Mock())
    msg = message()
    msg.channel.send.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "No permission")
    asyncio.run(vault.handle_message(msg))
    assert "member_vault_notice_failed" in caplog.text
    msg.author.send.assert_not_awaited()


def test_notice_mentions_member_and_self_deletes():
    msg = message()
    asyncio.run(MemberBetVault(Mock(), 300, Mock()).handle_message(msg))
    call = msg.channel.send.call_args
    assert call.args[0].startswith("<@10> ") and call.kwargs["delete_after"] == 60


def test_deletion_failure_is_logged_and_does_not_claim_removal(caplog):
    vault = MemberBetVault(Mock(), 300, Mock())
    msg = message()
    msg.delete.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "No permission")
    asyncio.run(vault.handle_message(msg))
    assert "member_vault_delete_failed" in caplog.text
    assert "could not be removed" in msg.channel.send.call_args.args[0]


@pytest.mark.parametrize("change", ["bot", "webhook", "dm", "other_channel"])
def test_unrelated_messages_ignored(change):
    msg = message()
    if change == "bot":
        msg.author.bot = True
    elif change == "webhook":
        msg.webhook_id = 12
    elif change == "dm":
        msg.guild = None
    else:
        msg.channel.id = 301
    asyncio.run(MemberBetVault(Mock(), 300, Mock()).handle_message(msg))
    msg.delete.assert_not_awaited()
    msg.author.send.assert_not_awaited()


def test_vault_channel_does_not_reach_official_parser_or_commands(monkeypatch):
    vault = SimpleNamespace(handle_message=AsyncMock())
    commands = AsyncMock()
    monkeypatch.setattr(bot_module, "MEMBER_BET_CHANNEL_ID", 300)
    monkeypatch.setattr(bot_module, "member_bet_vault", vault)
    monkeypatch.setattr(bot_module.bot, "process_commands", commands)
    asyncio.run(bot_module.on_message(message()))
    vault.handle_message.assert_awaited_once()
    commands.assert_not_awaited()


def test_card_controls_are_persistent():
    vault = MemberBetVault(Mock(), 300, Mock())
    assert vault.view.is_persistent()
    assert all(button.custom_id.startswith("member-vault:") for button in vault.view.children)


def test_members_cannot_settle():
    user = SimpleNamespace(id=10)
    assert not can_moderate(user)
    vault = MemberBetVault(Mock(), 300, Mock())
    interaction = SimpleNamespace(user=user, response=SimpleNamespace(send_message=AsyncMock()))
    asyncio.run(vault.view.settle.callback(interaction))
    assert interaction.response.send_message.call_args.kwargs["ephemeral"] is True
    vault.service.for_card.assert_not_called()


def test_other_members_cannot_read_hidden_selection():
    service = Mock()
    service.for_card.return_value = ticket()
    vault = MemberBetVault(Mock(), 300, service)
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=11), channel_id=300, message=SimpleNamespace(id=200),
        response=SimpleNamespace(defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()),
    )
    asyncio.run(vault.view.review.callback(interaction))
    assert "SECRET" not in str(interaction.followup.send.call_args)
    assert interaction.followup.send.call_args.kwargs["ephemeral"] is True


def test_confirmation_requires_owner_and_is_locked():
    service = MemberBetService(database=Mock())
    service.get = Mock(return_value=ticket(status="draft", confirmed_at=None))
    with pytest.raises(ValueError, match="Only the uploader"):
        service.confirm(1, 11, 2, -110)
    service.get.return_value = ticket(status="open")
    with pytest.raises(ValueError, match="Only the uploader"):
        service.confirm(1, 10, 2, -110)


def test_unsupported_ticket_routes_to_moderator_review():
    service = MemberBetService(database=Mock())
    row = ticket(status="draft", confirmed_at=None)
    service.get = Mock(return_value=row)
    service.match_event = Mock(return_value=None)
    service.update = Mock(side_effect=lambda bet_id, fields, status: {**row, **fields})
    confirmed = service.confirm(1, 10, 2, -110)
    assert confirmed["status"] == "review"
    assert confirmed["confirmed_at"]
    assert "moderator" in confirmed["review_reason"]


def test_parlay_never_auto_grades():
    row = ticket(status="draft", confirmed_at=None)
    row["details"]["legs"].append(deepcopy(row["details"]["legs"][0]))
    service = MemberBetService(database=Mock())
    service.get = Mock(return_value=row)
    service.match_event = Mock()
    service.update = Mock(side_effect=lambda bet_id, fields, status: {**row, **fields})
    assert service.confirm(1, 10, 2, -110)["status"] == "review"
    service.match_event.assert_not_called()


def test_api_failure_backoff_then_moderator_fallback_never_settles():
    service = MemberBetService(database=Mock())
    service.update = Mock()
    service.settle = Mock()
    row = ticket()
    service.retry(row, "API unavailable")
    fields = service.update.call_args.args[1]
    assert fields["status"] == "open"
    assert fields["attempts"] == 1
    assert datetime.fromisoformat(fields["next_check_at"]) > datetime.now(timezone.utc)
    row["attempts"] = 4
    service.retry(row, "API unavailable")
    assert service.update.call_args.args[1]["status"] == "review"
    service.settle.assert_not_called()


def test_suspended_event_stays_hidden_for_moderator_review():
    service = MemberBetService(database=Mock())
    service.update = Mock()
    service.settle = Mock()
    service.check(ticket(), {"status_code": "SUSP", "home_name": "Home", "away_name": "Away"})
    assert service.update.call_args.args[1]["status"] == "review"
    service.settle.assert_not_called()


def test_wrong_event_cannot_settle():
    service = MemberBetService(database=Mock())
    service.settle = Mock()
    with pytest.raises(ValueError, match="teams do not match"):
        service.check(ticket(), {"status_code": "FT", "home_name": "Wrong", "away_name": "Away", "home_score": 30, "away_score": 20})
    service.settle.assert_not_called()


def test_matching_final_event_settles_with_api_label():
    service = MemberBetService(database=Mock())
    service.settle = Mock()
    service.check(ticket(), {"status_code": "FT", "home_name": "Home", "away_name": "Away", "home_score": 30, "away_score": 20})
    assert service.settle.call_args.args[:4] == (1, "api-sports", "win", "API-verified")


def test_duplicate_source_submission_is_not_saved_twice():
    service = MemberBetService(database=Mock())
    service.for_source = Mock(return_value=ticket())
    assert service.save_submission(message(), photo_bytes())["id"] == 1
    service.db.insert.assert_not_called()
    service.db._ensure_client.assert_not_called()


def test_api_event_requests_shared_across_member_tickets():
    async def run():
        service = Mock()
        first, second = ticket(), ticket(id=2)
        service.pending.return_value = [first, second]
        service.get.side_effect = lambda bet_id: first if bet_id == 1 else second
        service.fetch_event.return_value = {"status_code": "Q1", "home_name": "Home", "away_name": "Away"}
        service.dirty.return_value = []
        vault = MemberBetVault(Mock(), 300, service)
        await vault.reconcile()
        service.fetch_event.assert_called_once()
        assert service.check.call_count == 2

    asyncio.run(run())


class EventQuery:
    def __init__(self, rows):
        self.rows = rows
        self.filters = []

    def select(self, *_args):
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def gte(self, key, value):
        self.filters.append(("gte", key, value))
        return self

    def lt(self, key, value):
        self.filters.append(("lt", key, value))
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


def test_match_uses_exact_names_date_and_unique_event():
    event = {
        "game_id": 123, "home_team_name": "Home", "away_team_name": "Away",
        "kickoff_at": "2026-10-03T23:00:00+00:00",
    }
    database = Mock()
    query = EventQuery([event])
    database._ensure_client.return_value.table.return_value = query
    service = MemberBetService(database=database)
    leg = ticket()["details"]["legs"][0]
    assert service.match_event(leg)["id"] == "123"
    assert query.filters == [
        ("gte", "kickoff_at", "2026-10-03T00:00:00-04:00"),
        ("lt", "kickoff_at", "2026-10-04T00:00:00-04:00"),
    ]
    query.rows = [event, {**event, "game_id": 124}]
    assert service.match_event(leg) is None
    query.rows = [{**event, "home_team_name": "Home College"}]
    assert service.match_event(leg) is None
    assert service.match_event({**leg, "event_date": None}) is None


def test_supported_confirmation_waits_for_event_start():
    service = MemberBetService(database=Mock())
    row = ticket(status="draft", confirmed_at=None)
    event = {**row["event"], "start_at": "2099-10-03T23:00:00+00:00"}
    service.get = Mock(return_value=row)
    service.match_event = Mock(return_value=event)
    service.update = Mock(side_effect=lambda bet_id, fields, status: {**row, **fields})
    confirmed = service.confirm(1, 10, 2, -110)
    assert confirmed["status"] == "open"
    assert confirmed["next_check_at"] == event["start_at"]
    assert service.update.call_args.args[2] == "draft"


def test_missing_line_does_not_get_auto_grading():
    service = MemberBetService(database=Mock())
    row = ticket(status="draft", confirmed_at=None)
    row["details"]["legs"][0]["line"] = None
    service.get = Mock(return_value=row)
    service.match_event = Mock(return_value=row["event"])
    service.update = Mock(side_effect=lambda bet_id, fields, status: {**row, **fields})
    assert service.confirm(1, 10, 2, -110)["status"] == "review"


def test_private_bucket_required_at_startup():
    database = Mock()
    database._ensure_client.return_value.storage.get_bucket.return_value = SimpleNamespace(public=True)
    with pytest.raises(RuntimeError, match="must be private"):
        MemberBetService(database=database).ready()


def test_settlement_uses_atomic_rpc_and_duplicate_result_is_false():
    database = Mock()
    database._ensure_client.return_value.rpc.return_value.execute.return_value = SimpleNamespace(data=False)
    service = MemberBetService(database=database)
    assert service.settle(1, "moderator", "win", "Moderator-settled", "Reviewed slip") is False
    database._ensure_client.return_value.rpc.assert_called_once_with("settle_member_bet", {
        "p_bet_id": 1, "p_actor_id": "moderator", "p_result": "win",
        "p_verification": "Moderator-settled", "p_reason": "Reviewed slip",
    })
    database.update.assert_not_called()


def test_moderator_permissions_are_required_even_for_owner(monkeypatch):
    user = Mock(spec=discord.Member)
    user.guild_permissions = SimpleNamespace(manage_guild=False)
    user.roles = [SimpleNamespace(id=99)]
    monkeypatch.setattr("src.member_bet_vault.OPERATOR_ROLE_IDS", {99})
    assert can_moderate(user)
    user.roles = []
    assert not can_moderate(user)
    user.guild_permissions.manage_guild = True
    assert can_moderate(user)


def test_failed_source_deletion_warning_is_public_without_selection():
    embed = card_embed(ticket(status="review", original_removed=False)).to_dict()
    assert "may still be visible" in str(embed)
    assert "SECRET" not in str(embed)


def test_processing_survives_restart_and_deletes_original_before_extract():
    async def run():
        service = Mock()
        row = ticket(status="processing", confirmed_at=None)
        service.pending.return_value = [row]
        service.dirty.return_value = []
        vault = MemberBetVault(Mock(), 300, service)
        vault.original_removed = AsyncMock(return_value=True)
        await vault.reconcile()
        vault.original_removed.assert_awaited_once_with(row)
        service.extract.assert_called_once_with(row)
        assert service.update.call_args.args[1] == {"original_removed": True}

    asyncio.run(run())


def test_failed_events_shared_and_each_ticket_retried_without_settlement():
    async def run():
        service = Mock()
        service.pending.return_value = [ticket(), ticket(id=2)]
        service.dirty.return_value = []
        service.fetch_event.side_effect = RuntimeError("API unavailable")
        vault = MemberBetVault(Mock(), 300, service)
        await vault.reconcile()
        service.fetch_event.assert_called_once()
        assert service.retry.call_count == 2
        service.check.assert_not_called()
        service.settle.assert_not_called()

    asyncio.run(run())


def test_card_update_is_retried_and_missing_card_is_recreated():
    async def run():
        channel = SimpleNamespace(fetch_message=AsyncMock(), send=AsyncMock())
        channel.fetch_message.side_effect = discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "Deleted")
        channel.send.return_value = SimpleNamespace(id=201)
        bot = SimpleNamespace(get_channel=lambda channel_id: channel)
        service = Mock()
        vault = MemberBetVault(bot, 300, service)
        row = ticket(status="win", verification="API-verified")
        await vault.publish(row)
        assert channel.send.call_args.kwargs["view"] is None
        assert "SECRET" in str(channel.send.call_args.kwargs["embed"].to_dict())
        service.db.update.assert_called_once_with("member_bets", {
            "card_message_id": "201", "card_dirty": False,
        }, {"id": 1, "updated_at": row["updated_at"]})

    asyncio.run(run())


@pytest.mark.parametrize("field,value", [("event_date", []), ("market", {}), ("line", "NaN")])
def test_invalid_vision_grading_fields_are_rejected(field, value):
    parsed = ticket()["details"]
    parsed["units"] = 2
    parsed["legs"][0][field] = value
    with pytest.raises(ValueError):
        MemberImageService._validate(parsed)


def test_long_parlay_review_fits_embed_and_private_attachment_is_complete():
    row = ticket()
    row["details"]["legs"] = [
        {**row["details"]["legs"][0], "selection": f"Leg {index} " + "x" * 990}
        for index in range(10)
    ]
    row["details"]["game"] = "G" * 500
    row["review_reason"] = "R" * 500
    assert len(review_embed(row)) <= 6000
    attachment = ticket_file(row, private=True)
    text = attachment.fp.read().decode("utf-8")
    assert "Leg 9 " + "x" * 990 in text
    assert "full_game_including_overtime" in text
    attachment.close()


def test_open_card_never_attaches_full_selection_text():
    async def run():
        card = SimpleNamespace(id=200, edit=AsyncMock())
        channel = SimpleNamespace(fetch_message=AsyncMock(return_value=card))
        bot = SimpleNamespace(get_channel=lambda channel_id: channel)
        vault = MemberBetVault(bot, 300, Mock())
        row = ticket()
        row["details"]["legs"][0]["selection"] = "SECRET" * 200
        await vault.publish(row)
        assert card.edit.call_args.kwargs["attachments"] == []
        assert "SECRET" not in str(card.edit.call_args.kwargs["embed"].to_dict())

    asyncio.run(run())


def test_settled_long_ticket_reveals_complete_text_without_api_metadata():
    row = ticket(status="win", verification="Moderator-settled")
    row["details"]["legs"][0]["selection"] = "SECRET" * 200
    attachment = ticket_file(row)
    text = attachment.fp.read().decode("utf-8")
    assert "SECRET" * 200 in text
    assert "Moderator-settled" in text
    assert "scope" not in text
    attachment.close()


def test_nonpaying_member_photo_is_deleted_without_processing(paid_membership_default):
    paid_membership_default.has_vault_access.return_value = False
    service = Mock()
    attachment = photo_attachment()
    msg = message([attachment])
    asyncio.run(MemberBetVault(Mock(), 300, service).handle_message(msg))
    msg.delete.assert_awaited_once()
    assert "current verified paid access" in msg.channel.send.call_args.args[0]
    attachment.read.assert_not_awaited()
    service.save_submission.assert_not_called()


def test_membership_lookup_failure_fails_closed(paid_membership_default, caplog):
    paid_membership_default.has_vault_access.side_effect = RuntimeError("Database unavailable")
    service = Mock()
    msg = message([photo_attachment()])
    asyncio.run(MemberBetVault(Mock(), 300, service).handle_message(msg))
    msg.delete.assert_awaited_once()
    assert "could not verify" in msg.channel.send.call_args.args[0]
    assert "membership_lookup_failed" in caplog.text
    service.save_submission.assert_not_called()


def test_membership_expiring_while_queued_is_rechecked(paid_membership_default):
    paid_membership_default.has_vault_access.side_effect = [True, False]
    service = Mock()
    msg = message([photo_attachment()])
    asyncio.run(MemberBetVault(Mock(), 300, service).handle_message(msg))
    service.save_submission.assert_not_called()
    assert "expired" in msg.channel.send.call_args.args[0]
