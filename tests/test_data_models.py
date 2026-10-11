import asyncio
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from src import data_models as ui
from src.services import data_model_service as models
from src.services.api_budget_service import ApiBudgetDenied
from src.services.api_sports_multi_service import ApiSportsMultiService
from src.services.api_sports_service import ApiSportsService

NOW = datetime.now(timezone.utc)


def history():
    return [
        models.ModelGame(
            str(i + 1), datetime(2026, 9, i + 1, 20, tzinfo=timezone.utc),
            str(i % 6), f"Team {i % 6}", 70 + i % 13,
            str((i + 1) % 6), f"Team {(i + 1) % 6}", 65 + i % 11,
            "FT", datetime(2026, 10, 10, 12, tzinfo=timezone.utc),
        ) for i in range(30)
    ]


def matchup():
    return replace(history()[0], id="99", start=datetime(2026, 11, 1, 20, tzinfo=timezone.utc),
                   home_score=None, away_score=None, status="NS", synced=NOW)


def test_projection_matches_website_numeric_baseline_and_excludes_future():
    games, game = history(), matchup()
    result = models.project_matchup(games, game)
    assert result is not None
    assert result.home_score == pytest.approx(72.9)
    assert result.away_score == pytest.approx(72.05)
    assert result.home_probability == pytest.approx(0.5726246983635468)
    assert (result.home_sample, result.away_sample, result.league_sample) == (10, 10, 30)
    excluded = [
        replace(game, home_score=999, away_score=0),
        replace(games[0], id="future", start=game.start + timedelta(days=1), home_score=999),
        replace(games[0], id="cancelled", status="CANC", home_score=999),
        replace(games[0], id="missing", home_score=None),
    ]
    assert models.project_matchup(games + excluded, game) == result
    assert models.project_matchup(games[:29], game) is None
    assert models.project_matchup([replace(g, home_score=7, away_score=3) for g in games], game) is None
    assert models.project_matchup(games, replace(game, home_id="unknown", home="Unknown")) is None


@pytest.mark.parametrize("value", [True, None, -1, float("nan"), float("inf"), "", "bad", {}, "-3"])
def test_model_score_rejects_invalid(value):
    assert models.model_score(value) is None


@pytest.mark.parametrize("value", [7, 7.0, "7", {"total": "7"}])
def test_model_score_accepts_provider_totals(value):
    assert models.model_score(value) == 7


@pytest.mark.parametrize("competition", list(models.COMPETITIONS))
def test_refresh_all_six_leagues_uses_complete_budgeted_provider_history(monkeypatch, competition):
    monkeypatch.setattr(models, "API_SPORTS_BUDGET_ENABLED", True)
    reserved, calls, saved = [], [], []
    monkeypatch.setattr("src.services.api_sports_service.reserve_request", lambda url: reserved.append(url))
    monkeypatch.setattr("src.services.api_sports_multi_service.reserve_request", lambda url: reserved.append(url))
    _, sport, league = models.COMPETITIONS[competition]
    nfl = sport == "nfl"

    def get(url, params, **kwargs):
        calls.append((url, params.copy()))
        page = params.get("page", 1)
        game = {
            "id": 100 + len(calls), "date": "2026-10-11T20:00:00Z",
            "status": {"short": "NS", "long": "Not Started"},
        }
        row = {
            "league": {"id": league, "name": "League", "season": params["season"]},
            "teams": {"home": {"id": 1, "name": "Home"}, "away": {"id": 2, "name": "Away"}},
            "scores": {"home": {"total": 7} if sport == "basketball" or nfl else 7,
                       "away": {"total": 3} if sport == "basketball" or nfl else 3},
        }
        if nfl or sport == "ncaa":
            row["game"] = {**game, "date": {"timestamp": 1791748800}}
        else:
            row.update(game)
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {
            "errors": {}, "response": [row], "paging": {"current": page, "total": 2},
        })

    nfl_api = ApiSportsService(api_key="test", get=get)
    multi = ApiSportsMultiService(api_key="test", get=get)
    monkeypatch.setattr(nfl_api, "_upsert", lambda table, rows, conflict: saved.extend(rows))
    monkeypatch.setattr(multi, "_store_events", lambda rows: saved.extend(rows))
    service = models.DataModelService(nfl=nfl_api, multi=multi, clock=lambda: datetime(2026, 10, 11, tzinfo=timezone.utc))
    games, used, refreshed = service.refresh(competition)
    assert len(games) == len(saved) == used == len(reserved) == 4
    assert [params.get("page") for _, params in calls] == [None, 2, None, 2]
    assert all(params["league"] == league for _, params in calls)
    if nfl or sport in {"hockey", "baseball", "ncaa"}:
        assert [str(calls[i][1]["season"]) for i in [0, 2]] == ["2025", "2026"]
    else:
        assert [calls[i][1]["season"] for i in [0, 2]] == ["2025-2026", "2026-2027"]
    assert refreshed == service.clock()


def test_refresh_no_unbudgeted_or_partial_success(monkeypatch):
    provider = Mock()
    service = models.DataModelService(nfl=provider)
    monkeypatch.setattr(models, "API_SPORTS_BUDGET_ENABLED", False)
    with pytest.raises(ApiBudgetDenied, match="BUDGET_ENABLED"):
        service.refresh("nfl")
    provider._request_page.assert_not_called()
    monkeypatch.setattr(models, "API_SPORTS_BUDGET_ENABLED", True)
    provider._request_page.side_effect = [([], 1), ApiBudgetDenied("Shared allowance exhausted")]
    with pytest.raises(ApiBudgetDenied, match="Shared allowance"):
        service.refresh("nfl")
    with pytest.raises(ValueError, match="supported"):
        service.refresh("not-a-league")


def test_refresh_rejects_other_league_and_storage_failure(monkeypatch):
    monkeypatch.setattr(models, "API_SPORTS_BUDGET_ENABLED", True)
    api = Mock()
    row = {
        "game": {"id": 1, "date": {"timestamp": int(NOW.timestamp())}},
        "league": {"id": 2, "season": 2025},
        "teams": {"home": {"id": 1, "name": "Home"}, "away": {"id": 2, "name": "Away"}},
    }
    api._request_page.return_value = ([row], 1)
    service = models.DataModelService(nfl=api, clock=lambda: datetime(2026, 10, 11, tzinfo=timezone.utc))
    with pytest.raises(RuntimeError, match="outside"):
        service.refresh("nfl")
    api._upsert.assert_not_called()
    row["league"]["id"] = 1
    api._upsert.side_effect = RuntimeError("database unavailable")
    with pytest.raises(RuntimeError, match="database unavailable"):
        service.refresh("nfl")


def test_choices_use_eastern_date_and_team_filter():
    game = replace(matchup(), start=datetime(2026, 11, 2, 1, tzinfo=timezone.utc))
    games = [game, replace(game, id="cancelled", status="CANC")]
    assert models.DataModelService.choices(games, date(2026, 11, 1), "TEAM 0") == [game]
    assert models.DataModelService.choices(games, date(2026, 11, 2), "") == []
    assert models.DataModelService.choices(games, date(2026, 11, 1), "Unknown") == []


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(ui, "GUILD_ID", 10)
    monkeypatch.setattr(ui, "CONFIRMATION_CHANNEL_ID", 20)
    monkeypatch.setattr(ui, "TRACKER_ROLE_ID", 30)


def interaction(role=30, user_id=123, channel_id=20, guild_id=10):
    member = Mock(spec=discord.Member)
    member.id, member.roles = user_id, [SimpleNamespace(id=role)]
    return SimpleNamespace(
        user=member, channel_id=channel_id, guild_id=guild_id,
        response=SimpleNamespace(send_message=AsyncMock(), send_modal=AsyncMock(), defer=AsyncMock(),
                                 edit_message=AsyncMock(), is_done=lambda: False),
        followup=SimpleNamespace(send=AsyncMock()),
        channel=SimpleNamespace(send=AsyncMock()),
    )


@pytest.mark.parametrize("changes,allowed", [
    ({}, True), ({"role": ui.WEBSITE_OWNER_ROLE_ID}, True), ({"role": 999}, False),
    ({"channel_id": 99}, False), ({"guild_id": 99}, False),
])
def test_command_access_and_no_refresh_before_authorization(configured, changes, allowed):
    async def run():
        request = interaction(**changes)
        service = Mock()
        cog = ui.DataModels(service=service)
        await ui.DataModels.model.callback(cog, request)
        message = request.response.send_message.call_args
        assert ("view" in message.kwargs) is allowed
        service.refresh.assert_not_called()
        if allowed:
            view = message.kwargs["view"]
            assert len(view.children[0].options) == 6
            view.stop()
    asyncio.run(run())


def test_complete_modal_dropdown_flow_posts_only_in_laboratory(configured):
    async def run():
        service = Mock()
        game = matchup()
        service.refresh.return_value = (history() + [game], 2, NOW)
        service.choices.return_value = [game]
        cog = ui.DataModels(service)
        request = interaction()
        await ui.DataModels.model.callback(cog, request)
        league_view = request.response.send_message.call_args.kwargs["view"]
        select = league_view.children[0]
        select._values = ["nfl"]
        await select.callback(request)
        modal = request.response.send_modal.call_args.args[0]
        modal.day._value, modal.team._value = "2026-11-01", "Team 0"
        await modal.on_submit(request)
        picker = request.followup.send.call_args.kwargs["view"]
        service.refresh.assert_called_once_with("nfl")
        service.choices.assert_called_once_with(history() + [game], date(2026, 11, 1), "Team 0")
        game_select = next(child for child in picker.children if isinstance(child, ui.GameSelect))
        game_select._values = [game.id]
        await game_select.callback(request)
        publish = request.response.send_modal.call_args.args[0]
        publish.confirm._value = "POST"
        await publish.on_submit(request)
        request.channel.send.assert_awaited_once()
        embed = request.channel.send.call_args.kwargs["embed"]
        assert "72.9" in embed.fields[0].value
        assert "57.3%" in embed.fields[1].value
        assert request.channel.send.call_args.kwargs["allowed_mentions"].everyone is False
        await publish.on_submit(request)
        request.channel.send.assert_awaited_once()
        league_view.stop()
        picker.stop()
    asyncio.run(run())


def test_picker_pagination_ownership_and_role_rechecks(configured):
    async def run():
        games = [replace(matchup(), id=str(i)) for i in range(60)]
        picker = ui.GameView(123, "nfl", games, games, 2, NOW)
        assert len(next(c for c in picker.children if isinstance(c, ui.GameSelect)).options) == 25
        request = interaction()
        await picker.next_page.callback(request)
        assert picker.page == 1
        await picker.next_page.callback(request)
        assert len(next(c for c in picker.children if isinstance(c, ui.GameSelect)).options) == 10
        assert picker.next_page.disabled is True
        await picker.previous.callback(request)
        assert picker.page == 1
        assert await picker.interaction_check(interaction(user_id=456)) is False
        assert await picker.interaction_check(interaction(role=999)) is False
        assert await picker.interaction_check(interaction(channel_id=99)) is False
        picker.stop()
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["date", "budget", "provider", "empty", "busy", "role"])
def test_filter_failures_explicit_no_public_post(configured, failure):
    async def run():
        service = Mock()
        service.refresh.return_value = ([], 2, NOW)
        service.choices.return_value = []
        cog = ui.DataModels(service)
        request = interaction(role=999 if failure == "role" else 30)
        modal = ui.GameFilterModal(cog, 123, "nfl")
        modal.day._value = "bad" if failure == "date" else "2026-11-01"
        if failure == "budget":
            service.refresh.side_effect = ApiBudgetDenied("Quota exhausted")
        elif failure == "provider":
            service.refresh.side_effect = RuntimeError("offline")
        elif failure == "busy":
            await cog.refresh_lock.acquire()
        await modal.on_submit(request)
        request.channel.send.assert_not_awaited()
        if failure in {"date", "role", "busy"}:
            service.refresh.assert_not_called()
        else:
            service.refresh.assert_called_once()
        assert request.response.send_message.called or request.followup.send.called
        if failure == "busy":
            cog.refresh_lock.release()
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["confirmation", "expired", "role", "channel", "owner", "send"])
def test_publish_failures_no_unauthorized_or_stale_post(configured, failure):
    async def run():
        game = matchup()
        picker = ui.GameView(123, "nfl", history(), [game], 2,
                             NOW - timedelta(minutes=4) if failure == "expired" else NOW)
        modal = ui.PublishModelModal(picker, game)
        modal.confirm._value = "NO" if failure == "confirmation" else "POST"
        request = interaction(role=999 if failure == "role" else 30,
                              channel_id=99 if failure == "channel" else 20,
                              user_id=456 if failure == "owner" else 123)
        if failure == "send":
            request.channel.send.side_effect = discord.Forbidden(Mock(status=403, reason="Forbidden"), "Missing permission")
        await modal.on_submit(request)
        if failure != "send":
            request.channel.send.assert_not_awaited()
        else:
            assert "could not be confirmed" in request.followup.send.call_args.args[0]
        picker.stop()
    asyncio.run(run())


def test_insufficient_history_embed_is_honest():
    embed = ui.model_embed("nfl", matchup(), [], 2, NOW)
    assert embed.fields[0].name == "No projection available"
    assert "probability" not in [field.name for field in embed.fields]
    assert "No injury" in embed.fields[-1].value
