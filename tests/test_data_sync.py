import asyncio
from datetime import time
from unittest.mock import AsyncMock, Mock

import pytest

from src.data_sync import DataSync
from src.reporting import TRACKER_TIMEZONE


LOOP_NAMES = (
    "daily_nfl_data_sync", "live_nfl_score_sync",
    "daily_multi_sport_api_sync", "live_multi_sport_api_sync",
)


def make_cog(enabled=True):
    return DataSync(
        Mock(is_ready=Mock(return_value=False), wait_until_ready=AsyncMock()),
        nfl=Mock(), multi=Mock(), enabled=enabled, staff_alert=AsyncMock(),
    )


def fake_loops(cog):
    loops = []
    for name in LOOP_NAMES:
        loop = Mock(is_running=Mock(return_value=False))
        loop.start.side_effect = lambda loop=loop: setattr(loop.is_running, "return_value", True)
        setattr(cog, name, loop)
        loops.append(loop)
    return loops


def test_disabled_api_gate_starts_no_jobs():
    cog = make_cog(enabled=False)
    cog.bot.is_ready.return_value = True
    loops = fake_loops(cog)

    async def check():
        await cog.cog_load()
        await cog.on_ready()
        await cog.on_ready()
    asyncio.run(check())
    for loop in loops:
        loop.start.assert_not_called()
    assert cog.initial_tasks == {}
    cog.nfl.sync_daily.assert_not_called()
    cog.multi.sync_daily.assert_not_called()


def test_reconnect_starts_each_loop_and_initial_sync_only_once():
    cog = make_cog()
    loops = fake_loops(cog)
    cog.initial_nfl_data_sync = AsyncMock()
    cog.initial_multi_sport_api_sync = AsyncMock()

    async def check():
        await cog.cog_load()
        for loop in loops:
            loop.start.assert_not_called()
        await cog.on_ready()
        first_tasks = dict(cog.initial_tasks)
        await asyncio.gather(*first_tasks.values())
        await cog.on_ready()
        assert cog.initial_tasks == first_tasks
        for loop in loops:
            loop.start.assert_called_once()
        cog.cog_unload()
        for loop in loops:
            loop.cancel.assert_called_once()
    asyncio.run(check())
    cog.initial_nfl_data_sync.assert_awaited_once()
    cog.initial_multi_sport_api_sync.assert_awaited_once()


def test_ready_hot_load_and_unload_cancel_owned_initial_tasks():
    cog = make_cog()
    cog.bot.is_ready.return_value = True
    loops = fake_loops(cog)

    async def check():
        started = [asyncio.Event(), asyncio.Event()]
        cancelled = []

        async def initial(index):
            started[index].set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(index)

        cog.initial_nfl_data_sync = lambda: initial(0)
        cog.initial_multi_sport_api_sync = lambda: initial(1)
        await cog.cog_load()
        await asyncio.gather(*(event.wait() for event in started))
        cog.cog_unload()
        results = await asyncio.gather(*cog.initial_tasks.values(), return_exceptions=True)
        assert all(isinstance(result, asyncio.CancelledError) for result in results)
        assert sorted(cancelled) == [0, 1]
        for loop in loops:
            loop.start.assert_called_once()
            loop.cancel.assert_called_once()
    asyncio.run(check())


def test_schedule_and_shared_ready_hook_match_existing_jobs():
    cog = make_cog()
    assert cog.daily_nfl_data_sync.time == [time(6, 10, tzinfo=TRACKER_TIMEZONE)]
    assert cog.daily_multi_sport_api_sync.time == [time(6, 25, tzinfo=TRACKER_TIMEZONE)]
    assert cog.live_nfl_score_sync.minutes == 15
    assert cog.live_multi_sport_api_sync.minutes == 15
    for name in LOOP_NAMES:
        assert getattr(cog, name)._before_loop == DataSync.before_data_sync
    asyncio.run(cog.before_data_sync())
    cog.bot.wait_until_ready.assert_awaited_once()


def test_real_loops_wait_for_ready_and_cancel_on_unload():
    cog = make_cog()

    async def check():
        waiting = asyncio.Event()

        async def wait():
            await waiting.wait()

        cog.bot.wait_until_ready.side_effect = wait
        cog.initial_nfl_data_sync = AsyncMock()
        cog.initial_multi_sport_api_sync = AsyncMock()
        cog.start_jobs()
        tasks = [getattr(cog, name).get_task() for name in LOOP_NAMES]
        await asyncio.sleep(0)
        assert all(getattr(cog, name).is_running() for name in LOOP_NAMES)
        cog.nfl.sync_daily.assert_not_called()
        cog.nfl.should_sync_live_scores.assert_not_called()
        cog.multi.sync_daily.assert_not_called()
        cog.multi.active_sports.assert_not_called()
        cog.cog_unload()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        assert all(isinstance(result, asyncio.CancelledError) for result in results)
        assert all(not getattr(cog, name).is_running() for name in LOOP_NAMES)
        await asyncio.gather(*cog.initial_tasks.values(), return_exceptions=True)
        cog.staff_alert.assert_not_awaited()
    asyncio.run(check())


@pytest.mark.parametrize("job,service", [
    ("daily_nfl_data_sync", "nfl"), ("daily_multi_sport_api_sync", "multi"),
    ("initial_nfl_data_sync", "nfl"), ("initial_multi_sport_api_sync", "multi"),
])
def test_daily_and_initial_jobs_call_existing_services(job, service):
    cog = make_cog()
    method = getattr(cog, job)
    coroutine = method.coro(cog) if job.startswith("daily") else method()
    asyncio.run(coroutine)
    getattr(cog, service).sync_daily.assert_called_once_with()
    other = cog.multi if service == "nfl" else cog.nfl
    other.sync_daily.assert_not_called()
    cog.staff_alert.assert_not_awaited()


@pytest.mark.parametrize("active", [False, True])
def test_nfl_live_job_preserves_game_gate(active):
    cog = make_cog()
    cog.nfl.should_sync_live_scores.return_value = active
    asyncio.run(cog.live_nfl_score_sync.coro(cog))
    cog.nfl.should_sync_live_scores.assert_called_once_with()
    if active:
        cog.nfl.sync_live_scores.assert_called_once_with()
    else:
        cog.nfl.sync_live_scores.assert_not_called()


@pytest.mark.parametrize("sports", [[], ["basketball", "hockey"]])
def test_multi_live_job_passes_only_active_sports(sports):
    cog = make_cog()
    cog.multi.active_sports.return_value = sports
    asyncio.run(cog.live_multi_sport_api_sync.coro(cog))
    cog.multi.active_sports.assert_called_once_with()
    if sports:
        cog.multi.sync_live_scores.assert_called_once_with(sports)
    else:
        cog.multi.sync_live_scores.assert_not_called()


@pytest.mark.parametrize("job,service,method,key", [
    ("daily_nfl_data_sync", "nfl", "sync_daily", "api_sports_daily_sync"),
    ("daily_multi_sport_api_sync", "multi", "sync_daily", "api_sports_multi_daily_sync"),
    ("initial_nfl_data_sync", "nfl", "sync_daily", "api_sports_initial_sync"),
    ("initial_multi_sport_api_sync", "multi", "sync_daily", "api_sports_multi_initial_sync"),
    ("live_nfl_score_sync", "nfl", "should_sync_live_scores", "api_sports_live_score_sync"),
    ("live_multi_sport_api_sync", "multi", "active_sports", "api_sports_multi_live_sync"),
    ("live_nfl_score_sync", "nfl", "sync_live_scores", "api_sports_live_score_sync"),
    ("live_multi_sport_api_sync", "multi", "sync_live_scores", "api_sports_multi_live_sync"),
])
def test_job_failures_log_and_alert_without_success(caplog, job, service, method, key):
    cog = make_cog()
    cog.nfl.should_sync_live_scores.return_value = True
    cog.multi.active_sports.return_value = ["hockey"]
    getattr(getattr(cog, service), method).side_effect = RuntimeError("provider unavailable")
    callback = getattr(cog, job)
    coroutine = callback() if job.startswith("initial") else callback.coro(cog)
    asyncio.run(coroutine)
    cog.staff_alert.assert_awaited_once()
    assert cog.staff_alert.call_args.args[0] == key
    assert f"{key}_failed" in caplog.text
    assert "provider unavailable" in caplog.text
    assert "_complete" not in caplog.text


def test_staff_alert_failure_is_logged_without_killing_scheduler(caplog):
    cog = make_cog()
    cog.nfl.sync_daily.side_effect = RuntimeError("provider unavailable")
    cog.staff_alert.side_effect = RuntimeError("Discord unavailable")
    asyncio.run(cog.daily_nfl_data_sync.coro(cog))
    assert "api_sports_daily_sync_failed" in caplog.text
    assert "data_sync_staff_alert_failed" in caplog.text


def test_failed_initial_job_is_not_retried_on_reconnect():
    cog = make_cog()
    fake_loops(cog)
    cog.nfl.sync_daily.side_effect = RuntimeError("provider unavailable")

    async def check():
        await cog.on_ready()
        await asyncio.gather(*cog.initial_tasks.values())
        await cog.on_ready()
        await asyncio.gather(*cog.initial_tasks.values())
        cog.cog_unload()
    asyncio.run(check())
    cog.nfl.sync_daily.assert_called_once()
    cog.multi.sync_daily.assert_called_once()
    cog.staff_alert.assert_awaited_once()
