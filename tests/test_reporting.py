import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

import src.bot as bot_module
import src.reporting as reporting_module
from src.reporting import Reporting, TRACKER_TIMEZONE, build_recap_embed
from src.services.team_ranking_service import TeamRankingService


def make_reporting():
    cog = Reporting(
        Mock(is_ready=Mock(return_value=False), wait_until_ready=AsyncMock()),
        features=Mock(history=Mock(return_value=([], [], {})), vault_bets=Mock(return_value=[])),
        rankings=Mock(fetch_rankings_from_supabase=Mock(return_value=[])),
        get_tracker_start=Mock(return_value=None),
        settlement_channels=Mock(return_value=[]),
        reconcile_reactions=AsyncMock(return_value=2),
        tracked_operators=AsyncMock(return_value={42}),
        resolve_channel=AsyncMock(return_value=SimpleNamespace(send=AsyncMock())),
        can_manage_plays=Mock(return_value=True),
        staff_alert=AsyncMock(),
    )
    cog.refresh_tracker = AsyncMock(return_value=2)
    cog.tracker_rows = Mock(return_value=([], []))
    cog.tracker_embed = Mock(return_value=(discord.Embed(title="Summary"), [], ""))
    return cog


def interaction(channel_id=777):
    return SimpleNamespace(
        user=SimpleNamespace(id=42), guild=None, channel_id=channel_id,
        response=SimpleNamespace(
            defer=AsyncMock(), send_message=AsyncMock(), is_done=Mock(return_value=True),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
    )


def test_reporting_registers_commands_before_bot_sync(monkeypatch):
    monkeypatch.setattr(bot_module, "GUILD_ID", None)
    monkeypatch.setattr(bot_module, "PAID_MEMBER_ROLE_ID", None)
    monkeypatch.setattr(bot_module, "whop_membership_sync", None)
    monkeypatch.setattr(bot_module, "member_bet_vault", None)

    async def check():
        bot = bot_module.OfficialBot(command_prefix="!", intents=discord.Intents.none())

        async def sync():
            assert isinstance(bot.get_cog("Reporting"), Reporting)
            assert {"recap", "rankings", "summary", "update_tracker", "start", "membership_status", "mystats", "vault_leaderboard"} <= {
                command.name for command in bot.tree.get_commands()
            }
            assert bot.get_cog("MemberOnboarding") is not None
            assert [command.name for command in bot.tree.get_commands()].count("results") == 1
            assert [command.name for command in bot.tree.get_commands()].count("play_results") == 1
            assert bot.get_cog("MemberActivity") is not None
            command_names = [command.name for command in bot.tree.get_commands()]
            assert command_names.count("mystats") == 1
            assert command_names.count("vault_leaderboard") == 1
            assert bot.get_cog("Administration") is not None
            website = bot.get_cog("WebsiteSync")
            assert website is not None
            assert not website.website_capper_sync.is_running()
            assert bot.extra_events["on_interaction"].count(website.on_insight_interaction) == 1
            assert command_names.count("request_insights") == 1
            assert bot.get_cog("PlaySubmission") is not None
            presentation = bot.get_cog("PlayPresentation")
            assert presentation is not None
            assert bot.extra_events["on_interaction"].count(presentation.on_play_feature_interaction) == 1
            settlement = bot.get_cog("PlaySettlement")
            assert settlement is not None
            assert not settlement.auto_settle_suggestions.is_running()
            assert bot.extra_events["on_interaction"].count(settlement.on_suggestion_interaction) == 1
            for name in ("settle", "regrade", "unsettle", "edit_play"):
                assert command_names.count(name) == 1
            data_sync = bot.get_cog("DataSync")
            assert data_sync is not None
            assert data_sync.enabled == bool(bot_module.API_SPORTS_KEY)
            assert data_sync.initial_tasks == {}
            for name in ("daily_nfl_data_sync", "live_nfl_score_sync", "daily_multi_sport_api_sync", "live_multi_sport_api_sync"):
                assert not getattr(data_sync, name).is_running()
            assert bot.extra_events["on_ready"].count(data_sync.on_ready) == 1
            assert command_names.count("play") == 1
            for name in ("api", "test", "testing", "tracker_start"):
                assert command_names.count(name) == 1
            assert not bot.get_cog("Reporting").hourly_tracker_update.is_running()
            return bot.tree.get_commands()

        bot.tree.sync = AsyncMock(side_effect=sync)
        try:
            await bot.setup_hook()
            bot.tree.sync.assert_awaited_once()
        finally:
            await bot.close()

    asyncio.run(check())


def test_reporting_jobs_start_once_on_reconnect_and_cancel_on_unload():
    cog = make_reporting()
    for name in ("hourly_tracker_update", "scheduled_recaps"):
        loop = Mock(is_running=Mock(return_value=False))
        loop.start.side_effect = lambda loop=loop: setattr(loop.is_running, "return_value", True)
        setattr(cog, name, loop)

    async def check():
        await cog.cog_load()
        cog.hourly_tracker_update.start.assert_not_called()
        await cog.on_ready()
        await cog.on_ready()
        cog.cog_unload()

    asyncio.run(check())
    for loop in (cog.hourly_tracker_update, cog.scheduled_recaps):
        loop.start.assert_called_once()
        loop.cancel.assert_called_once()


def test_loading_reporting_into_ready_bot_starts_jobs():
    cog = make_reporting()
    cog.bot.is_ready.return_value = True
    cog.start_jobs = Mock()
    asyncio.run(cog.cog_load())
    cog.start_jobs.assert_called_once()


def test_hourly_job_reports_failures():
    cog = make_reporting()
    cog.refresh_tracker.side_effect = RuntimeError("database unavailable")
    asyncio.run(cog.hourly_tracker_update.coro(cog))
    cog.staff_alert.assert_awaited_once()
    assert cog.staff_alert.call_args.args[0] == "tracker_update"
    assert "database unavailable" in cog.staff_alert.call_args.args[1]


@pytest.mark.parametrize("now, expected", [
    (datetime(2026, 6, 1, 10, tzinfo=TRACKER_TIMEZONE), ["weekly", "monthly"]),
    (datetime(2026, 6, 8, 10, tzinfo=TRACKER_TIMEZONE), ["weekly"]),
    (datetime(2026, 7, 1, 10, tzinfo=TRACKER_TIMEZONE), ["monthly"]),
    (datetime(2026, 6, 9, 10, tzinfo=TRACKER_TIMEZONE), []),
])
def test_recap_schedule_preserves_completed_period_dates(monkeypatch, now, expected):
    monkeypatch.setattr(reporting_module, "datetime", SimpleNamespace(now=lambda zone: now))
    cog = make_reporting()
    cog.post_recap = AsyncMock()
    asyncio.run(cog.scheduled_recaps.coro(cog))
    assert [call.args[0] for call in cog.post_recap.await_args_list] == expected


def test_failed_weekly_recap_does_not_skip_monthly(monkeypatch):
    now = datetime(2026, 6, 1, 10, tzinfo=TRACKER_TIMEZONE)
    monkeypatch.setattr(reporting_module, "datetime", SimpleNamespace(now=lambda zone: now))
    cog = make_reporting()
    cog.post_recap = AsyncMock(side_effect=[RuntimeError("post failed"), None])
    asyncio.run(cog.scheduled_recaps.coro(cog))
    assert cog.post_recap.await_count == 2
    assert cog.staff_alert.call_args.args[0] == "recap_weekly"


def test_recap_permission_and_private_preview():
    cog = make_reporting()
    request = interaction()
    cog.can_manage_plays.return_value = False
    period = discord.app_commands.Choice(name="Weekly", value="weekly")
    asyncio.run(cog.recap_command.callback(cog, request, period))
    assert request.response.send_message.call_args.kwargs["ephemeral"] is True
    request.response.defer.assert_not_awaited()

    cog.can_manage_plays.return_value = True
    request = interaction()
    asyncio.run(cog.recap_command.callback(cog, request, period))
    assert request.followup.send.call_args.kwargs["ephemeral"] is True
    assert request.followup.send.call_args.kwargs["embed"].title == "📊 Weekly Recap"
    cog.resolve_channel.assert_not_awaited()


def test_recap_post_routes_to_results_channel(monkeypatch):
    monkeypatch.setattr(reporting_module, "RESULT_CHANNEL_ID", 999)
    cog = make_reporting()
    request = interaction()
    period = discord.app_commands.Choice(name="Monthly", value="monthly")
    asyncio.run(cog.recap_command.callback(cog, request, period, True))
    cog.resolve_channel.assert_awaited_once_with(999, "RESULT_CHANNEL_ID", required=True)
    cog.resolve_channel.return_value.send.assert_awaited_once()
    assert "<#999>" in request.followup.send.call_args.args[0]


@pytest.mark.parametrize("command", ["rankings_command", "summary_command"])
def test_team_reports_preserve_channel_restrictions(monkeypatch, command):
    monkeypatch.setattr(reporting_module, "TEAM_STATS_CHANNEL_ID", 888)
    cog = make_reporting()
    request = interaction()
    asyncio.run(getattr(cog, command).callback(cog, request))
    assert "<#888>" in request.response.send_message.call_args.args[0]
    request.response.defer.assert_not_awaited()
    cog.tracker_rows.assert_not_called()
    cog.rankings.fetch_rankings_from_supabase.assert_not_called()


def test_summary_uses_operator_filtered_tracker_rows(monkeypatch):
    monkeypatch.setattr(reporting_module, "TEAM_STATS_CHANNEL_ID", 777)
    cog = make_reporting()
    plays = [{"id": 1, "user_id": 1}, {"id": 2, "user_id": 2}]
    users = [{"id": 1, "discord_user_id": "42"}, {"id": 2, "discord_user_id": "43"}]
    cog.tracker_rows.return_value = (plays, users)
    cog.tracker_embed.return_value = (
        discord.Embed(title="Playmaker Picks | Unit Summary"), ["Ace +2u"], "**Ace** · 1-0",
    )
    request = interaction()
    asyncio.run(cog.summary_command.callback(cog, request))
    cog.tracker_embed.assert_called_once_with([plays[0]], users)
    assert request.followup.send.await_count == 2
    summary = request.followup.send.await_args_list[0].kwargs["embed"]
    assert summary.fields[0].value == "**Ace** · 1-0"
    assert summary.footer.text == "Updated on request • Eastern Time"
    assert request.followup.send.await_args_list[1].kwargs["embed"].description == "Ace +2u"


def test_summary_database_failure_is_explicit(monkeypatch):
    monkeypatch.setattr(reporting_module, "TEAM_STATS_CHANNEL_ID", 777)
    cog = make_reporting()
    cog.tracker_rows.side_effect = RuntimeError("database unavailable")
    request = interaction()
    asyncio.run(cog.summary_command.callback(cog, request))
    assert "Could not build the summary" in request.followup.send.call_args.args[0]
    assert request.followup.send.call_args.kwargs["ephemeral"] is True
    cog.tracker_embed.assert_not_called()


def test_rankings_distinguishes_database_failure_from_empty_data(monkeypatch):
    monkeypatch.setattr(reporting_module, "TEAM_STATS_CHANNEL_ID", 777)
    cog = make_reporting()
    request = interaction()
    asyncio.run(cog.rankings_command.callback(cog, request))
    assert request.followup.send.call_args.kwargs["embed"].description == "No settled results yet."

    cog.rankings.fetch_rankings_from_supabase.side_effect = RuntimeError("database unavailable")
    request = interaction()
    asyncio.run(cog.rankings_command.callback(cog, request))
    assert "Could not load team rankings" in request.followup.send.call_args.args[0]
    assert request.followup.send.call_args.kwargs["ephemeral"] is True


def test_ranking_service_propagates_database_failure(monkeypatch):
    monkeypatch.setattr(
        "src.services.team_ranking_service.supabase_service.select",
        Mock(side_effect=RuntimeError("database unavailable")),
    )
    with pytest.raises(RuntimeError, match="database unavailable"):
        TeamRankingService.fetch_rankings_from_supabase()


def test_update_tracker_preserves_permission_and_reconciliation_count(monkeypatch):
    monkeypatch.setattr(reporting_module, "RESULT_CHANNEL_ID", 999)
    cog = make_reporting()
    request = interaction()
    cog.can_manage_plays.return_value = False
    asyncio.run(cog.update_tracker_command.callback(cog, request))
    cog.refresh_tracker.assert_not_awaited()

    cog.can_manage_plays.return_value = True
    request = interaction()
    asyncio.run(cog.update_tracker_command.callback(cog, request))
    assert "Reconciled 2 result(s)" in request.followup.send.call_args.args[0]
    assert request.followup.send.call_args.kwargs["ephemeral"] is True


def test_recap_builder_keeps_empty_month_and_vault_lookup():
    service = Mock(history=Mock(return_value=([], [], {})), vault_bets=Mock(return_value=[]))
    embed = build_recap_embed(service, "monthly", datetime(2026, 6, 1, tzinfo=TRACKER_TIMEZONE))
    assert embed.title == "📊 Monthly Recap"
    assert embed.description == "**May 2026**\nNo settled plays this period."
    service.vault_bets.assert_called_once()
