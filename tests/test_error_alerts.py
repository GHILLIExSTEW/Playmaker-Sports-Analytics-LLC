import asyncio
from datetime import datetime, time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

import src.error_alerts as module


def make_router(monkeypatch, *, text_channel=False):
    monkeypatch.setattr(module, "ERROR_LOG_CHANNEL_ID", 123)
    monkeypatch.setattr(module, "GUILD_ID", 99)
    guild = SimpleNamespace(id=99, active_threads=AsyncMock(return_value=[]))
    parent = Mock(spec=discord.TextChannel if text_channel else discord.ForumChannel)
    parent.id, parent.guild = 123, guild

    async def archived_threads(**kwargs):
        for item in parent.saved_archived:
            yield item

    parent.saved_archived = []
    parent.archived_threads = archived_threads
    thread = Mock(spec=discord.Thread)
    thread.id, thread.parent_id, thread.owner_id = 456, 123, 42
    thread.name = f"Errors {datetime.now(module.TRACKER_TIMEZONE).date().isoformat()}"
    thread.archived, thread.locked = False, False
    thread.send = AsyncMock()
    thread.edit = AsyncMock(return_value=thread)
    parent.create_thread = AsyncMock(return_value=SimpleNamespace(thread=thread))
    message = SimpleNamespace(create_thread=AsyncMock(return_value=thread))
    parent.send = AsyncMock(return_value=message)
    resolve = AsyncMock(return_value=parent)
    router = module.ErrorAlerts(SimpleNamespace(user=SimpleNamespace(id=42)), resolve)
    return router, parent, thread, message, resolve


def test_creates_one_forum_thread_and_records_each_alert(monkeypatch):
    router, parent, thread, _, _ = make_router(monkeypatch)

    async def check():
        await asyncio.gather(router.send("First error"), router.send("Second error"))

    asyncio.run(check())

    parent.create_thread.assert_awaited_once()
    assert parent.create_thread.call_args.kwargs["name"] == thread.name
    assert thread.send.await_count == 2
    assert thread.send.call_args.kwargs["allowed_mentions"].everyone is False


def test_daily_thread_is_scheduled_at_eastern_midnight(monkeypatch):
    router, _, _, _, _ = make_router(monkeypatch)
    assert router.daily_thread.time == [time(0, tzinfo=module.TRACKER_TIMEZONE)]


@pytest.mark.parametrize("archived", [False, True])
def test_reuses_existing_daily_thread_after_restart(monkeypatch, archived):
    router, parent, thread, _, _ = make_router(monkeypatch)
    thread.archived = archived
    if archived:
        parent.saved_archived = [thread]
    else:
        parent.guild.active_threads.return_value = [thread]

    result = asyncio.run(router.ensure_thread())

    assert result is thread
    parent.create_thread.assert_not_awaited()
    assert thread.edit.await_count == int(archived)


def test_existing_thread_id_uses_its_parent_text_channel(monkeypatch):
    router, parent, thread, message, resolve = make_router(monkeypatch, text_channel=True)
    configured_thread = Mock(spec=discord.Thread)
    configured_thread.parent_id = 123
    resolve.side_effect = [configured_thread, parent]

    asyncio.run(router.send("Failure"))

    resolve.assert_any_await(123, "error thread parent", required=True)
    parent.send.assert_awaited_once()
    message.create_thread.assert_awaited_once_with(name=thread.name)
    thread.send.assert_awaited_once()


def test_new_eastern_day_creates_another_thread(monkeypatch):
    router, parent, _, _, _ = make_router(monkeypatch)

    class Clock(datetime):
        day = 27

        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, cls.day, 0, 0, tzinfo=tz)

    monkeypatch.setattr(module, "datetime", Clock)

    async def check():
        await router.ensure_thread()
        Clock.day = 28
        await router.ensure_thread()

    asyncio.run(check())

    assert [call.kwargs["name"] for call in parent.create_thread.await_args_list] == [
        "Errors 2026-11-27", "Errors 2026-11-28",
    ]


def test_duplicate_daily_threads_fail_closed(monkeypatch):
    router, parent, thread, _, _ = make_router(monkeypatch)
    duplicate = Mock(spec=discord.Thread)
    duplicate.id, duplicate.parent_id, duplicate.owner_id = 457, 123, 42
    duplicate.name = thread.name
    parent.guild.active_threads.return_value = [thread, duplicate]

    with pytest.raises(RuntimeError, match="Multiple daily error threads"):
        asyncio.run(router.send("Failure"))

    parent.create_thread.assert_not_awaited()
    thread.send.assert_not_awaited()


def test_failed_send_invalidates_cached_thread_without_replaying_alert(monkeypatch):
    router, parent, thread, _, _ = make_router(monkeypatch)
    error = discord.HTTPException(SimpleNamespace(status=500, reason="unavailable"), "unavailable")
    thread.send.side_effect = [error, None]

    async def check():
        with pytest.raises(discord.HTTPException):
            await router.send("Failure")
        parent.guild.active_threads.return_value = [thread]
        await router.send("Later failure")

    asyncio.run(check())

    assert thread.send.await_count == 2
    parent.create_thread.assert_awaited_once()
    assert parent.guild.active_threads.await_count == 2
