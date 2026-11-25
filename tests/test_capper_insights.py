import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import src.bot as bot_module
import src.website_sync as website_module
from src.services import capper_insight_service as service


def website_sync(**overrides):
    kwargs = {
        "tracked_operators": AsyncMock(return_value={42}),
        "can_manage_plays": lambda user, guild: user.id == 42,
        "staff_alert": AsyncMock(),
    }
    kwargs.update(overrides)
    return website_module.WebsiteSync(bot_module.bot, **kwargs)


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


def test_author_can_submit_justification_by_replying_to_the_prompt_dm(monkeypatch):
    stored = context(prompt_message_id="999")
    monkeypatch.setattr(service, "insight_context", lambda play_id: [stored])
    save = Mock()
    monkeypatch.setattr(service, "save_insight", save)
    reply = SimpleNamespace(
        guild=None, author=SimpleNamespace(id=42, bot=False), content="Matchup reasoning",
        channel=SimpleNamespace(send=AsyncMock()),
        reference=SimpleNamespace(message_id=999),
        referenced_message=SimpleNamespace(content="Your justification is required for Play #7."),
    )
    website = website_sync()
    assert asyncio.run(website.handle_insight_message(reply))
    save.assert_called_once_with(7, 42, "Matchup reasoning")
    assert "was saved" in reply.channel.send.call_args.args[0]


def test_dm_justification_requires_author_and_matching_prompt(monkeypatch):
    stored = context(prompt_message_id="999")
    monkeypatch.setattr(service, "insight_context", lambda play_id: [stored])
    save = Mock()
    monkeypatch.setattr(service, "save_insight", save)
    website = website_sync()

    wrong_author = SimpleNamespace(
        guild=None, author=SimpleNamespace(id=43, bot=False), content="Not mine",
        channel=SimpleNamespace(send=AsyncMock()), reference=SimpleNamespace(message_id=999),
        referenced_message=SimpleNamespace(content="Play #7"),
    )
    wrong_prompt = SimpleNamespace(
        guild=None, author=SimpleNamespace(id=42, bot=False), content="Not this prompt",
        channel=SimpleNamespace(send=AsyncMock()), reference=SimpleNamespace(message_id=998),
        referenced_message=SimpleNamespace(content="Play #7"),
    )
    asyncio.run(website.handle_insight_message(wrong_author))
    asyncio.run(website.handle_insight_message(wrong_prompt))
    save.assert_not_called()
    assert "original author" in wrong_author.channel.send.call_args.args[0]
    assert "original Playmaker request" in wrong_prompt.channel.send.call_args.args[0]


def test_dm_message_can_identify_play_and_reports_save_failure(monkeypatch):
    stored = context(prompt_message_id="999")
    monkeypatch.setattr(service, "insight_context", lambda play_id: [stored])
    monkeypatch.setattr(service, "save_insight", Mock(side_effect=RuntimeError("Database unavailable")))
    reply = SimpleNamespace(
        guild=None, author=SimpleNamespace(id=42, bot=False), content="#7: Reason",
        channel=SimpleNamespace(send=AsyncMock()), reference=None, referenced_message=None,
    )
    assert asyncio.run(website_sync().handle_insight_message(reply))
    assert "was not saved" in reply.channel.send.call_args.args[0]


def test_request_sends_private_dm_and_deduplicates_after_restart(monkeypatch):
    stored = context()
    monkeypatch.setattr(service, "insight_context", lambda play_id: [stored])
    monkeypatch.setattr(service, "mark_prompt", lambda play_id, message_id: stored.update(prompt_message_id=str(message_id)))
    recipient = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=999)))
    cog = website_sync()
    monkeypatch.setattr(cog.bot, "get_user", Mock(return_value=recipient))

    async def send():
        assert await cog.send_insight_request(7)
        assert not await website_sync().send_insight_request(7)

    asyncio.run(send())
    recipient.send.assert_awaited_once()
    prompt = recipient.send.call_args.args[0]
    assert "Play #7" in prompt
    assert "Reply directly to this DM" in prompt
    assert "authorized website members" in prompt


def test_existing_insight_needs_no_request(monkeypatch):
    monkeypatch.setattr(service, "insight_context", lambda play_id: [context(justification="Already supplied")])
    recipient = SimpleNamespace(send=AsyncMock())
    website = website_sync()
    monkeypatch.setattr(website.bot, "get_user", Mock(return_value=recipient))
    assert not asyncio.run(website.send_insight_request(7))
    recipient.send.assert_not_awaited()


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


def test_bot_routes_direct_messages_to_insight_handler(monkeypatch):
    cog = website_sync()
    handler = AsyncMock(return_value=True)
    monkeypatch.setattr(cog, "handle_insight_message", handler)
    monkeypatch.setattr(bot_module.bot, "get_cog", lambda name: cog if name == "WebsiteSync" else None)
    message = SimpleNamespace(author=SimpleNamespace(bot=False), guild=None)
    asyncio.run(bot_module.on_message(message))
    handler.assert_awaited_once_with(message)


def test_record_confirmation_automatically_requests_insight(monkeypatch, load_settlement):
    monkeypatch.setattr(bot_module.official_play_service, "get_play_legs", lambda play_id: [])
    request = AsyncMock()
    monkeypatch.setattr(bot_module, "send_insight_request", request)
    owner = interaction()
    load_settlement()
    asyncio.run(bot_module.send_confirmation_message(owner, {"play_id": 7}))
    request.assert_awaited_once_with(7)
    assert owner.followup.send.call_args.kwargs["ephemeral"]


def test_request_failure_does_not_claim_the_record_failed(monkeypatch, load_settlement):
    monkeypatch.setattr(bot_module.official_play_service, "get_play_legs", lambda play_id: [])
    monkeypatch.setattr(bot_module, "send_insight_request", AsyncMock(side_effect=RuntimeError("Unavailable")))
    alert = AsyncMock()
    monkeypatch.setattr(bot_module, "send_staff_alert", alert)
    owner = interaction()
    load_settlement()
    asyncio.run(bot_module.send_confirmation_message(owner, {"play_id": 7}))
    assert "Bet recorded" in owner.followup.send.call_args.args[0]
    alert.assert_awaited_once()


def test_backfill_requires_manager_and_reports_partial_failure(monkeypatch):
    monkeypatch.setattr(service, "insight_context", lambda: [context(), context(play_id=8)])
    request = AsyncMock(side_effect=[True, RuntimeError("Unavailable")])
    cog = website_sync()
    monkeypatch.setattr(cog, "send_insight_request", request)
    denied = interaction(43)
    asyncio.run(cog.request_insights_command.callback(cog, denied))
    request.assert_not_awaited()
    assert denied.response.send_message.call_args.kwargs["ephemeral"]
    owner = interaction()
    asyncio.run(cog.request_insights_command.callback(cog, owner))
    assert "Sent 1 requests, then stopped" in owner.followup.send.call_args.args[0]
    assert owner.followup.send.call_args.kwargs["ephemeral"]
