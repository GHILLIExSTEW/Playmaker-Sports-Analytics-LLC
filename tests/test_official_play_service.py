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