import asyncio
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest
from discord import app_commands

from src import data_models as ui
from src.services import data_model_service as models
from src.services.api_budget_service import ApiBudgetDenied
from src.services.api_sports_multi_service import ApiSportsMultiService
from src.services.api_sports_service import ApiSportsService
from src.services.model_engine import Context, Market, ModelProjection
from src.services.model_projection_service import ModelProjectionService, ModelRun

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


def engine(service, backtest=None):
    def table(name):
        if backtest is None:
            raise RuntimeError("no backtests table")
        return FakeQuery({0: [backtest]}, [])
    inputs = Mock()
    inputs.context.side_effect = lambda competition, now: Context(now=now)
    return ModelProjectionService(data=service, inputs=inputs, client=SimpleNamespace(table=table), clock=lambda: NOW)


def picker_run(games):
    return ModelRun("nfl", games, {}, None, None, NOW)


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


def test_duplicate_provider_ids_count_once_and_preserve_scored_final():
    games = history()
    duplicates = [replace(game, id=f"replacement-{game.id}") for game in games]
    assert models.project_matchup(games + duplicates, matchup()) == models.project_matchup(games, matchup())
    game = games[0]
    schedule = replace(game, id="new", status="NS", synced=NOW)
    assert models.unique_model_games([game, schedule]) == [game]
    assert models.unique_model_games([schedule, game]) == [game]
    later = replace(game, id="later", start=game.start + timedelta(days=1))
    assert len(models.unique_model_games([game, later])) == 2


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
            "id": 100 + len(calls), "date": f"2026-10-{10 + len(calls)}T20:00:00Z",
            "status": {"short": "NS", "long": "Not Started"},
        }
        row = {
            "league": {"id": league, "name": "League", "season": params["season"]},
            "teams": {"home": {"id": 1, "name": "Home"}, "away": {"id": 2, "name": "Away"}},
            "scores": {"home": {"total": 7} if sport == "basketball" or nfl else 7,
                       "away": {"total": 3} if sport == "basketball" or nfl else 3},
        }
        if nfl or sport == "ncaa":
            row["game"] = {**game, "date": {"timestamp": 1791748800 + len(calls) * 86400}}
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
        cog = ui.DataModels(service, engine(service))
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
        projection = picker.run.projections[game.id]
        assert f"{projection.home_score:.1f}"[:3] in embed.fields[0].value
        assert [field.name for field in embed.fields][:3] == ["Projected score", "Estimated win probability", "Market comparison"]
        assert "no sportsbook consensus" in embed.fields[2].value
        assert "walk-forward backtest not available" in embed.fields[-2].value
        assert request.channel.send.call_args.kwargs["allowed_mentions"].everyone is False
        await publish.on_submit(request)
        request.channel.send.assert_awaited_once()
        league_view.stop()
        picker.stop()
    asyncio.run(run())


def test_picker_pagination_ownership_and_role_rechecks(configured):
    async def run():
        games = [replace(matchup(), id=str(i)) for i in range(60)]
        picker = ui.GameView(123, "nfl", picker_run(games), games, 2, NOW)
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
        cog = ui.DataModels(service, engine(service))
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
        picker = ui.GameView(123, "nfl", picker_run(history()), [game], 2,
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
    embed = ui.model_embed("nfl", matchup(), None, None, 2, NOW)
    assert embed.fields[0].name == "No projection available"
    assert "Estimated win probability" not in [field.name for field in embed.fields]
    assert "not available yet" in embed.fields[-2].value


def sample_projection(**changes):
    values = dict(home_score=24.4, away_score=20.1, home_probability=0.64, home_sample=10, away_sample=9,
                  league_sample=300, updated=NOW, margin_sd=13.0, total_sd=13.5)
    return ModelProjection(**{**values, **changes})


def test_embed_shows_market_edge_inputs_and_track_record():
    from src.services.model_engine import Injury, Weather
    market = Market(home_probability=0.6, home_spread=-3.5, total=44.5, books=7, fetched_at=NOW)
    injury = Injury("1", "Q. Back", "QB", "Out", "Knee")
    projection = sample_projection(
        market=market, market_edge=0.04, cover_probability=0.53, over_probability=0.41,
        rest_days=(7, 6), travel_km=(0.0, 1500.0), margin_adjustments=(("rest", 0.4),),
        weather=Weather(31.0, 18.0, 2.5, "forecast"), injuries=(injury,), injury_points=(injury.points, 0.0),
    )
    report = {"status": "ok", "games": 158, "model": {"brier": 0.2246, "log_loss": 0.6417, "accuracy": 0.586},
              "baseline": {"brier": 0.2263, "log_loss": 0.6476}}
    game = matchup()
    embed = ui.model_embed("nfl", game, projection, report, 2, NOW)
    fields = {field.name: field.value for field in embed.fields}
    assert "Team 0 -3.5" in fields["Market comparison"] and "Total 44.5" in fields["Market comparison"]
    assert "Model edge Team 0 +4.0 pts" in fields["Market comparison"]
    assert "cover 53.0%" in fields["Market comparison"] and "Over 41.0%" in fields["Market comparison"]
    assert "travel 1,500 km" in fields["Inputs"] and "31\u00b0F" in fields["Inputs"] and "Q. Back QB Out" in fields["Inputs"]
    assert "158 holdout games" in fields["Track record"] and "0.225 vs old 0.226" in fields["Track record"]

class FakeQuery:
    def __init__(self, pages, calls):
        self.pages, self.calls = pages, calls

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, *args))
            return self
        return record

    def range(self, start, end):
        self.calls.append(("range", start, end))
        self.offset = start
        return self

    def execute(self):
        return SimpleNamespace(data=self.pages.get(self.offset, []))


def event_row(event_id, home_id="1", home="Home", away_id="2", away="Away"):
    return {"event_id": str(event_id), "start_at": "2026-10-13T23:00:00+00:00", "home_id": home_id,
            "home_name": home, "home_score": None, "away_id": away_id, "away_name": away,
            "away_score": None, "status_code": "NS", "synced_at": "2026-10-13T12:00:00+00:00"}


def test_cached_reads_stored_history_pages_without_api_requests():
    calls = []
    full_page = [event_row(i, home_id=str(i + 1), away_id=str(i + 5000)) for i in range(1000)]
    pages = {0: full_page, 1000: [event_row(2000), event_row(2001, home=None), event_row(2002, away_id="0")]}
    client = SimpleNamespace(table=lambda name: (calls.append(("table", name)), FakeQuery(pages, calls))[1])
    nfl, multi = Mock(), Mock()
    service = models.DataModelService(nfl=nfl, multi=multi, clock=lambda: datetime(2026, 10, 13, 20, tzinfo=timezone.utc))
    games = service.cached("hockey:57", client=client)
    assert len(games) == 1001
    assert ("table", "api_sports_events") in calls
    assert ("in_", "season", ["2025", "2026"]) in calls
    assert ("eq", "sport_slug", "hockey") in calls and ("eq", "league_id", "57") in calls
    assert [call for call in calls if call[0] == "range"] == [("range", 0, 999), ("range", 1000, 1999)]
    nfl.assert_not_called()
    multi.assert_not_called()
    assert not multi.method_calls and not nfl.method_calls


def test_model_card_renders_projection_and_insufficient_history():
    from PIL import Image
    from src.services.model_card_image_service import render_model_card

    market = Market(home_probability=0.6, home_spread=-1.5, total=6.5, books=5, fetched_at=NOW)
    full = sample_projection(market=market, market_edge=-0.02, cover_probability=0.4, over_probability=0.55,
                             rest_days=(1, 2), travel_km=(0.0, 3200.0))
    for value in (sample_projection(), full, None):
        image = Image.open(render_model_card("NHL", matchup(), value, {"status": "ok", "games": 9, "model": {}, "baseline": {}}))
        assert image.format == "PNG" and image.size == (1200, 800)


def test_league_model_posts_one_card_per_game_as_regular_messages(configured):
    async def run():
        games = [replace(matchup(), id=str(i), start=datetime(2026, 11, 1, 20, i, tzinfo=timezone.utc)) for i in range(12)]
        service = Mock()
        service.cached.return_value = history() + games
        service.choices.return_value = games
        request = interaction()
        league = app_commands.Choice(name="NHL", value="hockey:57")
        await ui.DataModels.league_model.callback(ui.DataModels(service, engine(service)), request, league, "2026-11-01")
        service.cached.assert_called_once_with("hockey:57")
        service.choices.assert_called_once_with(history() + games, date(2026, 11, 1), "")
        service.refresh.assert_not_called()
        sends = request.channel.send.call_args_list
        assert [len(call.kwargs["files"]) for call in sends] == [10, 2]
        assert "NHL ratings models \u00b7" in sends[0].kwargs["content"] and sends[1].kwargs["content"] is None
        assert all(call.kwargs.get("ephemeral") is None for call in sends)
        assert sends[0].kwargs["allowed_mentions"].everyone is False
        assert "Posted 12 NHL model cards" in request.followup.send.call_args.args[0]
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["role", "channel", "date", "empty", "storage"])
def test_league_model_failures_post_nothing_publicly(configured, failure):
    async def run():
        service = Mock()
        service.cached.return_value = history()
        service.choices.return_value = []
        if failure == "storage":
            service.cached.side_effect = RuntimeError("offline")
        request = interaction(role=999 if failure == "role" else 30, channel_id=99 if failure == "channel" else 20)
        league = app_commands.Choice(name="NHL", value="hockey:57")
        await ui.DataModels.league_model.callback(
            ui.DataModels(service, engine(service)), request, league, "10/13/2026" if failure == "date" else "2026-11-01")
        request.channel.send.assert_not_awaited()
        if failure in {"role", "channel", "date"}:
            service.cached.assert_not_called()
            assert request.response.send_message.call_args.kwargs["ephemeral"] is True
        else:
            assert request.followup.send.call_args.kwargs["ephemeral"] is True
    asyncio.run(run())


def test_league_model_date_suggestions_cover_two_weeks_and_filter():
    async def run():
        cog = ui.DataModels(Mock())
        options = await ui.DataModels.league_model_day(cog, interaction(), "")
        assert len(options) == 15
        assert options[1].name.startswith("Today \u00b7 ")
        assert date.fromisoformat(options[1].value) == datetime.now(ui.TRACKER_TIMEZONE).date()
        tomorrow = await ui.DataModels.league_model_day(cog, interaction(), "tomorrow")
        assert len(tomorrow) == 1
    asyncio.run(run())
