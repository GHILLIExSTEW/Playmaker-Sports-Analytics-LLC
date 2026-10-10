import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

import src.bot as bot_module
from src.play_settlement import EditBetModal, EditBetView, OwnerOnlyView, PlayPickerView, RegradeModal, SettleResultView, UnsettleConfirmView
from src.services.official_play_service import OfficialPlayService


def request(user_id=42, custom_id="pm:as:7:win"):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id, display_name="Ace"), guild=None,
        type=discord.InteractionType.component, data={"custom_id": custom_id},
        message=SimpleNamespace(embeds=[discord.Embed(title="Suggestion")]),
        response=SimpleNamespace(
            send_message=AsyncMock(), edit_message=AsyncMock(), send_modal=AsyncMock(),
            defer=AsyncMock(), is_done=Mock(return_value=True),
        ),
        edit_original_response=AsyncMock(), followup=SimpleNamespace(send=AsyncMock()),
    )


def make_cog(load_settlement, **overrides):
    kwargs = {
        "official": Mock(open_play_label=OfficialPlayService.open_play_label),
        "features": Mock(),
        "is_official": Mock(return_value=True), "can_manage_plays": Mock(return_value=True),
        "fetch_message": AsyncMock(return_value=SimpleNamespace(id=500)),
        "update_message": AsyncMock(), "refresh_card": AsyncMock(),
        "resolve_channel": AsyncMock(return_value=SimpleNamespace(send=AsyncMock())),
        "staff_alert": AsyncMock(), "bang_notifications": AsyncMock(),
    }
    kwargs.update(overrides)
    return load_settlement(**kwargs)


@pytest.mark.parametrize("name", ["settle", "regrade", "unsettle", "edit_play"])
def test_commands_preserve_private_permission_gate(load_settlement, name):
    cog = make_cog(load_settlement)
    cog.show_play_picker = AsyncMock()
    callback = getattr(cog, f"{name}_command").callback
    click = request()
    cog.is_official.return_value = False
    asyncio.run(callback(cog, click))
    click.response.defer.assert_not_awaited()
    cog.show_play_picker.assert_not_awaited()
    assert click.response.send_message.call_args.kwargs["ephemeral"]
    cog.is_official.return_value = True
    click = request()
    asyncio.run(callback(cog, click))
    click.response.defer.assert_awaited_once_with(ephemeral=True)
    mode = "edit" if name == "edit_play" else name
    cog.show_play_picker.assert_awaited_once_with(click, mode, 0)


def test_jobs_start_once_and_cancel_on_unload(monkeypatch, load_settlement):
    cog = make_cog(load_settlement)
    running = False

    def start():
        nonlocal running
        running = True

    start_mock, cancel = Mock(side_effect=start), Mock()
    monkeypatch.setattr(cog.auto_settle_suggestions, "is_running", lambda: running)
    monkeypatch.setattr(cog.auto_settle_suggestions, "start", start_mock)
    monkeypatch.setattr(cog.auto_settle_suggestions, "cancel", cancel)
    asyncio.run(cog.on_ready())
    asyncio.run(cog.on_ready())
    start_mock.assert_called_once()
    cog.cog_unload()
    cancel.assert_called_once()


def test_hot_loaded_cog_starts_only_after_ready(monkeypatch, load_settlement):
    cog = make_cog(load_settlement)
    monkeypatch.setattr(cog.bot, "is_ready", Mock(return_value=False))
    cog.start_jobs = Mock()
    asyncio.run(cog.cog_load())
    cog.start_jobs.assert_not_called()
    cog.bot.is_ready.return_value = True
    asyncio.run(cog.cog_load())
    cog.start_jobs.assert_called_once()


def test_picker_falls_back_when_an_older_page_empties(load_settlement):
    cog = make_cog(load_settlement)
    cog.load_play_page = Mock(side_effect=[([], False), ([{"id": 7}], False)])
    click = request()
    asyncio.run(cog.show_play_picker(click, "settle", 2, edit=True))
    assert cog.load_play_page.call_args_list[1].args == ("settle", 0)
    view = click.edit_original_response.call_args.kwargs["view"]
    assert isinstance(view, PlayPickerView) and view.page == 0


def test_picker_database_failure_is_not_an_empty_result(load_settlement):
    cog = make_cog(load_settlement)
    cog.load_play_page = Mock(side_effect=RuntimeError("offline"))
    click = request()
    asyncio.run(cog.show_play_picker(click, "settle", 0))
    assert "Could not load plays" in click.followup.send.call_args.args[0]
    assert click.followup.send.call_args.kwargs["ephemeral"]


def test_menu_rechecks_requester_and_current_role(load_settlement):
    cog = make_cog(load_settlement)

    async def check():
        view = OwnerOnlyView(cog, 42)
        assert not await view.interaction_check(request(43))
        cog.is_official.return_value = False
        assert not await view.interaction_check(request())
        cog.is_official.return_value = True
        assert await view.interaction_check(request())
    asyncio.run(check())


@pytest.mark.parametrize("status", ["open", "regraded", "win"])
def test_picker_settlement_checks_current_status_and_updates_card(load_settlement, status):
    cog = make_cog(load_settlement)
    cog.official._fetch_play.return_value = {"id": 7, "status": status, "message_id": "500"}
    cog.official.settle_play.return_value = {"result": "partial"}
    click = request()

    async def settle():
        await SettleResultView(cog, 42, {"id": 7}, 0).settle(click, "partial")
    asyncio.run(settle())
    if status == "win":
        cog.official.settle_play.assert_not_called()
        cog.update_message.assert_not_awaited()
        assert "already settled" in click.edit_original_response.call_args.kwargs["content"]
    else:
        cog.official.settle_play.assert_called_once_with(7, "partial")
        cog.fetch_message.assert_awaited_once_with(None, 500)
        cog.update_message.assert_awaited_once_with(cog.fetch_message.return_value, 7, "partial")


def test_reopen_refreshes_card_and_reports_previous_result(load_settlement):
    cog = make_cog(load_settlement)
    cog.official.unsettle_play.return_value = {"previous": "loss"}
    click = request()

    async def reopen():
        await UnsettleConfirmView(cog, 42, {"id": 7}, 0).confirm.callback(click)
    asyncio.run(reopen())
    cog.official.unsettle_play.assert_called_once_with(7)
    cog.refresh_card.assert_awaited_once_with(None, 7)
    assert "LOSS" in click.edit_original_response.call_args.kwargs["content"]


def test_reopen_failure_never_refreshes_or_claims_success(load_settlement):
    cog = make_cog(load_settlement)
    cog.official.unsettle_play.side_effect = ValueError("Play 7 is not settled.")
    click = request()

    async def reopen():
        await UnsettleConfirmView(cog, 42, {"id": 7}, 0).confirm.callback(click)
    asyncio.run(reopen())
    cog.refresh_card.assert_not_awaited()
    assert click.edit_original_response.call_args.kwargs["content"] == "Play 7 is not settled."


def test_regrade_validates_inputs_rechecks_role_and_refreshes_card(load_settlement):
    cog = make_cog(load_settlement)
    cog.official.regrade_play.return_value = {"legs_left": 2, "odds": -110}

    async def regrade():
        modal = RegradeModal(cog, 42, {"id": 7})
        modal.legs_left._value, modal.odds._value, modal.note._value = "2", "-110", "Reason"
        await modal.on_submit(request(43))
        cog.is_official.return_value = False
        await modal.on_submit(request())
        cog.official.regrade_play.assert_not_called()
        cog.is_official.return_value = True
        await modal.on_submit(request())
    asyncio.run(regrade())
    cog.official.regrade_play.assert_called_once_with(7, 2, -110, "Reason")
    cog.refresh_card.assert_awaited_once_with(None, 7)


def test_regrade_database_failure_is_explicit(load_settlement):
    cog = make_cog(load_settlement)
    cog.official.regrade_play.side_effect = RuntimeError("offline")
    click = request()

    async def submit():
        modal = RegradeModal(cog, 42, {"id": 7})
        modal.legs_left._value, modal.odds._value = "2", "-110"
        await modal.on_submit(click)
    asyncio.run(submit())
    assert "Could not regrade" in click.response.send_message.call_args.args[0]
    cog.refresh_card.assert_not_awaited()


def test_record_edit_preserves_uploader_access_and_normalizes_odds(load_settlement):
    cog = make_cog(load_settlement, is_official=Mock(return_value=False))
    click = request()

    async def submit():
        modal = EditBetModal(cog, 7, 42, {})
        modal.units._value, modal.team._value = "2", "Team"
        modal.selections._value, modal.odds._value, modal.notes._value = "A\nB", "+150\n-110", "Reason"
        await modal.on_submit(request(43))
        cog.official.edit_play_record.assert_not_called()
        await modal.on_submit(click)
    asyncio.run(submit())
    cog.official.edit_play_record.assert_called_once_with(7, 2.0, "Team", ["A", "B"], [150, -110], "Reason")
    cog.refresh_card.assert_awaited_once_with(None, 7)
    assert click.response.edit_message.call_args.kwargs["view"] is None


def test_command_edit_rechecks_official_role_before_saving(load_settlement):
    cog = make_cog(load_settlement, is_official=Mock(return_value=False))

    async def submit():
        await EditBetModal(cog, 7, 42, {}, require_official=True).on_submit(request())
    asyncio.run(submit())
    cog.official.edit_play_record.assert_not_called()


def test_record_edit_database_error_is_explicit(load_settlement):
    cog = make_cog(load_settlement)
    cog.official.edit_play_record.side_effect = RuntimeError("offline")
    click = request()

    async def submit():
        modal = EditBetModal(cog, 7, 42, {})
        modal.units._value, modal.selections._value, modal.odds._value = "1", "A", "-110"
        await modal.on_submit(click)
    asyncio.run(submit())
    assert "Could not update" in click.response.send_message.call_args.args[0]
    cog.refresh_card.assert_not_awaited()


@pytest.mark.parametrize("custom_id,result", [("pm:as:7:win", "win"), ("pm:asx:7", None)])
def test_existing_suggestion_buttons_dispatch_once(load_settlement, load_presentation, custom_id, result):
    cog = make_cog(load_settlement)
    cog.handle_suggestion_click = AsyncMock()
    click = request(custom_id=custom_id)
    asyncio.run(cog.on_suggestion_interaction(click))
    cog.handle_suggestion_click.assert_awaited_once_with(click, 7, result)
    asyncio.run(load_presentation().on_play_feature_interaction(click))
    assert cog.handle_suggestion_click.await_count == 1


@pytest.mark.parametrize("custom_id", ["pm:as:7:bad", "pm:as:7", "pm:asx:0", "pm:asx:x"])
def test_bad_suggestion_ids_report_errors_without_mutation(load_settlement, custom_id):
    cog = make_cog(load_settlement)
    click = request(custom_id=custom_id)
    asyncio.run(cog.on_suggestion_interaction(click))
    cog.official.settle_play.assert_not_called()
    assert click.followup.send.call_args.kwargs["ephemeral"]


def test_suggestion_authority_and_current_status_are_checked(load_settlement):
    cog = make_cog(load_settlement)
    cog.can_manage_plays.return_value = False
    asyncio.run(cog.handle_suggestion_click(request(), 7, "win"))
    cog.official._fetch_play.assert_not_called()
    cog.can_manage_plays.return_value = True
    cog.official._fetch_play.return_value = {"status": "loss"}
    asyncio.run(cog.handle_suggestion_click(request(), 7, "win"))
    cog.official.settle_play.assert_not_called()


def test_suggestion_confirmation_updates_source_card(load_settlement):
    cog = make_cog(load_settlement)
    cog.official._fetch_play.return_value = {"status": "regraded", "message_id": "500"}
    click = request()
    asyncio.run(cog.handle_suggestion_click(click, 7, "win"))
    cog.official.settle_play.assert_called_once_with(7, "win")
    cog.update_message.assert_awaited_once_with(cog.fetch_message.return_value, 7, "win")
    assert "Settled as WIN" in click.edit_original_response.call_args.kwargs["embed"].footer.text


def test_suggestion_dismissal_does_not_settle(load_settlement):
    cog = make_cog(load_settlement)
    click = request(custom_id="pm:asx:7")
    asyncio.run(cog.handle_suggestion_click(click, 7, None))
    cog.official._fetch_play.assert_not_called()
    cog.official.settle_play.assert_not_called()
    assert click.edit_original_response.call_args.kwargs["view"] is None


def test_suggestion_job_marks_only_after_send(load_settlement):
    events = []
    channel = SimpleNamespace(send=AsyncMock(side_effect=lambda **kwargs: events.append("send")))
    cog = make_cog(load_settlement, resolve_channel=AsyncMock(return_value=channel))
    cog.features.suggestion_candidates.return_value = [{"id": 7}, {"id": 8}]
    cog.features.suggest.side_effect = [None, {"result": "win", "notes": ["Final score"]}]
    cog.features.mark_suggested.side_effect = lambda play_id: events.append(("mark", play_id))
    asyncio.run(cog.auto_settle_suggestions.coro(cog))
    assert events == ["send", ("mark", 8)]
    assert [item.custom_id for item in channel.send.call_args.kwargs["view"].children] == ["pm:as:8:win", "pm:asx:8"]


def test_failed_suggestion_send_does_not_mark_play(load_settlement):
    channel = SimpleNamespace(send=AsyncMock(side_effect=RuntimeError("offline")))
    cog = make_cog(load_settlement, resolve_channel=AsyncMock(return_value=channel))
    cog.features.suggestion_candidates.return_value = [{"id": 7}]
    cog.features.suggest.return_value = {"result": "win", "notes": []}
    asyncio.run(cog.auto_settle_suggestions.coro(cog))
    cog.features.mark_suggested.assert_not_called()
    cog.staff_alert.assert_awaited_once()


def test_reaction_lookup_failure_alerts_staff_without_claiming_settlement(load_settlement):
    cog = make_cog(load_settlement, channel_settings=lambda: [("test", 101)])
    cog.official.get_play_for_message.side_effect = RuntimeError("offline")
    payload = SimpleNamespace(
        user_id=42, member=SimpleNamespace(id=42, bot=False),
        emoji=bot_module.WIN_REACTION, channel_id=101, message_id=500,
    )
    asyncio.run(cog.handle_reaction(payload))
    cog.official.settle_play.assert_not_called()
    cog.staff_alert.assert_awaited_once()
    assert cog.staff_alert.call_args.args[0] == "reaction_settlement"


def test_reaction_outside_configured_channels_is_ignored(load_settlement):
    cog = make_cog(load_settlement, channel_settings=lambda: [("test", 101)])
    payload = SimpleNamespace(
        user_id=42, member=SimpleNamespace(id=42, bot=False),
        emoji=bot_module.WIN_REACTION, channel_id=999, message_id=500,
    )
    asyncio.run(cog.handle_reaction(payload))
    cog.official.get_play_for_message.assert_not_called()


def test_confirmation_uses_loaded_settlement_view(load_settlement, monkeypatch):
    cog = make_cog(load_settlement)
    cog.official.get_play_legs.return_value = []
    monkeypatch.setattr(bot_module.official_play_service, "get_play_legs", Mock(return_value=[]))
    monkeypatch.setattr(bot_module, "send_insight_request", AsyncMock())
    click = request()
    asyncio.run(bot_module.send_confirmation_message(click, {"play_id": 7}))
    view = click.followup.send.call_args.kwargs["view"]
    assert isinstance(view, EditBetView) and view.settlement is cog


def test_missing_settlement_cog_is_explicit(monkeypatch):
    monkeypatch.setattr(bot_module.bot, "get_cog", Mock(return_value=None))
    with pytest.raises(RuntimeError, match="not loaded"):
        bot_module.get_play_settlement()
