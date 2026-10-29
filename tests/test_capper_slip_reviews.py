import asyncio
import base64
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from io import BytesIO
from PIL import Image

import pytest
import discord

import src.capper_slip_reviews as module
import src.forum_health as health
from src.capper_slip_reviews import CapperSlipReviews, SlipConfirmation, review_embed
from src.services.ballot_vote_service import approval_text, slip_rating
from src.services.slip_review_service import (
    confirmed_legs, parse_cutoff, parse_event_start, response_choices, review_time,
)


def saved_row():
    now = datetime.now(timezone.utc)
    return {
        "message_id": "500", "state": "pending", "policy_version": "slip-v1",
        "review_message_id": "900", "eligible_voter_ids": ["42", "43", "44", "45", "46"],
        "cutoff_at": (now + timedelta(minutes=5)).isoformat(),
        "event_starts_at": (now + timedelta(minutes=10)).isoformat(),
        "reminder_sent": False, "response_states": {"42": {"yes": True}, "43": {"yes": True}},
        "review": {
            "author_id": "42", "slip_base64": "c2xpcA==", "source_text": "",
            "source_edited_at": None, "attachment_ids": ["700"],
            "parsed": {"legs": [{"selection": "Wolves ML", "odds": 100}]},
        },
    }


def make_cog(monkeypatch):
    monkeypatch.setattr(module, "GUILD_ID", 123)
    monkeypatch.setattr(health, "GUILD_ID", 123)
    channel = SimpleNamespace(
        id=module.CAPPER_BALLOT_CHANNEL_ID, guild=SimpleNamespace(id=123),
        fetch_message=AsyncMock(), send=AsyncMock(), purge=AsyncMock(),
    )
    prompt = SimpleNamespace(id=900, jump_url="https://discord.com/channels/123/100/900",
                             edit=AsyncMock(), add_reaction=AsyncMock(), clear_reactions=AsyncMock())
    channel.fetch_message.return_value = prompt
    store = Mock(get=Mock(return_value=saved_row()), finalize=Mock(return_value=None),
                 pending=Mock(return_value=[]), interrupted=Mock(return_value=[]),
                 find=Mock(return_value=None), known_ids=Mock(return_value=set()), invalid_ids=Mock(return_value=[]))
    cog = CapperSlipReviews(
        Mock(user=SimpleNamespace(id=1), is_ready=Mock(return_value=True)), store=store,
        official=Mock(), resolve_channel=AsyncMock(return_value=channel), is_tracked_operator=Mock(return_value=True),
        staff_alert=AsyncMock(), get_testing=Mock(return_value=False),
        tracked_operators=AsyncMock(return_value={42, 43, 44, 45, 46}), images=Mock(),
        request_insight=AsyncMock(return_value=True),
    )
    cog.needs_reconciliation = False
    cog.submission_notice_ready = True
    cog.valid_source = AsyncMock(return_value=True)
    cog.publisher.publish_approved = AsyncMock(return_value={"play_id": 7})
    return cog, channel, prompt


@pytest.mark.parametrize("size", range(2, 9))
def test_all_support_thresholds(size):
    for yes in range(size + 1):
        for no in range(size - yes + 1):
            units = min(yes - 1, 3)
            assert slip_rating(yes, no, size) == ((units + 2, units) if yes >= 2 and yes > no else None)


@pytest.mark.parametrize("counts", [(1, 0, 1), (-1, 0, 5), (2, -1, 5), (3, 3, 5)])
def test_invalid_counts_raise(counts):
    with pytest.raises(ValueError):
        slip_rating(*counts)


def test_cutoff_requires_timezone_and_converts_to_utc():
    assert review_time("2026-10-22T19:00-04:00").isoformat() == "2026-10-22T23:00:00+00:00"
    with pytest.raises(ValueError, match="timezone"):
        review_time("2026-10-22T19:00")
    with pytest.raises(ValueError, match="Use YYYY"):
        review_time("tonight")


@pytest.mark.parametrize("fraction", ["2", "21", "219", "2196", "21968", "219680"])
@pytest.mark.parametrize("offset", ["Z", "+00:00", "-04:00"])
def test_review_time_normalizes_postgres_fractions_for_python310(monkeypatch, fraction, offset):
    import src.datetime_utils as datetime_utils

    def python310_fromisoformat(value):
        assert value == f"2026-10-29T04:49:59.{fraction.ljust(6, '0')}{'+00:00' if offset == 'Z' else offset}"
        return datetime.fromisoformat(value)

    parser = Mock(wraps=datetime)
    parser.fromisoformat.side_effect = python310_fromisoformat
    monkeypatch.setattr(datetime_utils, "datetime", parser)
    value = f"2026-10-29T04:49:59.{fraction}{offset}"
    assert review_time(value) == datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    parser.fromisoformat.assert_called_once()


def test_confirmed_legs_preserve_only_matching_metadata():
    previous = [{"selection": "Wolves ML", "odds": 100, "home_name": "Minnesota"}]
    assert confirmed_legs("Wolves ML | +100", previous) == previous
    assert confirmed_legs("Other ML | -110", previous) == [{"selection": "Other ML", "odds": -110}]
    for text in ["", "Wolves ML", "Wolves ML | nope", "Wolves ML | 0"]:
        with pytest.raises(ValueError):
            confirmed_legs(text, previous)


def test_conflicts_are_unresolved_and_missing_never_means_no():
    row = saved_row()
    row["response_states"]["43"] = {"yes": True, "no": True}
    row["response_states"]["44"] = {"pass": True}
    assert response_choices(row) == {"42": "yes", "44": "pass"}
    fields = {field.name: field.value for field in review_embed(row).fields}
    assert fields["Live responses"] == "1 supported | 0 opposed | 1 passed | 3 did not respond"


def test_approval_distinguishes_respondent_and_team_unanimity():
    approval = {"policy_version": "slip-v1", "roster_size": 5, "counts": {"yes": 3, "no": 0, "pass": 0}}
    assert approval_text(approval, 2) == (
        "3 supported | 0 opposed | 0 passed | 2 did not respond | Official stake: 2u | Unanimous among respondents"
    )
    approval["counts"]["yes"] = 5
    assert approval_text(approval, 3).endswith("Unanimous team approval")
    approval["counts"] = {"yes": 3, "no": 0, "pass": 2}
    assert "Unanimous" not in approval_text(approval, 2)


def test_author_modal_requires_start_but_cutoff_is_optional(monkeypatch):
    async def check():
        cog, _, _ = make_cog(monkeypatch)
        modal = SlipConfirmation(cog, saved_row())
        assert modal.start.default == ""
        assert modal.start.required and not modal.cutoff.required
    asyncio.run(check())


def test_friendly_times():
    now = datetime(2026, 10, 22, 14, 0, tzinfo=timezone.utc)  # 10:00 AM ET
    start = parse_event_start("tonight 7:30 pm", now)
    assert start.isoformat() == "2026-10-22T23:30:00+00:00"
    assert parse_event_start("7pm", now).isoformat() == "2026-10-22T23:00:00+00:00"
    assert parse_event_start("9am", now).isoformat() == "2026-10-23T13:00:00+00:00"
    assert parse_event_start("Oct 24 1pm", now).isoformat() == "2026-10-24T17:00:00+00:00"
    assert parse_event_start("10/24 8:15 PM CT", now).isoformat() == "2026-10-25T01:15:00+00:00"
    assert parse_event_start("2026-10-22T19:00-04:00", now) == start - timedelta(minutes=30)
    assert parse_cutoff("", start, now) == start - timedelta(minutes=10)
    assert parse_cutoff("30 min before", start, now) == start - timedelta(minutes=30)
    assert parse_cutoff("1 hour", start, now) == start - timedelta(hours=1)
    assert parse_cutoff("6:45 PM", start, now).isoformat() == "2026-10-22T22:45:00+00:00"
    for bad in ["", "7", "soon"]:
        with pytest.raises(ValueError):
            parse_event_start(bad, now)


def test_confirm_saves_author_cutoff_and_frozen_roster(monkeypatch):
    async def check():
        cog, channel, prompt = make_cog(monkeypatch)
        row = saved_row()
        row["cutoff_at"] = None
        cog.store.get.side_effect = [row, saved_row()]
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=42), guild_id=123, channel_id=module.CAPPER_BALLOT_CHANNEL_ID,
            followup=SimpleNamespace(send=AsyncMock()),
        )
        now = datetime.now(timezone.utc)
        start, cutoff = now + timedelta(hours=1), now + timedelta(minutes=30)
        await cog.confirm(interaction, 500, "Wolves ML | +100", start.isoformat(), cutoff.isoformat())
        args = cog.store.open.call_args.args
        assert args[:3] == (500, 42, {42, 43, 44, 45, 46})
        assert args[-2:] == (start, cutoff)
        prompt.clear_reactions.assert_awaited_once()
        assert prompt.add_reaction.await_count == 2
        channel.send.assert_awaited_once()
        assert str(int(cutoff.timestamp())) in channel.send.call_args.args[0]
    asyncio.run(check())


@pytest.mark.parametrize("kind", ["past", "after-start", "wrong-author", "changed-source", "roster"])
def test_invalid_confirmation_does_not_open(monkeypatch, kind):
    async def check():
        cog, _, _ = make_cog(monkeypatch)
        row = saved_row()
        row["cutoff_at"] = None
        cog.store.get.return_value = row
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=43 if kind == "wrong-author" else 42),
            guild_id=123, channel_id=module.CAPPER_BALLOT_CHANNEL_ID,
        )
        now = datetime.now(timezone.utc)
        start, cutoff = now + timedelta(minutes=20), now + timedelta(minutes=10)
        if kind == "past":
            cutoff = now - timedelta(seconds=1)
        elif kind == "after-start":
            cutoff = start
        elif kind == "changed-source":
            cog.valid_source.return_value = False
        elif kind == "roster":
            cog.publisher.tracked_operators.return_value = None
        with pytest.raises(ValueError):
            await cog.confirm(interaction, 500, "Wolves ML | +100", start.isoformat(), cutoff.isoformat())
        cog.store.open.assert_not_called()
    asyncio.run(check())


def test_threshold_crossing_alone_does_not_publish(monkeypatch):
    cog, channel, prompt = make_cog(monkeypatch)
    asyncio.run(cog.evaluate(channel, saved_row()))
    cog.publisher.publish_approved.assert_not_awaited()
    assert "projected 1u" in str(prompt.edit.call_args.kwargs["embed"].to_dict())


def test_approved_review_uses_saved_original_and_existing_publisher(monkeypatch):
    cog, channel, prompt = make_cog(monkeypatch)
    row = saved_row()
    decision = {
        "outcome": "processing", "policy_version": "slip-v1", "yes": 2,
        "roster_size": 5, "counts": {"yes": 2, "no": 0, "pass": 0}, "units": 1, "stars": 3,
    }
    cog.store.finalize.return_value = decision
    asyncio.run(cog.evaluate(channel, row))
    args = cog.publisher.publish_approved.call_args
    assert args.args[1:] == (row["review"]["parsed"], decision)
    assert args.kwargs["slip"] == b"slip"
    cog.request_insight.assert_awaited_once_with(7)
    assert prompt.edit.call_args.kwargs["embed"].title == "Review closed - official pick published"


def test_justification_request_failure_does_not_fail_or_republish_official_pick(monkeypatch):
    cog, channel, prompt = make_cog(monkeypatch)
    cog.store.finalize.return_value = {
        "outcome": "processing", "policy_version": "slip-v1", "yes": 2,
        "roster_size": 5, "counts": {"yes": 2, "no": 0, "pass": 0}, "units": 1, "stars": 3,
    }
    cog.request_insight.side_effect = RuntimeError("team-discussion unavailable")
    asyncio.run(cog.evaluate(channel, saved_row()))
    cog.publisher.publish_approved.assert_awaited_once()
    cog.store.fail.assert_not_called()
    cog.publisher.staff_alert.assert_awaited_once()
    assert "Do not republish" in cog.publisher.staff_alert.call_args.args[1]
    assert prompt.edit.call_args.kwargs["embed"].title == "Review closed - official pick published"


@pytest.mark.parametrize("outcome", ["rejected", "expired"])
def test_unqualified_or_expired_review_never_publishes(monkeypatch, outcome):
    cog, channel, prompt = make_cog(monkeypatch)
    cog.store.finalize.return_value = {
        "outcome": outcome, "counts": {"yes": 1, "no": 1, "pass": 0}, "roster_size": 5,
    }
    asyncio.run(cog.evaluate(channel, saved_row()))
    cog.publisher.publish_approved.assert_not_awaited()
    cog.request_insight.assert_not_awaited()
    assert prompt.edit.call_args.kwargs["view"] is None
    if outcome == "expired":
        cog.store.fail.assert_called_once()


def test_changed_source_fails_closed(monkeypatch):
    cog, channel, _ = make_cog(monkeypatch)
    cog.valid_source.return_value = False
    asyncio.run(cog.evaluate(channel, saved_row()))
    cog.store.finalize.assert_not_called()
    cog.store.fail.assert_called_once()
    cog.publisher.staff_alert.assert_awaited_once()


def test_offline_across_cutoff_fails_instead_of_guessing(monkeypatch):
    cog, channel, _ = make_cog(monkeypatch)
    row = saved_row()
    row["cutoff_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    asyncio.run(cog.reconcile_reactions(channel, row))
    cog.store.fail.assert_called_once()
    cog.store.respond.assert_not_called()


def test_recovery_reconciles_live_reaction_removals(monkeypatch):
    cog, channel, prompt = make_cog(monkeypatch)
    prompt.reactions = []
    asyncio.run(cog.reconcile_reactions(channel, saved_row()))
    assert (500, 43, "yes_remove") in [call.args for call in cog.store.respond.call_args_list]


def test_reminder_is_claimed_once_and_targets_only_missing_voters(monkeypatch):
    cog, channel, _ = make_cog(monkeypatch)
    row = saved_row()
    row["cutoff_at"] = (datetime.now(timezone.utc) + timedelta(seconds=90)).isoformat()
    asyncio.run(cog.evaluate(channel, row))
    cog.store.claim_reminder.assert_called_once_with(500)
    text = channel.send.call_args.args[0]
    assert "<@42>" not in text and "<@43>" not in text
    assert "<@44>" in text and "<@45>" in text and "<@46>" in text


def test_cleanup_preserves_active_and_interrupted_messages(monkeypatch):
    cog, channel, _ = make_cog(monkeypatch)
    row = saved_row()
    cog.store.pending.return_value = [row]
    interrupted = deepcopy(row)
    interrupted.update(message_id="501", review_message_id="901")
    cog.store.interrupted.return_value = [interrupted]
    asyncio.run(cog.daily_cleanup.coro(cog))
    check = channel.purge.call_args.kwargs["check"]
    for message_id in (500, 900, 501, 901):
        assert not check(SimpleNamespace(id=message_id))
    assert check(SimpleNamespace(id=999))


def test_intake_keeps_private_original_and_displays_slip_on_review_message(monkeypatch):
    async def check():
        cog, channel, _ = make_cog(monkeypatch)
        raw = BytesIO()
        Image.new("RGB", (10, 10), "white").save(raw, format="JPEG")
        image = SimpleNamespace(id=700, size=len(raw.getvalue()), content_type="image/jpeg",
                                url="https://cdn/slip", read=AsyncMock(return_value=raw.getvalue()))
        source = SimpleNamespace(
            id=500, author=SimpleNamespace(id=42), content="Wolves ML", attachments=[image],
            edited_at=None, channel=channel, jump_url="https://discord.com/channels/123/100/500",
            created_at=datetime.now(timezone.utc),
            guild=SimpleNamespace(filesize_limit=10 * 1024 * 1024),
        )
        cog.publisher.images.extract_play.return_value = {"legs": [{"selection": "Wolves ML", "odds": 100}]}
        channel.send.return_value = SimpleNamespace(id=900)
        await cog.intake(source, [image])
        review = cog.store.register.call_args.args[1]
        assert base64.b64decode(review["slip_base64"]).startswith(b"\x89PNG")
        assert cog.publisher.images.extract_play.call_args.args[0] == "data:image/png;base64," + review["slip_base64"]
        sent = channel.send.call_args.kwargs
        assert sent["file"].filename == "slip-500.png"
        assert sent["embed"].image.url == "attachment://slip-500.png"
        cog.store.update_review.assert_called_with(500, review_message_id="900")
    asyncio.run(check())


@pytest.mark.parametrize("action", ["pass", "cancel", "confirm"])
def test_controls_acknowledge_before_slow_database_and_prepare_modal_safely(monkeypatch, action):
    async def check():
        cog, _, prompt = make_cog(monkeypatch)
        row = saved_row()
        if action == "confirm":
            row["cutoff_at"] = None
        acknowledged = []
        started = time.monotonic()
        request = SimpleNamespace(
            data={"custom_id": f"pm:sr:500:{action}"}, guild_id=123,
            channel_id=module.CAPPER_BALLOT_CHANNEL_ID, message=prompt,
            user=SimpleNamespace(id=42, bot=False),
            response=SimpleNamespace(defer=AsyncMock(side_effect=lambda **_: acknowledged.append(time.monotonic())),
                                     is_done=lambda: bool(acknowledged)),
            followup=SimpleNamespace(send=AsyncMock()),
        )

        def lookup(_):
            assert acknowledged, "Database work must never precede the initial response"
            if cog.store.get.call_count == 1:
                time.sleep(3.1)
            return row

        cog.store.get.side_effect = lookup
        cog.store.respond.return_value = "saved"
        cog.evaluate = AsyncMock()
        prompt.remove_reaction = AsyncMock()
        await cog.on_interaction(request)
        assert acknowledged[0] - started < 1
        request.response.defer.assert_awaited_once_with(ephemeral=True)
        if action == "confirm":
            view = request.followup.send.call_args.kwargs["view"]
            fresh = SimpleNamespace(user=request.user, response=SimpleNamespace(send_modal=AsyncMock()))
            await view.open_form.callback(fresh)
            assert isinstance(fresh.response.send_modal.call_args.args[0], SlipConfirmation)
    asyncio.run(check())


def test_invalid_intake_warns_once_across_restart_but_reconsiders_edits(monkeypatch):
    async def check():
        cog, channel, _ = make_cog(monkeypatch)
        saved = {}

        def claim(message_id, fingerprint, reason, forum_id):
            changed = saved.get(message_id) != fingerprint
            saved[message_id] = fingerprint
            return changed

        cog.store.claim_invalid.side_effect = claim
        images = [SimpleNamespace(id=700), SimpleNamespace(id=701)]
        source = SimpleNamespace(id=500, author=SimpleNamespace(id=42), content="", edited_at=None,
                                 attachments=images, channel=channel)
        await cog.intake(source, images)
        await cog.intake(source, images)
        assert channel.send.await_count == 1
        restarted, restarted_channel, _ = make_cog(monkeypatch)
        restarted.store.claim_invalid.side_effect = claim
        source.channel = restarted_channel
        await restarted.intake(source, images)
        restarted_channel.send.assert_not_awaited()
        source.content = "edited submission"
        source.edited_at = datetime.now(timezone.utc)
        await restarted.intake(source, images)
        restarted_channel.send.assert_awaited_once()
        cog.store.register.assert_not_called()
    asyncio.run(check())


def test_gateway_queue_is_bounded_deduplicated_and_reconciliation_is_slower(monkeypatch):
    async def check():
        cog, _, _ = make_cog(monkeypatch)
        forum, thread = forum_channel(), review_thread()
        cog.checked_forum.resolve = AsyncMock(return_value=forum)
        cog.publisher.resolve_channel.return_value = thread
        cog.discover_thread = AsyncMock()
        await cog.on_thread_create(thread)
        await cog.on_thread_create(thread)
        assert cog.intake_queue.qsize() == 1
        await cog.intake_worker.coro(cog)
        cog.discover_thread.assert_awaited_once_with(forum, thread)
        assert not cog.queued_ids and cog.intake_queue.empty()
        for value in range(1000, 1101):
            await cog.queue_intake(value)
        assert cog.intake_queue.qsize() == 100
        cog.publisher.staff_alert.assert_awaited_once()
        assert cog.scan_slips.minutes == 5
    asyncio.run(check())


def test_invalid_archived_post_is_rechecked_after_reconnect_cursor(monkeypatch):
    async def check():
        cog, _, _ = make_cog(monkeypatch)
        forum, thread = forum_channel(), review_thread()
        forum.guild.fetch_channel = AsyncMock(return_value=thread)
        cog.store.scan_state.return_value = {
            "started_at": "2026-01-01T00:00:00+00:00", "forum_scanned_at": "2026-10-28T00:00:00+00:00",
        }
        cog.store.invalid_ids.return_value = [500]
        cog.forum_threads = AsyncMock(return_value=[])
        cog.discover_thread = AsyncMock()
        await cog.discover(forum)
        cog.discover_thread.assert_awaited_once()
        cog.store.advance_forum.assert_called_once()
    asyncio.run(check())


def test_scan_failure_does_not_disable_gateway_intake_after_notice_verified(monkeypatch):
    async def check():
        cog, _, _ = make_cog(monkeypatch)
        cog.ensure_submission_notice = AsyncMock()
        cog.discover = AsyncMock(side_effect=RuntimeError("database unavailable"))
        await cog.scan_slips.coro(cog)
        assert cog.submission_notice_ready
        cog.publisher.staff_alert.assert_awaited_once()
    asyncio.run(check())


def test_notice_failure_disables_intake_and_skips_discovery(monkeypatch):
    async def check():
        cog, _, _ = make_cog(monkeypatch)
        cog.ensure_submission_notice = AsyncMock(side_effect=RuntimeError("notice unavailable"))
        cog.discover = AsyncMock()
        await cog.scan_slips.coro(cog)
        assert not cog.submission_notice_ready
        cog.discover.assert_not_awaited()
        cog.publisher.staff_alert.assert_awaited_once()
    asyncio.run(check())


@pytest.mark.parametrize("kind", ["oversized", "gif", "invalid-bytes", "guild-limit"])
def test_invalid_photos_never_reach_openai_or_register(monkeypatch, kind):
    async def check():
        cog, channel, _ = make_cog(monkeypatch)
        raw = BytesIO()
        Image.new("RGB", (10, 10), "white").save(raw, format="PNG")
        image = SimpleNamespace(
            id=700, size=11 * 1024 * 1024 if kind == "oversized" else len(raw.getvalue()),
            content_type="image/gif" if kind == "gif" else "image/png",
            read=AsyncMock(return_value=b"not an image" if kind == "invalid-bytes" else raw.getvalue()),
        )
        source = SimpleNamespace(id=500, author=SimpleNamespace(id=42), content="", edited_at=None,
                                 attachments=[image], channel=channel,
                                 guild=SimpleNamespace(filesize_limit=1 if kind == "guild-limit" else 10 * 1024 * 1024))
        await cog.intake(source, [image])
        cog.store.register.assert_not_called()
        cog.publisher.images.extract_play.assert_not_called()
        cog.store.claim_invalid.assert_called_once()
        channel.send.assert_awaited_once()
    asyncio.run(check())


def test_submission_notice_uncertainty_never_creates_a_duplicate(monkeypatch):
    async def check():
        cog, _, _ = make_cog(monkeypatch)
        forum = forum_channel()
        cog.checked_forum.resolve = AsyncMock(return_value=forum)
        select = Mock(return_value=SimpleNamespace(data=[{"thread_id": "sending"}]))
        monkeypatch.setattr(module.supabase_service, "select", select)
        forum.create_thread = AsyncMock()
        with pytest.raises(RuntimeError, match="uncertain"):
            await cog.ensure_submission_notice()
        forum.create_thread.assert_not_awaited()
    asyncio.run(check())


def test_health_is_staff_only_and_reports_failures_without_sends_or_mutation(monkeypatch):
    async def check():
        cog, _, _ = make_cog(monkeypatch)
        request = SimpleNamespace(guild_id=123, guild=SimpleNamespace(owner_id=99),
                                  user=SimpleNamespace(id=42),
                                  response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
                                  followup=SimpleNamespace(send=AsyncMock()))
        cog.health_report = AsyncMock(return_value=["FAIL Database readiness"])
        await cog.health_command.callback(cog, request)
        cog.health_report.assert_not_awaited()
        request.response.send_message.assert_awaited_once()
        member = Mock(spec=discord.Member)
        member.id, member.guild_permissions = 99, discord.Permissions.all()
        request.user = member
        await cog.health_command.callback(cog, request)
        request.response.defer.assert_awaited_once_with(ephemeral=True)
        assert request.followup.send.call_args.args[0] == "FAIL Database readiness"
        cog.store.register.assert_not_called()
        cog.publisher.publish_approved.assert_not_awaited()
    asyncio.run(check())


def test_one_failed_post_never_blocks_other_posts_or_advances_recovery_cursor(monkeypatch):
    async def check():
        cog, _, _ = make_cog(monkeypatch)
        forum, first, second = forum_channel(), review_thread(), review_thread(501)
        cog.store.scan_state.return_value = {"started_at": "2026-01-01T00:00:00+00:00"}
        cog.forum_threads = AsyncMock(return_value=[first, second])
        cog.discover_thread = AsyncMock(side_effect=[RuntimeError("locked post"), None])
        await cog.discover(forum)
        assert cog.discover_thread.await_count == 2
        cog.store.advance_forum.assert_not_called()
        cog.publisher.staff_alert.assert_awaited_once()
    asyncio.run(check())


def test_health_report_distinguishes_database_and_stopped_workers_without_side_effects(monkeypatch):
    async def check():
        cog, _, _ = make_cog(monkeypatch)
        cog.bot.intents = discord.Intents.all()
        cog.checked_forum.resolve = AsyncMock()
        cog.records.checked_forum.resolve = AsyncMock()
        cog.store.rpc.return_value = {
            "version": 1, "pending": 2, "interrupted": 0, "record_jobs": 3, "uncertain_records": 1,
        }
        report = await cog.health_report()
        assert any("schema v1; pending=2" in line for line in report)
        assert any(line.startswith("FAIL Staff reconciliation") for line in report)
        assert "FAIL Worker intake_worker" in report
        assert any("Portal" in line for line in report)
        cog.store.register.assert_not_called()
        cog.publisher.staff_alert.assert_not_awaited()
    asyncio.run(check())


def test_publication_failure_is_durable_and_never_retried_by_reaction(monkeypatch):
    cog, _, _ = make_cog(monkeypatch)
    row = saved_row()
    cog.store.pending.return_value = [row]
    cog.store.respond.return_value = "saved"
    cog.evaluate = AsyncMock(side_effect=RuntimeError("uncertain send"))
    payload = SimpleNamespace(
        guild_id=123, channel_id=module.CAPPER_BALLOT_CHANNEL_ID, message_id=900,
        emoji=module.SUPPORT, user_id=43,
    )
    asyncio.run(cog.reaction(payload, added=True))
    cog.store.fail.assert_called_once_with(500, "uncertain send")
    cog.publisher.staff_alert.assert_awaited_once()


def test_review_reactions_do_not_dispatch_settlement_and_ignore_other_channels(monkeypatch):
    cog, _, _ = make_cog(monkeypatch)
    row = saved_row()
    cog.store.pending.return_value = [row]
    cog.store.respond.return_value = "saved"
    cog.evaluate = AsyncMock()
    payload = SimpleNamespace(guild_id=123, channel_id=module.CAPPER_BALLOT_CHANNEL_ID,
                              message_id=900, emoji=module.SUPPORT, user_id=43)
    asyncio.run(cog.reaction(payload, added=True))
    cog.store.respond.assert_called_once_with(500, 43, "yes_add")
    cog.publisher.official.settle_play.assert_not_called()
    cog.store.respond.reset_mock()
    payload.channel_id = 999
    asyncio.run(cog.reaction(payload, added=True))
    cog.store.respond.assert_not_called()


def forum_channel():
    forum = Mock(spec=discord.ForumChannel)
    forum.id = module.CAPPER_BALLOT_CHANNEL_ID
    default = SimpleNamespace(id=0, permissions=discord.Permissions.none(), is_bot_managed=lambda: False)
    capper = SimpleNamespace(id=module.TRACKER_ROLE_ID, permissions=discord.Permissions.none(), is_bot_managed=lambda: False)
    forum.guild = SimpleNamespace(id=123, default_role=default, me=SimpleNamespace(id=1), owner_id=99,
                                  roles=[default, capper], get_role=lambda _: capper, chunked=False,
                                  get_member=Mock(return_value=SimpleNamespace(id=42)))
    forum.permissions_for.return_value = discord.Permissions.none()
    forum.permissions_for.side_effect = lambda target: forum.permissions_for.return_value if target is default else discord.Permissions.all()
    forum.flags = SimpleNamespace(require_tag=False)
    forum.overwrites = {}
    return forum


def review_thread(thread_id=500):
    thread = Mock(spec=discord.Thread)
    thread.id, thread.parent_id = thread_id, module.CAPPER_BALLOT_CHANNEL_ID
    thread.guild = SimpleNamespace(id=123)
    thread.archived, thread.locked = False, False
    thread.fetch_message = AsyncMock()
    thread.delete = AsyncMock()
    thread.edit = AsyncMock(return_value=thread)
    return thread


def test_forum_discovery_reads_only_new_starter_slips(monkeypatch):
    cog, _, _ = make_cog(monkeypatch)
    forum, thread, known = forum_channel(), review_thread(), review_thread(501)
    thread.fetch_message.return_value = SimpleNamespace(
        created_at=datetime.now(timezone.utc), author=SimpleNamespace(id=42, bot=False),
        attachments=[SimpleNamespace(content_type="image/png")],
    )
    cog.store.scan_state.return_value = {"started_at": "2026-01-01T00:00:00+00:00"}
    cog.store.find.side_effect = lambda value, **kwargs: saved_row() if value == 501 else None
    cog.store.known_ids.return_value = {501}
    cog.forum_threads = AsyncMock(return_value=[thread, known])
    cog.intake = AsyncMock()
    asyncio.run(cog.discover(forum))
    thread.fetch_message.assert_awaited_once_with(500)
    known.fetch_message.assert_not_awaited()
    cog.intake.assert_awaited_once_with(thread.fetch_message.return_value, thread.fetch_message.return_value.attachments)


def test_scan_recovers_reported_postgres_cursor_without_alert_or_republishing(monkeypatch):
    import src.datetime_utils as datetime_utils
    import src.services.slip_review_service as slip_service

    def python310_fromisoformat(value):
        if value == "2026-10-29T04:49:59.21968+00:00":
            raise ValueError("Invalid isoformat string")
        return datetime.fromisoformat(value)

    parser = Mock(wraps=datetime)
    parser.fromisoformat.side_effect = python310_fromisoformat
    monkeypatch.setattr(datetime_utils, "datetime", parser)
    monkeypatch.setattr(slip_service, "datetime", parser)

    async def check():
        cog, _, _ = make_cog(monkeypatch)
        forum, thread, known = forum_channel(), review_thread(), review_thread(501)
        cog.checked_forum.resolve = AsyncMock(return_value=forum)
        cog.ensure_submission_notice = AsyncMock()
        cog.store.scan_state.return_value = {
            "started_at": "2026-10-22T03:29:29.619288+00:00",
            "forum_scanned_at": "2026-10-29T04:49:59.21968+00:00",
        }
        cog.forum_threads = AsyncMock(return_value=[thread, known])
        cog.store.known_ids.return_value = {501}
        thread.fetch_message.return_value = SimpleNamespace(
            created_at=datetime.now(timezone.utc), author=SimpleNamespace(id=42, bot=False),
            attachments=[SimpleNamespace(content_type="image/png")],
        )
        cog.intake = AsyncMock()
        await cog.scan_slips.coro(cog)
        cog.forum_threads.assert_awaited_once_with(
            forum, since=datetime(2026, 10, 29, 4, 49, 59, 219680, tzinfo=timezone.utc),
        )
        cog.intake.assert_awaited_once_with(thread.fetch_message.return_value, thread.fetch_message.return_value.attachments)
        known.fetch_message.assert_not_awaited()
        cog.store.advance_forum.assert_called_once()
        cog.publisher.staff_alert.assert_not_awaited()
        cog.publisher.publish_approved.assert_not_awaited()
        assert cog.submission_notice_ready
    asyncio.run(check())


def test_forum_thread_listing_includes_archived_posts_and_filters_other_forums(monkeypatch):
    cog, _, _ = make_cog(monkeypatch)
    forum = forum_channel()
    active, archived, other = review_thread(500), review_thread(501), review_thread(502)
    other.parent_id = 999
    forum.threads = [active, other]

    async def history(**kwargs):
        yield archived

    forum.archived_threads = history
    assert [thread.id for thread in asyncio.run(cog.forum_threads(forum))] == [500, 501]


def test_forum_reactions_route_to_saved_thread_and_ignore_sibling_posts(monkeypatch):
    cog, _, _ = make_cog(monkeypatch)
    thread = review_thread()
    row = saved_row()
    row["review"]["source_channel_id"] = "500"
    cog.store.pending.return_value = [row]
    cog.store.respond.return_value = "saved"
    cog.publisher.resolve_channel.return_value = thread
    cog.evaluate = AsyncMock()
    payload = SimpleNamespace(guild_id=123, channel_id=500, message_id=900, emoji=module.SUPPORT, user_id=43)
    asyncio.run(cog.reaction(payload, added=True))
    cog.store.respond.assert_called_once_with(500, 43, "yes_add")
    assert cog.evaluate.call_args.args[0] is thread
    cog.store.respond.reset_mock()
    payload.channel_id = 501
    asyncio.run(cog.reaction(payload, added=True))
    cog.store.respond.assert_not_called()


def test_restart_recovery_resolves_and_reopens_saved_thread(monkeypatch):
    cog, _, _ = make_cog(monkeypatch)
    row, thread = saved_row(), review_thread()
    row["review"]["source_channel_id"] = "500"
    cog.store.get.return_value = row
    thread.archived = True
    cog.publisher.resolve_channel.return_value = thread
    cog.reconcile_reactions = AsyncMock()
    asyncio.run(cog.process_row(forum_channel(), row, reconcile=True))
    cog.publisher.resolve_channel.assert_awaited_with(500, "CAPPER_BALLOT_CHANNEL_ID", required=True)
    thread.edit.assert_awaited_once()
    cog.reconcile_reactions.assert_awaited_once_with(thread, row)


def test_nightly_forum_cleanup_records_before_deleting_and_preserves_active_posts(monkeypatch):
    cog, _, _ = make_cog(monkeypatch)
    forum = forum_channel()
    completed, active, interrupted, unknown = [review_thread(value) for value in (500, 501, 502, 503)]
    row = saved_row()
    row.update(state="published")
    row["review"]["source_channel_id"] = "500"
    pending = deepcopy(row)
    pending.update(message_id="501", state="pending")
    pending["review"]["source_channel_id"] = "501"
    processing = deepcopy(row)
    processing.update(message_id="502", state="processing")
    processing["review"]["source_channel_id"] = "502"
    cog.publisher.resolve_channel.return_value = forum
    cog.store.pending.return_value = [pending]
    cog.store.interrupted.return_value = [processing]
    cog.store.find.side_effect = lambda value: row if value == 500 else None
    cog.store.get.return_value = row
    cog.forum_threads = AsyncMock(return_value=[completed, active, interrupted, unknown])
    events = []

    async def record(*args):
        events.append("record")

    async def delete(**kwargs):
        events.append("delete")

    cog.records = Mock(save=AsyncMock(side_effect=record))
    completed.delete.side_effect = delete
    asyncio.run(cog.daily_cleanup.coro(cog))
    assert events == ["record", "delete"]
    for thread in (active, interrupted, unknown):
        thread.delete.assert_not_awaited()


def test_record_failure_prevents_source_deletion_and_alerts_staff(monkeypatch):
    cog, _, _ = make_cog(monkeypatch)
    thread = review_thread()
    row = saved_row()
    row["state"] = "rejected"
    row["review"]["source_channel_id"] = "500"
    cog.publisher.resolve_channel.return_value = forum_channel()
    cog.forum_threads = AsyncMock(return_value=[thread])
    cog.store.find.return_value = row
    cog.store.get.return_value = row
    cog.records = Mock(save=AsyncMock(side_effect=RuntimeError("record forum unavailable")))
    asyncio.run(cog.daily_cleanup.coro(cog))
    thread.delete.assert_not_awaited()
    cog.publisher.staff_alert.assert_awaited_once()


def test_valid_source_detects_edits_even_when_text_was_restored(monkeypatch):
    cog, channel, _ = make_cog(monkeypatch)
    source = SimpleNamespace(author=SimpleNamespace(id=42), content="", edited_at=datetime.now(timezone.utc),
                             attachments=[SimpleNamespace(id=700)])
    channel.fetch_message.return_value = source
    assert not asyncio.run(CapperSlipReviews.valid_source(cog, channel, saved_row()))


def test_slow_publication_does_not_block_another_reviews_deadline(monkeypatch):
    async def check():
        cog, channel, _ = make_cog(monkeypatch)
        first = saved_row()
        second = deepcopy(first)
        second["message_id"] = "501"
        cog.store.get.side_effect = lambda message_id: first if message_id == 500 else second
        started, release, second_done = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def evaluate(_, row):
            if row["message_id"] == "500":
                started.set()
                await release.wait()
            else:
                second_done.set()

        cog.evaluate = evaluate
        first_task = asyncio.create_task(cog.process_row(channel, first))
        await started.wait()
        try:
            await asyncio.wait_for(cog.process_row(channel, second), timeout=1)
            assert second_done.is_set()
        finally:
            release.set()
            await first_task
    asyncio.run(check())


def test_inflight_image_extraction_is_not_treated_as_interrupted_intake(monkeypatch):
    cog, channel, _ = make_cog(monkeypatch)
    row = saved_row()
    row["review_message_id"] = None
    row["review"].pop("parsed")
    cog.intake_ids.add(500)
    asyncio.run(cog.evaluate(channel, row))
    cog.store.fail.assert_not_called()
    cog.valid_source.assert_not_awaited()


def test_testing_mode_requires_reaction_recovery_before_live_processing_resumes(monkeypatch):
    cog, _, _ = make_cog(monkeypatch)
    cog.publisher.get_testing.return_value = True
    asyncio.run(cog.process_reviews.coro(cog))
    assert cog.needs_reconciliation
    cog.store.finalize.assert_not_called()
    cog.store.pending.assert_not_called()
