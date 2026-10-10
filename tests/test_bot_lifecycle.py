import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest
from discord.ext import commands, tasks

import src.bot as module
from src.data_sync import DataSync


def test_close_cancels_and_awaits_component_jobs_without_touching_other_bots(monkeypatch):
    async def check():
        bot = module.OfficialBot(command_prefix="!", intents=discord.Intents.none())
        started = asyncio.Event()
        stopped = asyncio.Event()

        async def work():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        task = asyncio.create_task(work())
        await started.wait()
        reconcile = Mock(get_task=Mock(return_value=task), cancel=Mock(side_effect=task.cancel))
        owned = SimpleNamespace(bot=bot, reconcile=reconcile)
        unrelated = SimpleNamespace(bot=object(), reconcile=Mock())
        monkeypatch.setattr(module, "whop_membership_sync", owned)
        monkeypatch.setattr(module, "member_bet_vault", unrelated)
        await bot.close()
        assert task.done() and task.cancelled() and stopped.is_set()
        reconcile.cancel.assert_called_once()
        unrelated.reconcile.cancel.assert_not_called()
        assert bot.is_closed()
        await bot.close()
    asyncio.run(check())


def test_close_stops_vault_persistent_view(monkeypatch):
    async def check():
        bot = module.OfficialBot(command_prefix="!", intents=discord.Intents.none())
        vault = module.MemberBetVault(bot, 123, service=Mock(), membership=Mock())
        monkeypatch.setattr(module, "member_bet_vault", vault)
        monkeypatch.setattr(module, "whop_membership_sync", None)
        assert not vault.view.is_finished()
        await bot.close()
        assert vault.view.is_finished()
    asyncio.run(check())


def test_close_awaits_cog_loops_and_data_startup_tasks(monkeypatch):
    monkeypatch.setattr(module, "member_bet_vault", None)
    monkeypatch.setattr(module, "whop_membership_sync", None)

    class Worker(commands.Cog):
        def __init__(self):
            self.started = asyncio.Event()
            self.stopped = asyncio.Event()

        @tasks.loop(seconds=60)
        async def job(self):
            self.started.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.stopped.set()

        def cog_unload(self):
            self.job.cancel()

    async def check():
        bot = module.OfficialBot(command_prefix="!", intents=discord.Intents.none())
        worker = Worker()
        await bot.add_cog(worker)
        worker.job.start()
        await worker.started.wait()
        loop_task = worker.job.get_task()
        sync = DataSync(bot, nfl=Mock(), multi=Mock(), enabled=False, staff_alert=AsyncMock())
        await bot.add_cog(sync)
        started, stopped = asyncio.Event(), asyncio.Event()

        async def initial():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        initial_task = asyncio.create_task(initial())
        sync.initial_tasks["nfl"] = initial_task
        await started.wait()
        await bot.close()
        assert worker.stopped.is_set() and stopped.is_set()
        assert loop_task.done() and loop_task.cancelled()
        assert initial_task.done() and initial_task.cancelled()
        assert not bot.cogs
    asyncio.run(check())


def test_ready_reconnect_does_not_duplicate_components_and_shutdown_does_not_restart(monkeypatch):
    bot = Mock(user="Test", _shutdown_started=False)
    monkeypatch.setattr(module, "bot", bot)
    loops = []
    for name in ("whop_membership_sync", "member_bet_vault"):
        loop = Mock(is_running=Mock(return_value=False))
        loop.start.side_effect = lambda loop=loop: setattr(loop.is_running, "return_value", True)
        monkeypatch.setattr(module, name, SimpleNamespace(reconcile=loop))
        loops.append(loop)
    asyncio.run(module.on_ready())
    asyncio.run(module.on_ready())
    for loop in loops:
        loop.start.assert_called_once()
        loop.is_running.return_value = False
    bot._shutdown_started = True
    asyncio.run(module.on_ready())
    for loop in loops:
        loop.start.assert_called_once()


@pytest.mark.parametrize("error", [RuntimeError("startup failed"), asyncio.CancelledError()])
def test_main_closes_client_when_start_fails_or_is_cancelled(monkeypatch, error):
    class Client:
        def __init__(self):
            self.enter = AsyncMock()
            self.close = AsyncMock()
            self.start = AsyncMock(side_effect=error)

        async def __aenter__(self):
            await self.enter()
            return self

        async def __aexit__(self, *args):
            await self.close()

    client = Client()
    monkeypatch.setattr(module, "bot", client)
    monkeypatch.setattr(module, "DISCORD_TOKEN", "test-token")
    with pytest.raises(type(error)):
        asyncio.run(module.main())
    client.enter.assert_awaited_once()
    client.close.assert_awaited_once()
    client.start.assert_awaited_once_with("test-token")


def test_main_missing_token_never_attempts_login(monkeypatch):
    client = Mock(start=AsyncMock())
    monkeypatch.setattr(module, "bot", client)
    monkeypatch.setattr(module, "DISCORD_TOKEN", "")
    with pytest.raises(RuntimeError, match="not configured"):
        asyncio.run(module.main())
    client.start.assert_not_awaited()


def test_command_sync_failure_cleans_up_partially_loaded_bot(monkeypatch):
    monkeypatch.setattr(module, "GUILD_ID", None)
    monkeypatch.setattr(module, "PAID_MEMBER_ROLE_ID", None)
    monkeypatch.setattr(module, "member_bet_vault", None)
    monkeypatch.setattr(module, "whop_membership_sync", None)
    monkeypatch.setattr(module, "DISCORD_TOKEN", "test-token")

    async def check():
        bot = module.OfficialBot(command_prefix="!", intents=discord.Intents.none())
        monkeypatch.setattr(module, "bot", bot)
        bot.tree.sync = AsyncMock(side_effect=RuntimeError("sync failed"))

        async def start(token):
            await bot.setup_hook()

        bot.start = start
        with pytest.raises(RuntimeError, match="sync failed"):
            await module.main()
        assert bot.is_closed() and bot._shutdown_started
        assert not bot.cogs
        assert not bot.extra_events.get("on_interaction")
    asyncio.run(check())
