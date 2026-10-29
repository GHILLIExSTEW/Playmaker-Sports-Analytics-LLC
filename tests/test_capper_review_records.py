import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

import src.capper_review_records as module
import src.forum_health as health
from src.capper_review_records import ReviewRecords, record_content, record_text


def ballot():
    return {
        "message_id": "500", "state": "published", "votes": {"yes": 3, "no": 0, "pass": 1},
        "created_at": "2026-10-29T02:00:00+00:00",
        "review": {
            "author_id": "42", "author_name": "Capper", "post_title": "Wolves ML",
            "source_channel_id": "500", "slip_base64": "c2xpcA==", "image_filename": "slip-500.png",
            "parsed": {"legs": [{"selection": "Wolves ML", "odds": 100}]},
        },
    }


def play(status="open"):
    return {"id": 7, "status": status, "units": 2, "odds": 100, "message_id": "800"}


def make_records(monkeypatch):
    monkeypatch.setattr(module, "GUILD_ID", 123)
    monkeypatch.setattr(health, "GUILD_ID", 123)
    forum = Mock(spec=discord.ForumChannel)
    forum.id = module.CAPPER_RECORD_CHANNEL_ID
    default = SimpleNamespace(id=0, permissions=discord.Permissions.none(), is_bot_managed=lambda: False)
    forum.guild = SimpleNamespace(id=123, default_role=default, me=SimpleNamespace(id=1), roles=[default],
                                  owner_id=99, filesize_limit=10 * 1024 * 1024, chunked=False)
    forum.permissions_for.return_value = discord.Permissions.none()
    forum.permissions_for.side_effect = lambda target: forum.permissions_for.return_value if target is default else discord.Permissions.all()
    forum.overwrites = {}
    forum.flags = SimpleNamespace(require_tag=False)
    thread = Mock(spec=discord.Thread)
    thread.id, thread.parent_id = 700, forum.id
    thread.guild = forum.guild
    thread.archived, thread.locked = False, False
    thread.edit = AsyncMock(return_value=thread)
    message = SimpleNamespace(id=900, author=SimpleNamespace(id=1), content="", attachments=[Mock(), Mock()])

    async def send(content, **kwargs):
        message.content = content
        message.saved_files = [(file.filename, file.fp.read()) for file in kwargs["files"]]
        return message

    async def edit(**kwargs):
        message.content = kwargs["content"]

    message.edit = AsyncMock(side_effect=edit)
    thread.send = AsyncMock(side_effect=send)
    thread.fetch_message = AsyncMock(return_value=message)
    forum.create_thread = AsyncMock(return_value=SimpleNamespace(thread=thread))
    store = Mock(day=Mock(return_value=None), record=Mock(return_value=None), play=Mock(return_value=play()),
                 snapshots=Mock(return_value=[]), claim=Mock(return_value=True))
    resolver = AsyncMock(side_effect=lambda channel_id, *args, **kwargs: forum if channel_id == forum.id else thread)
    records = ReviewRecords(SimpleNamespace(user=SimpleNamespace(id=1)), resolver, store=store)
    return records, forum, thread, message, store


def test_record_contains_original_author_full_bet_decision_and_pending_result(monkeypatch):
    records, forum, thread, message, store = make_records(monkeypatch)
    asyncio.run(records.save(SimpleNamespace(id=500), ballot()))
    assert forum.create_thread.call_args.kwargs["name"] == "2026-10-28", "group by Eastern submission date, not deletion date"
    assert "Capper (Discord 42)" in message.content
    assert "Wolves ML | +100" in message.content
    assert "PUBLISHED" in message.content and "Pending" in message.content
    assert message.saved_files[0][0] == "review-500.txt"
    assert message.saved_files[1] == ("slip-500.png", b"slip")
    assert len(thread.fetch_message.call_args_list) == 1
    store.update.assert_called()


def test_existing_daily_post_and_record_are_reused_without_duplicate_sends(monkeypatch):
    records, forum, thread, _, store = make_records(monkeypatch)
    store.day.return_value = {"record_date": "2026-10-28", "forum_channel_id": str(forum.id), "thread_id": "700"}
    store.record.return_value = {"record_date": "2026-10-28", "record_message_id": "900"}
    asyncio.run(records.save(SimpleNamespace(id=500), ballot()))
    forum.create_thread.assert_not_awaited()
    thread.send.assert_not_awaited()


def test_interrupted_record_send_blocks_deletion_without_resending(monkeypatch):
    records, forum, thread, _, store = make_records(monkeypatch)
    store.day.return_value = {"forum_channel_id": str(forum.id), "thread_id": "700"}
    store.record.return_value = {"record_date": "2026-10-28", "record_message_id": "sending"}
    with pytest.raises(RuntimeError, match="interrupted"):
        asyncio.run(records.save(SimpleNamespace(id=500), ballot()))
    thread.send.assert_not_awaited()


def test_public_records_forum_is_rejected(monkeypatch):
    records, forum, thread, _, _ = make_records(monkeypatch)
    forum.permissions_for.return_value.view_channel = True
    with pytest.raises(RuntimeError, match="Unexpected role"):
        asyncio.run(records.save(SimpleNamespace(id=500), ballot()))
    thread.send.assert_not_awaited()


def test_failed_readback_preserves_original(monkeypatch):
    records, _, thread, message, _ = make_records(monkeypatch)
    message.attachments = []
    with pytest.raises(RuntimeError, match="could not be verified"):
        asyncio.run(records.save(SimpleNamespace(id=500), ballot()))
    thread.send.assert_awaited_once()


def test_pending_records_update_after_settlement_and_regrading_without_original_thread(monkeypatch):
    records, forum, thread, message, store = make_records(monkeypatch)
    row = ballot()
    store.snapshots.return_value = [{
        "message_id": "500", "thread_id": "700", "forum_channel_id": str(forum.id),
        "record_message_id": "900", "rendered_content": record_content(row, play()),
        "ballot": row, "play": play("win"),
    }]
    thread.archived = True
    asyncio.run(records.refresh())
    assert "Win" in message.content
    thread.edit.assert_awaited_once()
    thread.send.assert_not_awaited()
    store.snapshots.return_value[0].update(play=play("loss"), rendered_content=message.content)
    asyncio.run(records.refresh())
    assert "Loss" in message.content


def test_rejected_untracked_bet_is_not_claimed_as_a_loss():
    row = ballot()
    row["state"] = "rejected"
    text = record_text(row, None)
    assert "REJECTED" in text and "Not tracked" in text
    assert "Loss" not in text


def test_long_bets_retain_current_result_in_summary_and_all_legs_in_full_record():
    row = ballot()
    row["review"]["parsed"]["legs"] *= 200
    content = record_content(row, play("win"))
    assert len(content) <= 2000 and "Win" in content
    assert "200. Wolves ML" in record_text(row, play("win"))
    row["review"]["source_text"] = "@everyone" * 1000
    content = record_content(row, play("loss"))
    assert len(content) <= 2000 and "Loss" in content


def test_uncertain_publication_is_recorded_but_original_must_remain(monkeypatch):
    records, _, _, message, store = make_records(monkeypatch)
    store.play.return_value = {**play(), "message_id": None}
    with pytest.raises(RuntimeError, match="publication is uncertain"):
        asyncio.run(records.save(SimpleNamespace(id=500), ballot()))
    assert "not confirmed" in message.content


def test_discord_send_error_keeps_durable_sending_marker_for_reconciliation(monkeypatch):
    records, _, thread, _, store = make_records(monkeypatch)
    thread.send.side_effect = RuntimeError("connection lost after send")
    with pytest.raises(RuntimeError, match="connection lost"):
        asyncio.run(records.save(SimpleNamespace(id=500), ballot()))
    assert store.claim.call_args.args[1]["record_message_id"] == "sending"
    assert not any(call.args[0] == "capper_review_records" for call in store.update.call_args_list)


def test_lost_day_claim_never_creates_a_second_dated_post(monkeypatch):
    records, forum, thread, _, store = make_records(monkeypatch)
    store.claim.return_value = False
    with pytest.raises(RuntimeError, match="already claimed"):
        asyncio.run(records.save(SimpleNamespace(id=500), ballot()))
    forum.create_thread.assert_not_awaited()
    thread.send.assert_not_awaited()


def expired_record(forum):
    row = ballot()
    row["review"]["source_text"] = "private original note"
    return {
        "message_id": "500", "thread_id": "700", "forum_channel_id": str(forum.id), "record_message_id": "900",
        "rendered_content": "old record", "ballot": row, "play": None, "revision": 3,
        "source_deleted_at": datetime.now(timezone.utc).isoformat(),
        "evidence_eligible_at": (datetime.now(timezone.utc) - timedelta(days=366)).isoformat(),
        "evidence_removed_at": None,
    }


def test_retention_replaces_attachments_with_bet_summary_before_scrubbing_database(monkeypatch):
    records, forum, thread, message, store = make_records(monkeypatch)
    record = expired_record(forum)
    store.snapshots.return_value = [record]
    store.snapshot.return_value = record
    store.begin_expiry.return_value = True
    sequence = []

    async def edit(**kwargs):
        sequence.append("discord")
        message.content = kwargs["content"]
        message.attachments = [SimpleNamespace(filename=file.filename) for file in kwargs["attachments"]]
        text = kwargs["attachments"][0].fp.read().decode("utf-8")
        assert "Wolves ML" in text and "private original note" not in text
        assert message.attachments[0].filename == "bet-summary-500.txt"

    message.edit.side_effect = edit
    store.complete.side_effect = lambda *args, **kwargs: sequence.append("database") or True
    asyncio.run(records.refresh())
    assert sequence == ["discord", "database"]
    assert "private original note" not in message.content
    assert "365-day policy" in message.content
    store.complete.assert_called_once_with(record, message.content, remove_evidence=True)
    thread.send.assert_not_awaited()


def test_reopened_record_and_failed_expiry_claim_never_delete_attachments(monkeypatch):
    records, forum, _, message, store = make_records(monkeypatch)
    record = expired_record(forum)
    store.snapshots.return_value = [record]
    store.snapshot.return_value = {**record, "evidence_eligible_at": None}
    asyncio.run(records.refresh())
    store.begin_expiry.assert_not_called()
    assert "attachments" not in message.edit.call_args.kwargs
    message.edit.reset_mock()
    store.snapshot.return_value = record
    store.begin_expiry.return_value = False
    asyncio.run(records.refresh())
    message.edit.assert_not_awaited()


def test_failed_retention_readback_never_scrubs_database_originals(monkeypatch):
    records, forum, _, message, store = make_records(monkeypatch)
    record = expired_record(forum)
    store.snapshots.return_value = [record]
    store.snapshot.return_value = record
    store.begin_expiry.return_value = True
    with pytest.raises(RuntimeError, match="Could not update records"):
        asyncio.run(records.refresh())
    store.complete.assert_not_called()


def test_expiry_removes_bot_owned_published_cards_without_deleting_bet_messages(monkeypatch):
    records, forum, _, _, store = make_records(monkeypatch)
    record = expired_record(forum)
    record["play"] = play("win")
    store.deliveries.return_value = [{"channel_id": "1234", "message_id": "5678"}]
    published = SimpleNamespace(id=800, author=SimpleNamespace(id=1), attachments=[Mock()], embeds=[])

    async def edit(**kwargs):
        assert kwargs["attachments"] == []
        published.attachments, published.embeds = [], [kwargs["embed"]]

    published.edit = AsyncMock(side_effect=edit)
    channel = SimpleNamespace(guild=forum.guild, fetch_message=AsyncMock(return_value=published))
    records.resolve_channel = AsyncMock(return_value=channel)
    asyncio.run(records.remove_published_evidence(record))
    assert published.edit.await_count == 2
    assert len(published.embeds) == 1 and published.attachments == []
    assert "Wolves ML" in published.embeds[0].description
    assert "Capper" not in published.embeds[0].description
    assert "private original note" not in published.embeds[0].description
    assert "Discord 42" not in published.embeds[0].description


@pytest.mark.parametrize("missing", [True, False])
def test_old_source_deletion_recovery_requires_not_found_not_forbidden(monkeypatch, missing):
    records, forum, _, _, store = make_records(monkeypatch)
    record = expired_record(forum)
    record["source_deleted_at"] = None
    response = SimpleNamespace(status=404 if missing else 403, reason="test")
    error = discord.NotFound(response, "missing") if missing else discord.Forbidden(response, "denied")
    guild = SimpleNamespace(fetch_channel=AsyncMock(side_effect=error))
    records.bot.get_guild = lambda _: guild
    if missing:
        asyncio.run(records.check_source_deleted(record))
        store.source_deleted.assert_called_once_with("500")
        assert record["source_deleted_at"] is not None
    else:
        with pytest.raises(discord.Forbidden):
            asyncio.run(records.check_source_deleted(record))
        store.source_deleted.assert_not_called()
