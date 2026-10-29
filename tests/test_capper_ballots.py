import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

pytestmark = pytest.mark.usefixtures("mock_play_cards")

import src.capper_ballots as module
from src.capper_ballots import CapperBallots, ballot_counts, ballot_rating
from src.play_presentation import build_play_card_embed


@pytest.mark.parametrize("size", [3, 4, 5])
def test_exact_vote_thresholds(size):
    for yes in range(size + 1):
        for no in range(size - yes + 1):
            units = yes if size == 3 else yes - size + 3
            expected = (units + 2, units) if yes > size // 2 else None
            assert ballot_rating(yes, no, size) == expected


@pytest.mark.parametrize("yes,no", [(6, 0), (-1, 0), (3, -1)])
def test_out_of_range_votes_require_review(yes, no):
    with pytest.raises(ValueError):
        ballot_rating(yes, no, 5)


@pytest.mark.parametrize("size", [0, 1, 2, 6, 10])
def test_unsupported_roster_requires_review(size):
    with pytest.raises(ValueError):
        ballot_rating(0, 0, size)


def poll(yes=4, no=1, passed=0, final=True):
    next_id = 42
    answers = []
    for name, count in [("Yes", yes), ("No", no), ("Pass", passed)]:
        ids = list(range(next_id, next_id + count))
        next_id += count

        async def voters(limit=None, ids=ids):
            for voter_id in ids:
                yield SimpleNamespace(id=voter_id, bot=False)

        answers.append(SimpleNamespace(text=name, vote_count=count, voters=voters))
    return SimpleNamespace(
        answers=answers,
        multiple=False, is_finalised=lambda: final, question="Home ML",
    )


def make_cog():
    return CapperBallots(
        Mock(user=SimpleNamespace(id=900, display_name="Playmaker Picks Team", display_avatar=SimpleNamespace(url="https://cdn/bot"))),
        official=Mock(create_play_record=Mock(return_value={
            "play_id": 7, "units": 2, "odds": 150, "to_win": 3,
            "summary": "2u", "stars": 4, "tracking_scope": "official",
        })),
        resolve_channel=AsyncMock(return_value=SimpleNamespace(
            id=100, guild=SimpleNamespace(id=123), send=AsyncMock(return_value=SimpleNamespace(id=800)),
        )),
        is_tracked_operator=Mock(return_value=True), staff_alert=AsyncMock(),
        get_testing=Mock(return_value=False),
        tracked_operators=AsyncMock(return_value={42, 43, 44, 45, 46}),
        chat_votes=Mock(collect=AsyncMock(return_value=({}, False))),
        images=Mock(extract_play=Mock(return_value={"legs": [{"selection": "Home ML", "odds": 150}]})),
        store=Mock(
            claim=Mock(return_value=True), pending=Mock(return_value=[]), source_message_id=Mock(return_value=None),
            get=Mock(return_value={
                "state": "pending", "eligible_voter_ids": ["42", "43", "44", "45", "46"], "policy_version": "roster-v1",
            }),
        ),
    )


def message(vote=None):
    image = SimpleNamespace(
        content_type="image/png", url="https://cdn/slip",
        to_file=AsyncMock(return_value=Mock(filename="slip.png")),
        read=AsyncMock(return_value=b"original slip"),
    )
    return SimpleNamespace(
        id=500, poll=vote or poll(), content="", attachments=[image],
        author=SimpleNamespace(id=42, bot=False),
        guild=SimpleNamespace(id=123, get_member=Mock(return_value=SimpleNamespace(id=42))),
        channel=Mock(), jump_url="https://discord.com/channels/123/100/500",
    )


@pytest.mark.parametrize("labels,multiple", [
    (["Yes", "No"], False), (["Yes", "No", "Maybe"], False),
    (["Yes", "Yes", "Pass"], False), (["Yes", "No", "Pass"], True),
])
def test_invalid_ballots_are_not_accepted(labels, multiple):
    value = poll()
    value.answers = [SimpleNamespace(text=label, vote_count=3) for label in labels]
    value.multiple = multiple
    with pytest.raises(ValueError):
        ballot_counts(value)


@pytest.mark.parametrize("yes", [3, 4, 5])
def test_approved_ballot_uses_team_identity_rating_and_vote_units(monkeypatch, yes, mock_play_cards):
    monkeypatch.setattr(module, "GUILD_ID", 123)
    cog = make_cog()
    source = message(poll(yes, 1, passed=10))
    cog.official.create_play_record.return_value.update(units=yes - 2, stars=yes)
    asyncio.run(cog.evaluate(source))
    kwargs = cog.official.create_play_record.call_args.kwargs
    assert kwargs["discord_user_id"] == "900" and kwargs["username"] == "Official Playmaker Picks"
    assert kwargs["units"] == yes - 2 and kwargs["stars"] == yes
    assert kwargs["tracking_scope"] == "official" and kwargs["ballot_message_id"] == "500"
    cog.images.extract_play.assert_called_once_with("https://cdn/slip", "Home ML\n", require_readable=True)
    embed = cog.resolve_channel.return_value.send.call_args.kwargs["embed"]
    sent = cog.resolve_channel.return_value.send.call_args.kwargs
    assert sent["content"] == f"<@&{module.HIGHROLLER_ROLE_ID}>"
    mentions = sent["allowed_mentions"].to_dict()
    assert mentions == {"roles": [module.HIGHROLLER_ROLE_ID], "parse": []}
    assert sent["allowed_mentions"].replied_user is False
    assert embed.author.name == "Playmaker Picks Team" and embed.author.icon_url == "https://cdn/bot"
    assert embed.fields == []
    assert "approval" not in embed.description.lower() and "Official stake" not in embed.description
    rendered_play = mock_play_cards.call_args.args[0]
    assert rendered_play["stars"] == yes and rendered_play["units"] == yes - 2
    assert embed.image.url == f"attachment://{sent['file'].filename}"
    updated = build_play_card_embed(
        SimpleNamespace(embeds=[embed]),
        {"id": 7, "status": "win", "units": yes - 2, "odds": 150},
    )
    assert updated.image.url == embed.image.url
    assert sent["file"].filename == "playmaker-card-7.png"
    assert embed.footer.text is None
    cog.official.attach_message_id.assert_called_once_with(7, 800)
    assert cog.store.finish.call_args.kwargs["official_message_id"] == "800"
    assert cog.store.finish.call_args.kwargs["official_channel_id"] == "100"
    cog.staff_alert.assert_not_awaited()


@pytest.mark.parametrize("yes,no", [(0, 0), (2, 0), (2, 3)])
def test_rejected_ballot_never_reads_or_posts_a_bet(monkeypatch, yes, no):
    monkeypatch.setattr(module, "GUILD_ID", 123)
    cog = make_cog()
    asyncio.run(cog.evaluate(message(poll(yes, no))))
    assert cog.store.finish.call_args.args[1] == "rejected"
    cog.images.extract_play.assert_not_called()
    cog.official.create_play_record.assert_not_called()
    cog.resolve_channel.assert_not_awaited()


@pytest.mark.parametrize("final,claim", [(False, True), (True, False)])
def test_unfinished_or_already_claimed_ballot_is_not_published(final, claim):
    cog = make_cog()
    cog.store.claim.return_value = claim
    asyncio.run(cog.evaluate(message(poll(final=final))))
    cog.official.create_play_record.assert_not_called()
    cog.resolve_channel.assert_not_awaited()


@pytest.mark.parametrize("blocked", ["event-started", "testing"])
def test_publication_rechecks_start_time_and_testing_before_discord_send(blocked):
    from datetime import datetime, timedelta, timezone

    cog = make_cog()
    if blocked == "testing":
        cog.get_testing.return_value = True
    decision = {
        "policy_version": "roster-v1", "yes": 4, "roster_size": 5,
        "counts": {"yes": 4, "no": 0, "pass": 0}, "units": 2, "stars": 4,
    }
    if blocked == "event-started":
        decision["event_starts_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError):
        asyncio.run(cog.publish_approved(
            message(), {"legs": [{"selection": "Home ML", "odds": 150}]}, decision, slip=b"original",
        ))
    cog.resolve_channel.return_value.send.assert_not_awaited()


@pytest.mark.parametrize("failure", ["author", "read", "publish", "link"])
def test_failures_are_durable_and_alert_staff_without_retrying(monkeypatch, failure):
    monkeypatch.setattr(module, "GUILD_ID", 123)
    cog = make_cog()
    if failure == "author":
        cog.is_tracked_operator.return_value = False
    elif failure == "read":
        cog.images.extract_play.side_effect = ValueError("Unreadable bet")
    elif failure == "publish":
        cog.resolve_channel.return_value.send.side_effect = RuntimeError("Discord unavailable")
    else:
        cog.official.attach_message_id.side_effect = RuntimeError("Database unavailable")
    asyncio.run(cog.evaluate(message()))
    assert cog.store.finish.call_args.args[1] == "failed"
    cog.staff_alert.assert_awaited_once()
    assert "not be republished automatically" in cog.staff_alert.call_args.args[1]


def test_unreadable_bet_alert_names_the_reason_and_recovery_steps(monkeypatch):
    monkeypatch.setattr(module, "GUILD_ID", 123)
    cog = make_cog()
    cog.images.extract_play.side_effect = module.BallotUnreadable(
        "The ballot bet text could not be read unambiguously. No odds are given.")
    asyncio.run(cog.evaluate(message()))
    assert cog.store.finish.call_args.args[1] == "failed"
    text = cog.staff_alert.call_args.args[1]
    assert "No odds are given." in text
    assert "source_message_id" in text and "vision providers" not in text


def test_followup_image_must_be_immediate_and_same_author():
    cog = make_cog()
    source = message()
    image = source.attachments[0]
    source.attachments = []

    async def history(**kwargs):
        assert kwargs == {"limit": 1, "after": source, "oldest_first": True}
        yield SimpleNamespace(author=SimpleNamespace(id=42), poll=None, attachments=[image], content="")

    source.channel.history = history
    assert asyncio.run(cog.bet_source(source))[0] is image

    async def wrong_author(**kwargs):
        yield SimpleNamespace(author=SimpleNamespace(id=43), poll=None, attachments=[image])

    source.channel.history = wrong_author
    assert asyncio.run(cog.bet_source(source)) == (None, "Home ML\n")


def test_testing_disables_ballot_publication():
    cog = make_cog()
    cog.get_testing.return_value = True
    asyncio.run(cog.evaluate_ballots.coro(cog))
    cog.resolve_channel.assert_not_awaited()


def test_daily_cleanup_deletes_every_channel_post_and_closes_active_ballots(monkeypatch):
    from datetime import datetime, timezone

    monkeypatch.setattr(module, "GUILD_ID", 123)
    assert module.BALLOT_CLEANUP_TIME.hour == 3 and module.BALLOT_CLEANUP_TIME.minute == 0
    assert module.BALLOT_CLEANUP_TIME.tzinfo.key == "America/New_York"
    cog = make_cog()
    cog.store.scan_state.return_value = {
        "last_message_id": "50", "started_at": "2026-10-14T00:00:00+00:00",
        "policy_started_at": "2026-10-20T00:00:00+00:00",
    }
    cog.store.pending.return_value = [{"message_id": "101"}]
    cog.store.interrupted.return_value = [{"message_id": "102"}]
    poll_message = SimpleNamespace(
        id=100, poll=poll(), author=SimpleNamespace(id=42, bot=False),
        created_at=datetime(2026, 10, 21, tzinfo=timezone.utc),
    )
    channel = cog.resolve_channel.return_value
    channel.guild.id = 123
    channel.guild.get_member = Mock()
    channel.purge = AsyncMock(return_value=[poll_message, SimpleNamespace(id=90)])

    async def history(**kwargs):
        assert kwargs == {"limit": None, "after": discord.Object(id=50), "oldest_first": True}
        yield poll_message

    channel.history = history
    asyncio.run(cog.daily_ballot_cleanup.coro(cog))

    channel.purge.assert_awaited_once_with(
        limit=None, reason="Scheduled daily capper ballot channel cleanup",
    )
    cog.store.finish.assert_any_call(
        101, "failed", error="Ballot post removed by the scheduled 3:00 AM Eastern channel cleanup.",
    )
    cog.store.finish.assert_any_call(
        102, "failed", error="Ballot post removed by the scheduled 3:00 AM Eastern channel cleanup.",
    )
    cog.staff_alert.assert_not_awaited()


def test_daily_cleanup_is_disabled_in_testing():
    cog = make_cog()
    cog.get_testing.return_value = True
    asyncio.run(cog.daily_ballot_cleanup.coro(cog))
    cog.resolve_channel.assert_not_awaited()


def test_daily_cleanup_reports_channel_deletion_failures(monkeypatch):
    monkeypatch.setattr(module, "GUILD_ID", 123)
    cog = make_cog()
    cog.store.scan_state.return_value = {"last_message_id": "50", "started_at": "2026-10-14T00:00:00+00:00"}
    cog.store.pending.return_value = []
    cog.store.interrupted.return_value = []
    channel = cog.resolve_channel.return_value
    channel.guild.id = 123

    async def history(**kwargs):
        assert kwargs == {"limit": None, "after": discord.Object(id=50), "oldest_first": True}
        if False:
            yield None

    channel.history = history
    channel.purge = AsyncMock(side_effect=RuntimeError("Missing Manage Messages permission"))
    asyncio.run(cog.daily_ballot_cleanup.coro(cog))
    cog.staff_alert.assert_awaited_once()
    assert cog.staff_alert.call_args.args[0] == "ballot_channel_cleanup"
    assert "Missing Manage Messages permission" in cog.staff_alert.call_args.args[1]


def test_current_discord_poll_labels_are_supported():
    value = poll()
    for answer, text in zip(value.answers, ["Yes lets Ride", "No, I like Montreal", "Pass on this Game"]):
        answer.text = text
    assert ballot_counts(value) == {"yes": 4, "no": 1, "pass": 0}


def test_no_image_posts_readable_text_without_capper_credit(monkeypatch):
    monkeypatch.setattr(module, "GUILD_ID", 123)
    cog = make_cog()
    cog.images.extract_text_play.return_value = {"legs": [{"selection": "Capitals ML", "odds": -110}]}
    source = message()
    source.poll.question = "Capitals ML -110"
    source.attachments = []

    async def history(**kwargs):
        if False:
            yield None

    source.channel.history = history
    cog.official.create_play_record.return_value["play_text"] = "Leg 1: Capitals ML (-110)"
    asyncio.run(cog.evaluate(source))
    cog.images.extract_text_play.assert_called_once_with("Capitals ML -110\n")
    sent = cog.resolve_channel.return_value.send.call_args.kwargs
    assert sent["file"].filename == "playmaker-card-7.png"
    assert sent["embed"].image.url == "attachment://playmaker-card-7.png"
    assert sent["embed"].fields == []
    assert sent["embed"].author.name == "Playmaker Picks Team"
    cog.staff_alert.assert_not_awaited()


def test_late_image_uses_explicit_staff_source_not_an_unrelated_upload():
    cog = make_cog()
    source = message()
    image = source.attachments[0]
    source.attachments = []
    cog.store.source_message_id.return_value = 600
    source.channel.fetch_message = AsyncMock(return_value=SimpleNamespace(
        attachments=[image], poll=None, author=SimpleNamespace(bot=False), content="Confirmed slip",
    ))
    result = asyncio.run(cog.bet_source(source))
    assert result == (image, "Home ML\n\nConfirmed slip")
    source.channel.fetch_message.assert_awaited_once_with(600)


def test_discovery_resumes_cursor_and_never_overwrites_saved_ballot_state():
    from datetime import datetime, timezone

    cog = make_cog()
    cog.store.scan_state.return_value = {
        "last_message_id": "100", "started_at": "2015-01-01T00:00:00+00:00",
        "policy_started_at": "2026-10-20T00:00:00+00:00",
    }

    async def history(**kwargs):
        assert kwargs["after"].id == 100 and kwargs["oldest_first"] is True
        yield SimpleNamespace(
            id=101, poll=poll(), author=SimpleNamespace(id=42, bot=False),
            created_at=datetime(2026, 10, 21, tzinfo=timezone.utc),
        )
        yield SimpleNamespace(id=102, poll=None, author=SimpleNamespace(bot=False))

    asyncio.run(cog.discover(SimpleNamespace(history=history, guild=SimpleNamespace(get_member=Mock()))))
    cog.store.remember.assert_called_once_with(101, ["42", "43", "44", "45", "46"])
    assert [call.args[0] for call in cog.store.advance.call_args_list] == [101, 102]


@pytest.mark.parametrize("size,yes,units", [(3, 2, 2), (3, 3, 3), (4, 3, 2), (4, 4, 3), (5, 3, 1), (5, 4, 2), (5, 5, 3)])
def test_dynamic_units_are_claimed_saved_and_rendered(monkeypatch, size, yes, units, mock_play_cards):
    monkeypatch.setattr(module, "GUILD_ID", 123)
    cog = make_cog()
    cog.store.get.return_value["eligible_voter_ids"] = [str(value) for value in range(42, 42 + size)]
    cog.official.create_play_record.return_value.update(units=units, stars=units + 2)
    asyncio.run(cog.evaluate(message(poll(yes=yes, no=0))))
    decision = cog.store.claim.call_args.args[1]
    assert decision["units"] == units
    assert decision["stars"] == units + 2
    assert decision["roster_size"] == size
    kwargs = cog.official.create_play_record.call_args.kwargs
    assert kwargs["units"] == units and kwargs["team_approval"] == decision
    assert mock_play_cards.call_args.args[0]["units"] == units
    description = cog.resolve_channel.return_value.send.call_args.kwargs["embed"].description
    assert "approval" not in description.lower() and "Official stake" not in description


def test_native_vote_overrides_confirmed_chat_and_never_double_counts(monkeypatch):
    monkeypatch.setattr(module, "GUILD_ID", 123)
    cog = make_cog()
    cog.store.get.return_value["eligible_voter_ids"] = ["42", "43", "44"]
    cog.chat_votes.collect.return_value = ({"42": "no", "43": "yes", "44": "yes"}, False)
    asyncio.run(cog.evaluate(message(poll(yes=1, no=0))))
    decision = cog.store.claim.call_args.args[1]
    assert decision["yes"] == 3 and decision["units"] == 3
    assert decision["native_voter_ids"] == ["42"]
    assert decision["chat_voter_ids"] == ["43", "44"]


def test_unconfirmed_statement_holds_closed_ballot(monkeypatch):
    monkeypatch.setattr(module, "GUILD_ID", 123)
    cog = make_cog()
    cog.chat_votes.collect.return_value = ({}, True)
    asyncio.run(cog.evaluate(message()))
    cog.store.claim.assert_not_called()
    cog.official.create_play_record.assert_not_called()


def test_open_poll_collects_chat_without_publishing(monkeypatch):
    monkeypatch.setattr(module, "GUILD_ID", 123)
    cog = make_cog()
    asyncio.run(cog.evaluate(message(poll(final=False))))
    cog.chat_votes.collect.assert_awaited_once()
    cog.store.claim.assert_not_called()


@pytest.mark.parametrize("action", ["confirm", "reject"])
def test_verification_buttons_validate_source_and_persist_actor(monkeypatch, action):
    monkeypatch.setattr(module, "GUILD_ID", 123)
    monkeypatch.setattr(module, "CAPPER_BALLOT_CHANNEL_ID", 100)
    cog = make_cog()
    cog.chat_votes.store.get.return_value = {"prompt_message_id": "900", "escalation_message_id": "901"}
    cog.chat_votes.valid_source = AsyncMock(return_value=True)
    cog.chat_votes.store.approve.return_value = "confirmed" if action == "confirm" else "rejected"
    interaction = SimpleNamespace(
        data={"custom_id": f"pm:cv:501:{action}"}, guild_id=123, channel_id=100,
        user=SimpleNamespace(id=42, bot=False), message=SimpleNamespace(id=900),
        response=SimpleNamespace(defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()),
    )
    asyncio.run(cog.on_vote_verification(interaction))
    cog.chat_votes.store.approve.assert_called_once_with("501", "42", action == "reject")
    assert "not accepted" not in interaction.followup.send.call_args.args[0]


@pytest.mark.parametrize("reason", ["guild", "channel", "bot", "role", "message", "source", "testing"])
def test_invalid_verification_controls_never_approve(monkeypatch, reason):
    monkeypatch.setattr(module, "GUILD_ID", 123)
    monkeypatch.setattr(module, "CAPPER_BALLOT_CHANNEL_ID", 100)
    cog = make_cog()
    cog.chat_votes.store.get.return_value = {"prompt_message_id": "900", "escalation_message_id": "901"}
    cog.chat_votes.valid_source = AsyncMock(return_value=reason != "source")
    interaction = SimpleNamespace(
        data={"custom_id": "pm:cv:501:confirm"}, guild_id=0 if reason == "guild" else 123,
        channel_id=0 if reason == "channel" else 100, user=SimpleNamespace(id=42, bot=reason == "bot"),
        message=SimpleNamespace(id=0 if reason == "message" else 900),
        response=SimpleNamespace(defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()),
    )
    if reason == "role":
        cog.is_tracked_operator.return_value = False
    if reason == "testing":
        cog.get_testing.return_value = True
    asyncio.run(cog.on_vote_verification(interaction))
    cog.chat_votes.store.approve.assert_not_called()
    assert "Verification not accepted" in interaction.followup.send.call_args.args[0]


def test_discord_voter_list_must_be_complete(monkeypatch):
    monkeypatch.setattr(module, "GUILD_ID", 123)
    cog = make_cog()
    source = message()
    source.poll.answers[0].vote_count += 1
    asyncio.run(cog.evaluate(source))
    cog.store.claim.assert_not_called()
    cog.staff_alert.assert_awaited_once()
    assert "voter list does not match" in cog.staff_alert.call_args.args[1]


@pytest.mark.parametrize("roster", [None, set(), {42, 43}, {42, 43, 44, 45, 46, 47}])
def test_bad_new_roster_holds_only_the_new_ballot_and_advances_discovery(roster):
    from datetime import datetime, timezone

    cog = make_cog()
    cog.tracked_operators.return_value = roster
    cog.store.scan_state.return_value = {
        "last_message_id": "100", "policy_started_at": "2026-10-20T00:00:00+00:00",
    }

    async def history(**kwargs):
        yield SimpleNamespace(
            id=101, poll=poll(), author=SimpleNamespace(id=42, bot=False),
            created_at=datetime(2026, 10, 21, tzinfo=timezone.utc), jump_url="https://discord.com/channels/123/100/101",
        )

    asyncio.run(cog.discover(SimpleNamespace(history=history, guild=SimpleNamespace(get_member=Mock()))))
    cog.store.hold.assert_called_once()
    cog.store.remember.assert_not_called()
    cog.store.advance.assert_called_once_with(101)
    cog.staff_alert.assert_awaited_once()


def test_cleanup_does_not_require_a_current_eligible_roster(monkeypatch):
    monkeypatch.setattr(module, "GUILD_ID", 123)
    cog = make_cog()
    cog.tracked_operators.return_value = None
    cog.store.pending.return_value = []
    cog.store.interrupted.return_value = []
    channel = cog.resolve_channel.return_value
    channel.purge = AsyncMock(return_value=[])
    asyncio.run(cog.daily_ballot_cleanup.coro(cog))
    channel.purge.assert_awaited_once()
    cog.tracked_operators.assert_not_awaited()
