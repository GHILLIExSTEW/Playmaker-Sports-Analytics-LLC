import asyncio
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

import src.reporting as reporting_module
from src.reporting import Reporting
from src.results import ResultsView, build_results_embed, filter_results, parse_results_dates
from src.services.play_features_service import EASTERN

NOW = datetime(2026, 10, 9, 12, tzinfo=EASTERN)
USERS = [{"id": 1, "discord_user_id": "42", "display_name": "Ace"},
         {"id": 2, "discord_user_id": "43", "display_name": "Other"}]
SPORTS = {1: "basketball", 2: "nfl"}


def play(play_id=1, **overrides):
    return {
        "id": play_id, "message_id": str(500 + play_id), "user_id": 1, "sport_id": 1,
        "units": 2, "odds": 150, "status": "win",
        "settled_at": "2026-10-09T04:00:00+00:00", "created_at": "2026-09-01T12:00:00+00:00",
        **overrides,
    }


def filter_rows(rows, **kwargs):
    return filter_results(rows, USERS, SPORTS, operator_ids={42}, now=NOW, **kwargs)


@pytest.mark.parametrize("start,end", [("20261009", None), ("2026-13-01", None), (None, "bad"), ("2026-10-10", "2026-10-09")])
def test_invalid_dates_are_explicit(start, end):
    with pytest.raises(ValueError):
        parse_results_dates(start, end)


def test_dates_accept_inclusive_same_day_and_missing_bounds():
    assert parse_results_dates(None, None) == (None, None)
    assert parse_results_dates("2026-10-09", "2026-10-09") == (date(2026, 10, 9), date(2026, 10, 9))


def test_filters_use_eastern_settlement_dates_not_creation_and_include_full_end_day():
    rows = [
        play(1, settled_at="2026-10-09T03:59:59+00:00"),
        play(2, settled_at="2026-10-09T04:00:00+00:00"),
        play(3, settled_at="2026-10-10T03:59:59+00:00"),
        play(4, settled_at="2026-10-10T04:00:00+00:00"),
    ]
    result = filter_results(
        rows, USERS, SPORTS, operator_ids={42}, start=date(2026, 10, 9), end=date(2026, 10, 9),
        now=datetime(2026, 10, 11, tzinfo=EASTERN),
    )
    assert [row["id"] for row in result] == [3, 2]


def test_only_settled_published_current_operator_rows_count_once_and_future_is_excluded():
    rows = [
        play(1), play(2, message_id="501"), play(3, message_id=None), play(4, status="open"),
        play(5, status="regraded"), play(6, user_id=2), play(7, settled_at="2026-10-10T12:00:00+00:00"),
    ]
    assert [row["id"] for row in filter_rows(rows)] == [1]


def test_filters_combine_sport_capper_result_and_sort_newest_then_id():
    rows = [play(1, status="loss"), play(2, status="loss", sport_id=2), play(3, status="loss", sport_id=2)]
    assert [row["id"] for row in filter_rows(rows, sport="nfl", capper_id=42, result="loss")] == [3, 2]
    assert not filter_rows(rows, capper_id=43)
    assert not filter_rows(rows, sport="hockey")


def test_missing_sport_is_unspecified_without_guessing():
    rows = [play(sport_id=999)]
    assert filter_rows(rows, sport="official") == rows
    assert not filter_rows(rows, sport="basketball")


def test_embed_totals_cover_all_pages_not_only_visible_rows():
    rows = [play(index) for index in range(1, 13)] + [play(13, status="partial"), play(14, status="void"), play(15, status="loss")]
    embed = build_results_embed(rows, USERS, SPORTS, "All history", 0, {1: "https://discord.com/channels/1/2/501"})
    assert len(embed.fields) == 10
    assert "W12 L1 V1 P1" in embed.description
    assert "+35.50u net" in embed.description
    assert "Original post" in embed.fields[0].value
    assert "Page 1/2" in embed.footer.text
    second = build_results_embed(rows, USERS, SPORTS, "All history", 1, {})
    assert len(second.fields) == 5 and second.description == embed.description
    assert "Original post" not in second.fields[0].value


def test_empty_results_display_zero_totals_and_clear_message():
    embed = build_results_embed([], USERS, SPORTS, "All history", 0, {})
    assert "No results match" in embed.description and "+0.00u" in embed.description
    assert not embed.fields


def interaction(user_id=42, guild_id=123):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id), guild=SimpleNamespace(id=guild_id),
        response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()), edit_original_response=AsyncMock(),
    )


def reporting():
    return Reporting(
        Mock(), features=Mock(history=Mock(return_value=([play()], USERS, SPORTS))),
        rankings=Mock(), get_tracker_start=Mock(), settlement_channels=Mock(return_value=[]),
        reconcile_reactions=AsyncMock(), tracked_operators=AsyncMock(return_value={42}),
        resolve_channel=AsyncMock(), can_manage_plays=Mock(), staff_alert=AsyncMock(),
    )


def test_command_is_private_and_uses_only_official_history(monkeypatch):
    monkeypatch.setattr(reporting_module, "GUILD_ID", 123)
    cog = reporting()
    request = interaction()
    asyncio.run(cog.results_command.callback(cog, request))
    request.response.defer.assert_awaited_once_with(ephemeral=True)
    cog.features.history.assert_called_once()
    cog.features.vault_bets.assert_not_called()
    assert request.followup.send.call_args.kwargs["ephemeral"]
    assert isinstance(request.followup.send.call_args.kwargs["view"], ResultsView)


@pytest.mark.parametrize("guild_id", [None, 999])
def test_command_rejects_wrong_server_before_data_access(monkeypatch, guild_id):
    monkeypatch.setattr(reporting_module, "GUILD_ID", 123)
    cog = reporting()
    request = interaction(guild_id=guild_id)
    if guild_id is None:
        request.guild = None
    asyncio.run(cog.results_command.callback(cog, request))
    cog.features.history.assert_not_called()
    request.response.defer.assert_not_awaited()


def test_command_bad_dates_do_not_query(monkeypatch):
    monkeypatch.setattr(reporting_module, "GUILD_ID", 123)
    cog = reporting()
    request = interaction()
    asyncio.run(cog.results_command.callback(cog, request, start="bad"))
    cog.features.history.assert_not_called()
    assert "YYYY-MM-DD" in request.response.send_message.call_args.args[0]


@pytest.mark.parametrize("failure", ["roster", "database"])
def test_command_unavailable_data_is_error_not_empty_success(monkeypatch, failure):
    monkeypatch.setattr(reporting_module, "GUILD_ID", 123)
    cog = reporting()
    if failure == "roster":
        cog.tracked_operators.return_value = None
    else:
        cog.features.history.side_effect = RuntimeError("offline")
    request = interaction()
    asyncio.run(cog.results_command.callback(cog, request))
    assert "Could not load" in request.followup.send.call_args.args[0]
    assert "embed" not in request.followup.send.call_args.kwargs


def test_command_filters_choices_and_member(monkeypatch):
    monkeypatch.setattr(reporting_module, "GUILD_ID", 123)
    cog = reporting()
    cog.features.history.return_value = ([play(1), play(2, status="loss"), play(3, sport_id=2)], USERS, SPORTS)
    request = interaction()
    asyncio.run(cog.results_command.callback(
        cog, request, sport=discord.app_commands.Choice(name="Basketball", value="basketball"),
        capper=SimpleNamespace(id=42, display_name="Ace"), result=discord.app_commands.Choice(name="Loss", value="loss"),
    ))
    embed = request.followup.send.call_args.kwargs["embed"]
    assert len(embed.fields) == 1 and embed.fields[0].name.startswith("#2")
    assert "Basketball" in embed.description and "Ace" in embed.description


def test_requester_guard_paging_and_bounds():
    async def check():
        view = ResultsView(42, [play(i) for i in range(1, 22)], USERS, SPORTS, "All", [])
        assert view.previous.disabled and not view.next_page.disabled
        assert not await view.interaction_check(interaction(43))
        assert await view.interaction_check(interaction())
        request = interaction()
        await view.change_page(request, 1)
        assert view.page == 1 and len(request.edit_original_response.call_args.kwargs["embed"].fields) == 10
        await view.change_page(request, 10)
        assert view.page == 2 and view.next_page.disabled
        await view.change_page(request, -10)
        assert view.page == 0 and view.previous.disabled
    asyncio.run(check())


def test_links_only_lookup_channels_member_can_read_and_verify_post():
    async def check():
        denied = SimpleNamespace(
            permissions_for=Mock(return_value=SimpleNamespace(view_channel=False, read_message_history=True)),
            fetch_message=AsyncMock(),
        )
        allowed = SimpleNamespace(
            permissions_for=Mock(return_value=SimpleNamespace(view_channel=True, read_message_history=True)),
            fetch_message=AsyncMock(return_value=SimpleNamespace(jump_url="https://discord.com/channels/123/456/501")),
        )
        view = ResultsView(42, [play()], USERS, SPORTS, "All", [denied, allowed])
        embed = await view.embed(interaction())
        denied.fetch_message.assert_not_awaited()
        allowed.fetch_message.assert_awaited_once_with(501)
        assert "https://discord.com/channels/123/456/501" in embed.fields[0].value
    asyncio.run(check())


def test_missing_post_omits_link_without_losing_result():
    async def check():
        channel = SimpleNamespace(
            permissions_for=Mock(return_value=SimpleNamespace(view_channel=True, read_message_history=True)),
            fetch_message=AsyncMock(side_effect=discord.NotFound(SimpleNamespace(status=404, reason="Not found"), "missing")),
        )
        view = ResultsView(42, [play()], USERS, SPORTS, "All", [channel])
        embed = await view.embed(interaction())
        assert len(embed.fields) == 1 and "Original post" not in embed.fields[0].value
    asyncio.run(check())


def test_failed_page_rolls_back_and_reports_error():
    async def check():
        view = ResultsView(42, [play(i) for i in range(11)], USERS, SPORTS, "All", [])
        request = interaction()
        request.edit_original_response.side_effect = RuntimeError("offline")
        await view.change_page(request, 1)
        assert view.page == 0 and view.previous.disabled
        assert "Could not load" in request.followup.send.call_args.args[0]
    asyncio.run(check())
