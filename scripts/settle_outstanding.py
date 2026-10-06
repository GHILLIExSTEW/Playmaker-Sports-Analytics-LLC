"""Try to settle every outstanding official play from cached final scores.

Dry run by default; pass --apply to write results. Plays whose legs lack structured
game details, or whose games are not final yet, are reported and left open.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services.official_play_service import official_play_service
from src.services.play_features_service import GRADABLE_SPORTS, play_features_service

OUTSTANDING = ["open", "regraded"]


def outstanding_plays() -> list[dict]:
    client = play_features_service._client()
    plays = play_features_service._all(lambda: client.table("plays").select(
        "id,user_id,units,odds,legs,status,message_id,team_name,created_at"
    ).in_("status", OUTSTANDING).order("id"))
    if not plays:
        return []
    ids = [int(play["id"]) for play in plays]
    legs = play_features_service._all(lambda: client.table("play_legs").select(
        "play_id,leg_number,selection,odds,details"
    ).in_("play_id", ids).order("play_id"))
    for play in plays:
        play["leg_records"] = sorted((leg for leg in legs if int(leg["play_id"]) == int(play["id"])), key=lambda leg: leg["leg_number"])
    return plays


def skip_reason(play: dict) -> str | None:
    legs = play["leg_records"]
    if not legs:
        return "no legs recorded"
    if play["status"] == "open" and len(legs) != int(play.get("legs") or 0):
        return f"{len(legs)} leg rows but play has {play.get('legs')} legs"
    missing = [leg["leg_number"] for leg in legs if (leg.get("details") or {}).get("sport") not in GRADABLE_SPORTS]
    if missing:
        return f"legs {missing} have no gradable game details (manual entry or unsupported sport)"
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="settle plays that grade cleanly")
    args = parser.parse_args()

    plays = outstanding_plays()
    print(f"{len(plays)} outstanding plays ({'APPLY' if args.apply else 'dry run'})\n")
    settled = graded = 0
    for play in plays:
        label = f"#{play['id']} [{play['status']}] {play.get('team_name') or ''} {play.get('units')}u".strip()
        reason = skip_reason(play)
        if reason:
            print(f"SKIP   {label}: {reason}")
            continue
        try:
            suggestion = play_features_service.suggest(play)
        except Exception as exc:
            print(f"ERROR  {label}: {exc}")
            continue
        if suggestion is None:
            print(f"WAIT   {label}: game not final or not matched in schedule")
            continue
        graded += 1
        print(f"{suggestion['result'].upper():6} {label}")
        for note in suggestion["notes"]:
            print(f"         {note}")
        if args.apply:
            official_play_service.settle_play(int(play["id"]), suggestion["result"])
            play_features_service.mark_suggested(int(play["id"]))
            settled += 1
    print(f"\n{graded} gradable, {settled} settled, {len(plays) - graded} left outstanding")


if __name__ == "__main__":
    main()
