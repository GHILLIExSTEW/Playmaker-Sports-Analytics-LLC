from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.services.official_play_service import OfficialPlayService
import src.services.official_play_service as official_play_module


def test_settle_play_updates_valid_play_columns(monkeypatch):
    service = OfficialPlayService()
    updates = []

    monkeypatch.setattr(service, "_fetch_play", lambda play_id: {"units": 2})
    monkeypatch.setattr(
        official_play_module.supabase_service,
        "update",
        lambda table, payload, match: updates.append((table, payload, match)),
    )

    outcome = service.settle_play(42, "win")

    assert outcome == {"result": "win", "tally": 2.0}
    assert updates[0][0] == "plays"
    assert updates[0][2] == {"id": 42}
    assert set(updates[0][1]) == {"status", "settled_at"}
    assert datetime.fromisoformat(updates[0][1]["settled_at"]).tzinfo is not None


@pytest.mark.parametrize("recover", [False, True])
def test_ballot_metadata_is_on_play_and_response_not_legs(monkeypatch, recover):
    service = OfficialPlayService()
    monkeypatch.setattr(service, "_ensure_default_sport", lambda: 1)
    monkeypatch.setattr(service, "_ensure_user", lambda *args: 2)
    saved = {"id": 7, "user_id": 2, "sport_id": 1, "team_name": None, "play_text": "", "units": 2, "legs": 1, "odds": -118,
             "tracking_scope": "official", "stars": 4, "status": "open", "message_id": None}
    monkeypatch.setattr(official_play_module.supabase_service, "select", Mock(return_value=SimpleNamespace(data=[saved] if recover else [])))
    insert = Mock(return_value=SimpleNamespace(data=[{"id": 7}]))
    upsert = Mock()
    monkeypatch.setattr(official_play_module.supabase_service, "insert", insert)
    monkeypatch.setattr(official_play_module.supabase_service, "upsert", upsert)
    result = service.create_play_record(
        "900", "Official Playmaker Picks", 2, 1, "-118",
        leg_records=[{"selection": "Capitals ML", "odds": -118}],
        tracking_scope="official", stars=4, ballot_message_id="500",
    )
    assert result["stars"] == 4 and result["tracking_scope"] == "official"
    assert result["play_id"] == 7
    if recover:
        insert.assert_not_called()
    else:
        assert insert.call_args.args[1]["ballot_message_id"] == "500"
    assert set(upsert.call_args.args[1][0]) == {"play_id", "leg_number", "selection", "odds", "details"}


def test_personal_capper_play_over_three_units_is_rejected():
    service = OfficialPlayService()

    result = service.create_play_record(
    "900", "Ace", 10, 2, "+235125",
    tracking_scope="capper",
    leg_records=[
        {"selection": "L. James over 0.5 points", "odds": 100},
        {"selection": "P. Pritchard 2+ rebounds", "odds": 100},
    ],
    )

    assert result == {"error": "Personal capper plays cannot exceed 3 units."}


@pytest.mark.parametrize("scope", ["capper", "official"])
def test_created_play_returns_saved_team_details_for_card_artwork(monkeypatch, scope):
    service = OfficialPlayService()
    monkeypatch.setattr(service, "_ensure_sport", lambda _: 1)
    monkeypatch.setattr(service, "_ensure_user", lambda *args: 2)
    monkeypatch.setattr(
        official_play_module.supabase_service, "select",
        Mock(return_value=SimpleNamespace(data=[])),
    )
    insert = Mock(return_value=SimpleNamespace(data=[{"id": 361}]))
    monkeypatch.setattr(official_play_module.supabase_service, "insert", insert)
    leg = {
        "selection": "DAL Stars moneyline (3-way, excluding OT)",
        "odds": -105,
        "sport": "NHL",
        "home_name": "Dallas Stars",
        "away_name": "Montreal Canadiens",
        "market": "moneyline",
        "side": "home",
        "scope": "full_game",
    }

    result = service.create_play_record(
        "900", "Ace", 2.1, 1, "-105", tracking_scope=scope, leg_records=[leg],
    )

    saved_leg = insert.call_args.args[1][0]
    assert result["leg_records"] == [{
        "selection": saved_leg["selection"],
        "odds": saved_leg["odds"],
        "details": saved_leg["details"],
    }]
    assert result["leg_records"][0]["details"]["sport"] == "hockey"
    assert result["leg_records"][0]["details"]["home_name"] == "Dallas Stars"


def test_new_capper_play_snapshots_selected_frame_path(monkeypatch):
    service = OfficialPlayService()
    path = "capper-frames/11111111-1111-1111-1111-111111111111/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa.webp"
    monkeypatch.setattr(service, "_ensure_default_sport", lambda: 1)
    monkeypatch.setattr(service, "_ensure_user", lambda *args: 2)
    monkeypatch.setattr(
        official_play_module.supabase_service, "select",
        Mock(return_value=SimpleNamespace(data=[{"play_card_frame_path": path}])),
    )
    insert = Mock(return_value=SimpleNamespace(data=[{"id": 7}]))
    monkeypatch.setattr(official_play_module.supabase_service, "insert", insert)
    monkeypatch.setattr(official_play_module.supabase_service, "upsert", Mock())

    result = service.create_play_record("900", "Ace", 1, 1, "-110", tracking_scope="capper")

    assert result["capper_frame_path"] == path
    assert insert.call_args.args[1]["capper_frame_path"] == path


def test_team_approval_is_persisted_and_must_match_official_stake(monkeypatch):
    service = OfficialPlayService()
    monkeypatch.setattr(service, "_ensure_default_sport", lambda: 1)
    monkeypatch.setattr(service, "_ensure_user", lambda *args: 2)
    monkeypatch.setattr(official_play_module.supabase_service, "select", Mock(return_value=SimpleNamespace(data=[])))
    insert = Mock(return_value=SimpleNamespace(data=[{"id": 7}]))
    monkeypatch.setattr(official_play_module.supabase_service, "insert", insert)
    decision = {"yes": 2, "counts": {"no": 0}, "roster_size": 3, "units": 2, "stars": 4}
    result = service.create_play_record(
        "900", "Official", 2, 1, "-110", stars=4, ballot_message_id="500", team_approval=decision,
    )
    assert result["team_approval"] == decision
    assert insert.call_args.args[1]["team_approval"] == decision
    with pytest.raises(ValueError, match="must match"):
        service.create_play_record(
            "900", "Official", 1, 1, "-110", stars=3, ballot_message_id="500", team_approval=decision,
        )


def test_ballot_assigned_units_cannot_be_manually_edited(monkeypatch):
    service = OfficialPlayService()
    monkeypatch.setattr(service, "_fetch_play", lambda _: {"units": 2, "team_approval": {"yes": 2}})
    update = Mock()
    monkeypatch.setattr(official_play_module.supabase_service, "update", update)
    with pytest.raises(ValueError, match="cannot be edited"):
        service.edit_play_record(7, 1, "Team", ["Team ML"], [-110])
    update.assert_not_called()


@pytest.mark.parametrize("yes,size,units", [(2, 5, 1), (3, 5, 2), (4, 4, 3), (7, 7, 3)])
def test_slip_approval_uses_additional_supporters_not_legacy_majority(monkeypatch, yes, size, units):
    service = OfficialPlayService()
    monkeypatch.setattr(service, "_ensure_default_sport", lambda: 1)
    monkeypatch.setattr(service, "_ensure_user", lambda *args: 2)
    monkeypatch.setattr(official_play_module.supabase_service, "select", Mock(return_value=SimpleNamespace(data=[])))
    monkeypatch.setattr(official_play_module.supabase_service, "insert", Mock(return_value=SimpleNamespace(data=[{"id": 7}])))
    decision = {
        "policy_version": "slip-v1", "author_id": "42", "voter_choices": {"42": "yes"},
        "yes": yes, "roster_size": size, "counts": {"yes": yes, "no": 0, "pass": 0},
        "units": units, "stars": units + 2,
    }
    result = service.create_play_record(
        "900", "Official", units, 1, "-110", stars=units + 2,
        ballot_message_id="500", team_approval=decision,
    )
    assert result["units"] == units
    assert result["team_approval"] == decision
    decision["voter_choices"]["42"] = "pass"
    with pytest.raises(ValueError, match="author"):
        service.create_play_record(
            "900", "Official", units, 1, "-110", stars=units + 2,
            ballot_message_id="500", team_approval=decision,
        )


@pytest.mark.parametrize("selection,odds", [("Other ML", 100), ("Wolves ML", -110)])
def test_approved_slip_selection_and_odds_cannot_be_edited(monkeypatch, selection, odds):
    service = OfficialPlayService()
    monkeypatch.setattr(service, "_fetch_play", lambda _: {
        "units": 1, "team_name": None, "team_approval": {"policy_version": "slip-v1"},
    })
    monkeypatch.setattr(service, "get_play_legs", lambda _: [{"selection": "Wolves ML", "odds": 100}])
    update = Mock()
    monkeypatch.setattr(official_play_module.supabase_service, "update", update)
    with pytest.raises(ValueError, match="fresh slip review"):
        service.edit_play_record(7, 1, "", [selection], [odds])
    update.assert_not_called()