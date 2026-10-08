import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import src.bot as bot_module
from src.services import capper_insight_service as service


def interaction(user_id=42, operator=True, guild_id=None):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id, roles=[SimpleNamespace(id=bot_module.TRACKER_ROLE_ID)] if operator else []),
        guild=SimpleNamespace(id=bot_module.GUILD_ID if guild_id is None else guild_id),
        response=SimpleNamespace(send_modal=AsyncMock(), send_message=AsyncMock(), defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


def context(**overrides):
    return {"play_id": 7, "discord_user_id": "42", "selection": "Team A", "justification": "",
            "prompt_message_id": None, **overrides}


def test_only_original_operator_can_open_modal(monkeypatch):
    monkeypatch.setattr(service, "insight_context", lambda play_id: [context(justification="My reasoning")])
    for user_id, operator, guild_id in [(43, True, None), (42, False, None), (42, True, 999)]:
        denied = interaction(user_id, operator, guild_id)
        asyncio.run(bot_module.handle_insight_click(denied, 7))
        denied.response.send_modal.assert_not_awaited()
        assert denied.response.send_message.call_args.kwargs["ephemeral"]
    owner = interaction()
    asyncio.run(bot_module.handle_insight_click(owner, 7))
    modal = owner.response.send_modal.call_args.args[0]
    assert modal.play_id == 7 and modal.owner_id == 42
    assert modal.justification.default == "My reasoning"


def test_closed_pick_cannot_open_modal(monkeypatch):
    monkeypatch.setattr(service, "insight_context", lambda play_id: [])
    owner = interaction()
    asyncio.run(bot_module.handle_insight_click(owner, 7))
    owner.response.send_modal.assert_not_awaited()
    assert "no longer open" in owner.response.send_message.call_args.args[0]


def test_modal_rechecks_author_and_role_and_saves_privately(monkeypatch):
    save = Mock()
    monkeypatch.setattr(service, "save_insight", save)

    async def submit():
        modal = bot_module.CapperInsightModal(7, 42, "")
        modal.justification._value = "Matchup reasoning"
        for denied in [interaction(43), interaction(operator=False)]:
            await modal.on_submit(denied)
            denied.response.defer.assert_not_awaited()
        owner = interaction()
        await modal.on_submit(owner)
        assert owner.response.defer.call_args.kwargs["ephemeral"]
        assert owner.followup.send.call_args.kwargs["ephemeral"]
        assert "Insight saved" in owner.followup.send.call_args.args[0]

    asyncio.run(submit())
    save.assert_called_once_with(7, 42, "Matchup reasoning")


def test_save_failure_is_not_success(monkeypatch):
    monkeypatch.setattr(service, "save_insight", Mock(side_effect=RuntimeError("Database unavailable")))

    async def submit():
        modal = bot_module.CapperInsightModal(7, 42, "")
        modal.justification._value = "Reason"
        owner = interaction()
        await modal.on_submit(owner)
        assert "not saved" in owner.followup.send.call_args.args[0]
        assert owner.followup.send.call_args.kwargs["ephemeral"]

    asyncio.run(submit())


def test_request_posts_in_confirmation_channel_and_deduplicates_after_restart(monkeypatch):
    stored = context()
    monkeypatch.setattr(service, "insight_context", lambda play_id: [stored])
    monkeypatch.setattr(service, "mark_prompt", lambda play_id, message_id: stored.update(prompt_message_id=str(message_id)))
    channel = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=999)))
    resolve = AsyncMock(return_value=channel)
    monkeypatch.setattr(bot_module, "resolve_channel", resolve)
    monkeypatch.setattr(bot_module, "testing_enabled", False)

    async def send():
        assert await bot_module.send_insight_request(7)
        assert not await bot_module.send_insight_request(7)

    asyncio.run(send())
    resolve.assert_awaited_once_with(bot_module.CONFIRMATION_CHANNEL_ID, "CONFIRMATION_CHANNEL_ID", required=True)
    channel.send.assert_awaited_once()
    args, kwargs = channel.send.call_args
    assert "<@42>" in args[0] and "Play #7" in args[0]
    assert "authorized website members" in args[0]
    assert [item.custom_id for item in kwargs["view"].children] == ["pm:insight:7"]
    assert kwargs["view"].is_finished()
    assert [user.id for user in kwargs["allowed_mentions"].users] == [42]
    assert not kwargs["allowed_mentions"].everyone and not kwargs["allowed_mentions"].roles


def test_existing_insight_needs_no_request(monkeypatch):
    monkeypatch.setattr(service, "insight_context", lambda play_id: [context(justification="Already supplied")])
    resolve = AsyncMock()
    monkeypatch.setattr(bot_module, "resolve_channel", resolve)
    assert not asyncio.run(bot_module.send_insight_request(7))
    resolve.assert_not_awaited()


def test_rpc_saves_exact_author_and_independent_insight(monkeypatch):
    client = Mock()
    client.rpc.return_value.execute.return_value = SimpleNamespace(data=True)
    monkeypatch.setattr(service.supabase_service, "_ensure_client", lambda: client)
    service.save_insight(7, 42, " Reason ")
    client.rpc.assert_called_once_with("save_capper_insight", {
        "p_play_id": 7, "p_discord_user_id": "42", "p_justification": "Reason",
    })


@pytest.mark.parametrize("text", ["", "   ", "x" * 2001])
def test_invalid_insight_rejected(text):
    with pytest.raises(ValueError, match="1 to 2000"):
        service.save_insight(7, 42, text)


def test_rpc_unconfirmed_responses_are_errors(monkeypatch):
    monkeypatch.setattr(service, "_rpc", lambda *args: None)
    with pytest.raises(RuntimeError, match="unexpected"):
        service.insight_context()
    with pytest.raises(RuntimeError, match="not confirmed"):
        service.save_insight(7, 42, "Reason")
    with pytest.raises(RuntimeError, match="not confirmed"):
        service.mark_prompt(7, 999)


def test_persistent_button_dispatches_after_restart(monkeypatch):
    handler = AsyncMock()
    monkeypatch.setattr(bot_module, "handle_insight_click", handler)
    click = interaction()
    click.type = bot_module.discord.InteractionType.component
    click.data = {"custom_id": "pm:insight:7"}
    asyncio.run(bot_module.on_play_feature_interaction(click))
    handler.assert_awaited_once_with(click, 7)


def test_record_confirmation_automatically_requests_insight(monkeypatch):
    monkeypatch.setattr(bot_module.official_play_service, "get_play_legs", lambda play_id: [])
    request = AsyncMock()
    monkeypatch.setattr(bot_module, "send_insight_request", request)
    owner = interaction()
    asyncio.run(bot_module.send_confirmation_message(owner, {"play_id": 7}))
    request.assert_awaited_once_with(7)
    assert owner.followup.send.call_args.kwargs["ephemeral"]


def test_request_failure_does_not_claim_the_record_failed(monkeypatch):
    monkeypatch.setattr(bot_module.official_play_service, "get_play_legs", lambda play_id: [])
    monkeypatch.setattr(bot_module, "send_insight_request", AsyncMock(side_effect=RuntimeError("Unavailable")))
    alert = AsyncMock()
    monkeypatch.setattr(bot_module, "send_staff_alert", alert)
    owner = interaction()
    asyncio.run(bot_module.send_confirmation_message(owner, {"play_id": 7}))
    assert "Bet recorded" in owner.followup.send.call_args.args[0]
    alert.assert_awaited_once()


def test_backfill_requires_manager_and_reports_partial_failure(monkeypatch):
    monkeypatch.setattr(bot_module, "can_manage_plays", lambda user, guild: user.id == 42)
    monkeypatch.setattr(service, "insight_context", lambda: [context(), context(play_id=8)])
    request = AsyncMock(side_effect=[True, RuntimeError("Unavailable")])
    monkeypatch.setattr(bot_module, "send_insight_request", request)
    denied = interaction(43)
    asyncio.run(bot_module.request_insights_command.callback(denied))
    request.assert_not_awaited()
    assert denied.response.send_message.call_args.kwargs["ephemeral"]
    owner = interaction()
    asyncio.run(bot_module.request_insights_command.callback(owner))
    assert "Sent 1 requests, then stopped" in owner.followup.send.call_args.args[0]
    assert owner.followup.send.call_args.kwargs["ephemeral"]
