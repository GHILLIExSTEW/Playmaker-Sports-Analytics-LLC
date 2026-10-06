from datetime import datetime

from src.services.play_features_service import (
    EASTERN,
    build_recap,
    clean_leg_details,
    combine_leg_results,
    current_streak,
    format_record,
    grade_leg,
    play_sport_slug,
    previous_month,
    previous_week,
    record_summary,
    team_matches,
    vault_leaderboard,
)


def final(home, away, status="FT"):
    return {"status_code": status, "home_score": home, "away_score": away}


def test_clean_leg_details_keeps_known_fields_and_rejects_unknown_sport():
    details = clean_leg_details({
        "selection": "Lakers -3.5", "sport": "Basketball", "home_name": " Los Angeles  Lakers ",
        "away_name": "Celtics", "event_date": "2026-10-05", "market": "Spread", "side": "Home",
        "line": "-3.5", "scope": "full_game", "extra": "ignored",
    })
    assert details == {
        "sport": "basketball", "home_name": "Los Angeles Lakers", "away_name": "Celtics",
        "event_date": "2026-10-05", "market": "spread", "side": "home", "line": -3.5, "scope": "full_game",
    }
    assert clean_leg_details({"selection": "x", "sport": "curling"}) is None
    assert clean_leg_details({"selection": "x"}) is None
    assert clean_leg_details({"sport": "nfl", "event_date": "soon", "line": True})["event_date"] is None


def test_play_sport_slug():
    assert play_sport_slug([{"details": {"sport": "nfl"}}, {"details": {"sport": "nfl"}}]) == "nfl"
    assert play_sport_slug([{"details": {"sport": "nfl"}}, {"details": {"sport": "hockey"}}]) == "mixed"
    assert play_sport_slug([{"details": None}]) is None


def test_team_matches_requires_meaningful_fragment():
    assert team_matches("Lakers", "Los Angeles Lakers")
    assert team_matches("LA Lakers", "LA Lakers")
    assert not team_matches("LA", "Los Angeles Lakers")
    assert not team_matches("", "Lakers")


def test_grade_leg_moneyline_spread_total():
    assert grade_leg({"market": "moneyline", "side": "home"}, final(110, 100)) == "win"
    assert grade_leg({"market": "moneyline", "side": "away"}, final(110, 100)) == "loss"
    assert grade_leg({"market": "spread", "side": "home", "line": -10}, final(110, 100)) == "void"
    assert grade_leg({"market": "spread", "side": "away", "line": 10.5}, final(110, 100)) == "win"
    assert grade_leg({"market": "total", "side": "over", "line": 209.5}, final(110, 100)) == "win"
    assert grade_leg({"market": "total", "side": "under", "line": 209.5}, final(110, 100)) == "loss"
    assert grade_leg({"market": "total", "side": "over", "line": 200}, final({"total": 110}, {"total": 100})) == "win"


def test_grade_leg_refuses_unfinished_partial_or_unknown_markets():
    assert grade_leg({"market": "moneyline", "side": "home"}, final(1, 0, status="Q4")) is None
    assert grade_leg({"market": "moneyline", "side": "home", "scope": "partial"}, final(1, 0)) is None
    assert grade_leg({"market": "other", "side": "home"}, final(1, 0)) is None
    assert grade_leg({"market": "spread", "side": "home"}, final(1, 0)) is None
    assert grade_leg({"market": "moneyline", "side": "home"}, final(None, 0)) is None


def test_combine_leg_results():
    assert combine_leg_results(["win", None, "loss"]) == "loss"
    assert combine_leg_results(["win", "win"]) == "win"
    assert combine_leg_results(["void", "void"]) == "void"
    assert combine_leg_results(["win", "void"]) is None
    assert combine_leg_results(["win", None]) is None
    assert combine_leg_results([]) is None


def play(play_id, user_id, status, units=1, odds=100, settled="2026-10-05T15:00:00+00:00", message_id=None, sport_id=1):
    return {
        "id": play_id, "user_id": user_id, "status": status, "units": units, "odds": odds, "sport_id": sport_id,
        "message_id": message_id or str(play_id), "created_at": settled, "settled_at": settled,
    }


def test_record_summary_and_format():
    summary = record_summary([play(1, 1, "win"), play(2, 1, "loss"), play(3, 1, "void"), play(4, 1, "open")])
    assert (summary["wins"], summary["losses"], summary["voids"]) == (1, 1, 1)
    assert summary["net"] == 0
    assert summary["risked"] == 2
    assert format_record(summary) == "1-1-1"


def test_current_streak_ignores_pushes():
    plays = [
        play(1, 1, "loss", settled="2026-10-01T12:00:00+00:00"),
        play(2, 1, "win", settled="2026-10-02T12:00:00+00:00"),
        play(3, 1, "void", settled="2026-10-03T12:00:00+00:00"),
        play(4, 1, "win", settled="2026-10-04T12:00:00+00:00"),
    ]
    assert current_streak(plays) == "W2"
    assert current_streak([]) == "—"


def test_previous_week_and_month_boundaries():
    now = datetime(2026, 10, 5, 10, 0, tzinfo=EASTERN)  # Monday
    start, end = previous_week(now)
    assert (start.date().isoformat(), end.date().isoformat()) == ("2026-09-28", "2026-10-05")
    start, end = previous_month(datetime(2026, 10, 1, 10, 0, tzinfo=EASTERN))
    assert (start.date().isoformat(), end.date().isoformat()) == ("2026-09-01", "2026-10-01")


def test_build_recap_dedupes_messages_and_ranks_cappers():
    plays = [
        play(1, 1, "win", units=2, odds=150),
        play(2, 1, "win", units=2, odds=150, message_id="1"),  # duplicate tracked message
        play(3, 2, "loss", units=1),
        play(4, 2, "win", units=1, settled="2026-09-01T12:00:00+00:00"),  # outside the period
    ]
    users = [{"id": 1, "display_name": "Ace"}, {"id": 2, "username": "Bee"}]
    start = datetime(2026, 9, 28, tzinfo=EASTERN)
    end = datetime(2026, 10, 5, 23, tzinfo=EASTERN)
    recap = build_recap(plays, users, {1: "nfl"}, start, end)
    assert recap["count"] == 2
    assert [row["name"] for row in recap["cappers"]] == ["Ace", "Bee"]
    assert recap["cappers"][0]["net"] == 3
    assert recap["cappers"][1]["streak"] == "L1"
    assert recap["sports"][0]["sport"] == "NFL"
    assert recap["best_play"]["id"] == 1


def test_vault_leaderboard_filters_period_and_sorts():
    bets = [
        {"owner_id": "a", "owner_name": "A", "status": "win", "units": 1, "odds": 200, "settled_at": "2026-10-03T12:00:00+00:00"},
        {"owner_id": "b", "owner_name": "B", "status": "win", "units": 1, "odds": 100, "settled_at": "2026-10-03T12:00:00+00:00"},
        {"owner_id": "b", "owner_name": "B", "status": "loss", "units": 5, "odds": 100, "settled_at": "2026-09-03T12:00:00+00:00"},
        {"owner_id": "c", "owner_name": "C", "status": "win", "units": None, "odds": 100, "settled_at": "2026-10-03T12:00:00+00:00"},
    ]
    board = vault_leaderboard(bets, datetime(2026, 10, 1, tzinfo=EASTERN), datetime(2026, 11, 1, tzinfo=EASTERN))
    assert [row["name"] for row in board] == ["A", "B"]
    assert board[0]["net"] == 2
