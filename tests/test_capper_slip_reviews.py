import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import src.capper_slip_reviews as module
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
    channel = SimpleNamespace(
        guild=SimpleNamespace(id=123), fetch_message=AsyncMock(), send=AsyncMock(), purge=AsyncMock(),
    )
    prompt = SimpleNamespace(id=900, jump_url="https://discord.com/channels/123/100/900",
                             edit=AsyncMock(), add_reaction=AsyncMock(), clear_reactions=AsyncMock())
    channel.fetch_message.return_value = prompt
    store = Mock(get=Mock(return_value=saved_row()), finalize=Mock(return_value=None),
                 pending=Mock(return_value=[]), interrupted=Mock(return_value=[]))
    cog = CapperSlipReviews(
        Mock(user=SimpleNamespace(id=1), is_ready=Mock(return_value=True)), store=store,
        official=Mock(), resolve_channel=AsyncMock(return_value=channel), is_tracked_operator=Mock(return_value=True),
        staff_alert=AsyncMock(), get_testing=Mock(return_value=False),
        tracked_operators=AsyncMock(return_value={42, 43, 44, 45, 46}), images=Mock(),
        request_insight=AsyncMock(return_value=True),
    )
    cog.needs_reconciliation = False
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
        image = SimpleNamespace(id=700, content_type="image/jpeg", url="https://cdn/slip", read=AsyncMock(return_value=b"slip"))
        source = SimpleNamespace(
            id=500, author=SimpleNamespace(id=42), content="Wolves ML", attachments=[image],
            edited_at=None, channel=channel, jump_url="https://discord.com/channels/123/100/500",
        )
        cog.publisher.images.extract_play.return_value = {"legs": [{"selection": "Wolves ML", "odds": 100}]}
        channel.send.return_value = SimpleNamespace(id=900)
        await cog.intake(source, [image])
        review = cog.store.register.call_args.args[1]
        assert review["slip_base64"] == "c2xpcA=="
        sent = channel.send.call_args.kwargs
        assert sent["file"].filename == "slip-500.jpg"
        assert sent["embed"].image.url == "attachment://slip-500.jpg"
        cog.store.update_review.assert_called_with(500, review_message_id="900")
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
