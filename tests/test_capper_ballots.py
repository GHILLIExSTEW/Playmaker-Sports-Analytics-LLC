import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import src.capper_ballots as module
from src.capper_ballots import CapperBallots, ballot_counts, ballot_rating


@pytest.mark.parametrize("yes", range(6))
@pytest.mark.parametrize("no", range(6))
def test_exact_vote_thresholds(yes, no):
    expected = (yes, yes - 2) if yes >= 3 and yes > no else None
    assert ballot_rating(yes, no) == expected


@pytest.mark.parametrize("yes,no", [(6, 0), (-1, 0), (3, -1)])
def test_out_of_range_votes_require_review(yes, no):
    with pytest.raises(ValueError):
        ballot_rating(yes, no)


def poll(yes=4, no=1, passed=0, final=True):
    return SimpleNamespace(
        answers=[SimpleNamespace(text=name, vote_count=count) for name, count in [("Yes", yes), ("No", no), ("Pass", passed)]],
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
            guild=SimpleNamespace(id=123), send=AsyncMock(return_value=SimpleNamespace(id=800)),
        )),
        is_tracked_operator=Mock(return_value=True), staff_alert=AsyncMock(),
        get_testing=Mock(return_value=False),
        images=Mock(extract_play=Mock(return_value={"legs": [{"selection": "Home ML", "odds": 150}]})),
        store=Mock(claim=Mock(return_value=True), pending=Mock(return_value=[]), source_message_id=Mock(return_value=None)),
    )


def message(vote=None):
    image = SimpleNamespace(
        content_type="image/png", url="https://cdn/slip",
        to_file=AsyncMock(return_value=Mock(filename="slip.png")),
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
def test_approved_ballot_uses_team_identity_rating_and_vote_units(monkeypatch, yes):
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
    assert embed.fields[3].value == "⭐" * yes
    assert embed.image.url is None
    assert sent["file"].filename == "slip.png"
    assert embed.footer.text is None
    cog.official.attach_message_id.assert_called_once_with(7, 800)
    assert cog.store.finish.call_args.kwargs["official_message_id"] == "800"
    cog.staff_alert.assert_not_awaited()
    source.attachments[0].to_file.return_value.close.assert_called_once()


@pytest.mark.parametrize("yes,no", [(0, 0), (2, 0), (3, 3), (4, 5)])
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
    assert "file" not in sent
    assert sent["embed"].fields[-1].value == "Leg 1: Capitals ML (-110)"
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
    cog = make_cog()
    cog.store.scan_state.return_value = {"last_message_id": "100", "started_at": "2015-01-01T00:00:00+00:00"}

    async def history(**kwargs):
        assert kwargs["after"].id == 100 and kwargs["oldest_first"] is True
        yield SimpleNamespace(id=101, poll=poll(), author=SimpleNamespace(bot=False))
        yield SimpleNamespace(id=102, poll=None, author=SimpleNamespace(bot=False))

    asyncio.run(cog.discover(SimpleNamespace(history=history)))
    cog.store.remember.assert_called_once_with(101)
    assert [call.args[0] for call in cog.store.advance.call_args_list] == [101, 102]
