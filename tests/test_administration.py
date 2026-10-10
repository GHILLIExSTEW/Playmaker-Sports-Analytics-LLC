import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import src.administration as module
import src.bot as bot_module
from src.administration import Administration, TestFlowView as ImageTestFlowView


def administration():
    return Administration(
        set_testing=Mock(), get_tracker_start=Mock(return_value=None), set_tracker_start=Mock(),
        api_service=Mock(), image_service=Mock(), diagnostics=Mock(),
    )


def interaction(*, roles=(), manager=False, guild=True):
    return SimpleNamespace(
        id=123, guild=SimpleNamespace(id=999) if guild else None,
        user=SimpleNamespace(
            id=42, roles=[SimpleNamespace(id=role) for role in roles], bot=False,
            guild_permissions=SimpleNamespace(manage_guild=manager),
        ),
        response=SimpleNamespace(
            defer=AsyncMock(), send_message=AsyncMock(), edit_message=AsyncMock(),
            send_modal=AsyncMock(), is_done=Mock(return_value=False),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
    )


def parsed():
    return {"units": 2, "legs": [{"selection": "Home ML", "odds": 150}]}


def test_diagnostics_rejects_nonofficial_without_running_checks(monkeypatch):
    monkeypatch.setattr(module, "OFFICIAL_ROLE_IDS", {77})
    cog = administration()
    request = interaction()
    asyncio.run(cog.test_command.callback(cog, request))
    cog.diagnostics.run_checks.assert_not_called()
    assert request.response.send_message.call_args.kwargs["ephemeral"] is True


def test_diagnostics_preserves_safe_check_results(monkeypatch):
    monkeypatch.setattr(module, "OFFICIAL_ROLE_IDS", {77})
    cog = administration()
    cog.diagnostics.run_checks.return_value = [
        {"passed": True, "name": "Odds", "detail": "Correct"},
        {"passed": False, "name": "Schema", "detail": "Missing"},
    ]
    request = interaction(roles=[77])
    asyncio.run(cog.test_command.callback(cog, request))
    embed = request.response.send_message.call_args.kwargs["embed"]
    assert embed.description == "1/2 checks passed. No database records were changed."
    assert [field.name for field in embed.fields] == ["PASS • Odds", "FAIL • Schema"]
    assert request.response.send_message.call_args.kwargs["ephemeral"] is True


def test_diagnostics_exception_is_explicit(monkeypatch, caplog):
    monkeypatch.setattr(module, "OFFICIAL_ROLE_IDS", set())
    cog = administration()
    cog.diagnostics.run_checks.side_effect = RuntimeError("failed")
    request = interaction()
    asyncio.run(cog.test_command.callback(cog, request))
    assert "Diagnostics failed" in request.response.send_message.call_args.args[0]
    assert "diagnostics_failed" in caplog.text


@pytest.mark.parametrize("content_type", [None, "application/pdf", "text/plain"])
def test_image_test_rejects_nonimage_without_extracting(monkeypatch, content_type):
    monkeypatch.setattr(module, "OFFICIAL_ROLE_IDS", set())
    cog = administration()
    request = interaction()
    asyncio.run(cog.test_command.callback(cog, request, SimpleNamespace(content_type=content_type)))
    cog.image_service.extract_play.assert_not_called()
    assert "Attach an image" in request.response.send_message.call_args.args[0]


def test_image_test_parses_privately_without_recording(monkeypatch):
    monkeypatch.setattr(module, "OFFICIAL_ROLE_IDS", set())
    cog = administration()
    cog.image_service.extract_play.return_value = parsed()
    request = interaction()
    asyncio.run(cog.test_command.callback(
        cog, request, SimpleNamespace(content_type="image/png", url="https://cdn/test.png"),
    ))
    cog.image_service.extract_play.assert_called_once_with("https://cdn/test.png")
    kwargs = request.followup.send.call_args.kwargs
    assert kwargs["ephemeral"] is True
    assert kwargs["embed"].title == "Image Test Result"
    assert "Nothing was recorded" in kwargs["embed"].description
    assert not kwargs["view"].children
    cog.set_testing.assert_not_called()
    cog.set_tracker_start.assert_not_called()


def test_image_test_failure_is_private(monkeypatch):
    monkeypatch.setattr(module, "OFFICIAL_ROLE_IDS", set())
    cog = administration()
    cog.image_service.extract_play.side_effect = RuntimeError("unreadable")
    request = interaction()
    asyncio.run(cog.test_command.callback(
        cog, request, SimpleNamespace(content_type="image/png", url="https://cdn/test.png"),
    ))
    assert "Image test failed" in request.followup.send.call_args.args[0]
    assert request.followup.send.call_args.kwargs["ephemeral"] is True


@pytest.mark.parametrize("manager,roles,allowed", [(False, [], False), (True, [], True), (False, [88], True)])
def test_testing_setting_preserves_operator_or_manager_permission(monkeypatch, manager, roles, allowed):
    monkeypatch.setattr(module, "OPERATOR_ROLE_IDS", {88})
    cog = administration()
    request = interaction(manager=manager, roles=roles)
    asyncio.run(cog.testing_command.callback(cog, request, True))
    if allowed:
        cog.set_testing.assert_called_once_with(True)
    else:
        cog.set_testing.assert_not_called()
    assert request.response.send_message.call_args.kwargs["ephemeral"] is True


def test_tracker_show_does_not_require_mutation_permission():
    cog = administration()
    cog.get_tracker_start.return_value = date(2026, 10, 1)
    request = interaction()
    asyncio.run(cog.tracker_start_command.callback(cog, request, " SHOW "))
    assert "2026-10-01" in request.response.send_message.call_args.args[0]
    cog.set_tracker_start.assert_not_called()


@pytest.mark.parametrize("value", ["clear", "none", "off"])
def test_tracker_clear_aliases_preserved(value):
    cog = administration()
    request = interaction(manager=True)
    asyncio.run(cog.tracker_start_command.callback(cog, request, value))
    cog.set_tracker_start.assert_called_once_with(None)


def test_tracker_date_is_validated_before_mutation():
    cog = administration()
    request = interaction(manager=True)
    asyncio.run(cog.tracker_start_command.callback(cog, request, "2026-02-30"))
    cog.set_tracker_start.assert_not_called()
    assert "YYYY-MM-DD" in request.response.send_message.call_args.args[0]
    asyncio.run(cog.tracker_start_command.callback(cog, interaction(manager=True), "2026-10-01"))
    cog.set_tracker_start.assert_called_once_with(date(2026, 10, 1))


def test_tracker_mutation_denied_without_permission(monkeypatch):
    monkeypatch.setattr(module, "OPERATOR_ROLE_IDS", {88})
    cog = administration()
    asyncio.run(cog.tracker_start_command.callback(cog, interaction(), "clear"))
    cog.set_tracker_start.assert_not_called()


def test_admin_state_callbacks_update_existing_bot_routing_and_cutoff(monkeypatch):
    monkeypatch.setattr(bot_module, "testing_enabled", False)
    monkeypatch.setattr(bot_module, "tracker_start_date", None)
    monkeypatch.setattr(bot_module, "TEST_CHANNEL_ID", 777)
    cog = Administration(
        set_testing=bot_module.set_testing_enabled,
        get_tracker_start=bot_module.get_tracker_start_date,
        set_tracker_start=bot_module.set_tracker_start_date,
    )
    asyncio.run(cog.testing_command.callback(cog, interaction(manager=True), True))
    assert bot_module.testing_enabled
    assert bot_module.play_post_target() == ("TEST_CHANNEL_ID", 777)
    assert bot_module.official_post_target() == ("TEST_CHANNEL_ID", 777)
    asyncio.run(cog.tracker_start_command.callback(cog, interaction(manager=True), "2026-10-01"))
    assert bot_module.tracker_start_date == date(2026, 10, 1)
    assert cog.get_tracker_start() == bot_module.tracker_start_date


def test_test_channel_flow_only_completes_when_units_available():
    async def check():
        values = {"units": None, "legs": parsed()["legs"]}
        view = ImageTestFlowView(values)
        request = interaction()
        await view.complete_test.callback(request)
        request.response.edit_message.assert_not_awaited()
        values["units"] = 2
        await view.complete_test.callback(request)
        assert request.response.edit_message.call_args.kwargs["content"] == "Test complete. Nothing was recorded."
    asyncio.run(check())
