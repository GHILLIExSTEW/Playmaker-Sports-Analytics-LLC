from datetime import date, datetime
from zoneinfo import ZoneInfo

from src.bot import build_official_tracker_embed


def test_tracker_uses_only_official_plays_and_settlement_time():
    now = datetime(2026, 9, 21, 12, tzinfo=ZoneInfo("America/New_York"))
    plays = [
        {"id": 1, "message_id": "101", "user_id": 1, "units": 3, "status": "win", "created_at": "2026-09-20T18:00:00+00:00", "settled_at": "2026-09-21T14:00:00+00:00"},
        {"id": 2, "message_id": "102", "user_id": 1, "units": 2, "status": "loss", "created_at": "2026-09-20T18:00:00+00:00", "settled_at": "2026-09-21T15:00:00+00:00"},
        {"id": 3, "message_id": "103", "user_id": 2, "units": 5, "status": "open", "created_at": "2026-09-21T15:00:00+00:00", "settled_at": None},
        {"id": 4, "message_id": "104", "user_id": 2, "units": 1, "status": "void", "created_at": "2026-09-20T18:00:00+00:00", "settled_at": "2026-09-21T15:00:00+00:00"},
        {"id": 5, "message_id": "105", "user_id": 2, "units": 1, "status": "win", "created_at": "2026-09-19T18:00:00+00:00", "settled_at": "2026-09-20T19:00:00+00:00"},
    ]
    users = [{"id": 1, "display_name": "MoneyPicks", "username": "money"}, {"id": 2, "display_name": "Lady4", "username": "lady"}]

    embed, top_lines, breakdown = build_official_tracker_embed(plays, users, now)

    assert [field.name for field in embed.fields] == ["⏳ Pending Bets", "📅 Monthly Units", "🗓️ Yearly Units"]
    assert embed.fields[0].value == "1 bet"
    assert embed.fields[1].value == "+2u"
    assert embed.fields[2].value == "+2u"
    assert "MoneyPicks" in breakdown
    assert "1-1" in breakdown
    assert "Lady4" in breakdown
    assert "\n\n" in breakdown
    assert "MoneyPicks" in top_lines[0]


def test_tracker_cutoff_excludes_plays_settled_before_the_start_date():
    now = datetime(2026, 9, 21, 12, tzinfo=ZoneInfo("America/New_York"))
    plays = [
        {"id": 1, "message_id": "101", "user_id": 1, "units": 3, "odds": 100, "status": "win", "created_at": "2026-09-20T18:00:00+00:00", "settled_at": "2026-09-21T14:00:00+00:00"},
        {"id": 2, "message_id": "102", "user_id": 1, "units": 2, "odds": 100, "status": "loss", "created_at": "2026-09-18T18:00:00+00:00", "settled_at": "2026-09-19T15:00:00+00:00"},
        {"id": 3, "message_id": "103", "user_id": 2, "units": 5, "odds": 100, "status": "open", "created_at": "2026-09-18T15:00:00+00:00", "settled_at": None},
    ]
    users = [{"id": 1, "display_name": "MoneyPicks"}, {"id": 2, "display_name": "Lady4"}]

    embed, top_lines, breakdown = build_official_tracker_embed(plays, users, now, cutoff=date(2026, 9, 21))

    assert embed.fields[0].value == "0 bets"
    assert embed.fields[1].value == "+3u"
    assert "Lady4" not in breakdown


def test_tracker_counts_each_published_message_once():
    now = datetime(2026, 9, 21, 12, tzinfo=ZoneInfo("America/New_York"))
    plays = [
        {"id": 1, "message_id": "123", "user_id": 1, "units": 2, "status": "open", "created_at": "2026-09-21T15:00:00+00:00", "settled_at": None},
        {"id": 2, "message_id": "123", "user_id": 1, "units": 2, "status": "open", "created_at": "2026-09-21T15:00:00+00:00", "settled_at": None},
        {"id": 3, "message_id": None, "user_id": 1, "units": 2, "status": "open", "created_at": "2026-09-21T15:00:00+00:00", "settled_at": None},
        {"id": 4, "message_id": "456", "user_id": 1, "units": 1, "status": "win", "created_at": "2026-09-21T15:00:00+00:00", "settled_at": "2026-09-21T15:30:00+00:00"},
    ]

    embed, _, _ = build_official_tracker_embed(plays, [{"id": 1, "display_name": "Operator"}], now)

    assert embed.fields[0].value == "1 bet"
    assert embed.fields[1].value == "+1u"