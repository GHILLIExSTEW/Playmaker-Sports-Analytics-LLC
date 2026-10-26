from __future__ import annotations

import math
import re
from decimal import Decimal, InvalidOperation
from typing import Any


MAX_CAPPER_UNITS = 3


class PlayService:
    """Business logic for official play validation and profit calculations."""

    @staticmethod
    def calculate_to_win(units: float, odds: int) -> float:
        odds_int = int(odds)
        if odds_int == 0:
            raise ValueError("odds cannot be 0")
        if odds_int > 0:
            profit = float(units * (odds_int / 100.0))
        else:
            profit = float(units * (100.0 / abs(odds_int)))
        return round(profit, 2)

    @staticmethod
    def combine_american_odds(odds_values: list[int]) -> int:
        if not odds_values or any(int(odds) == 0 for odds in odds_values):
            raise ValueError("Each leg must have non-zero odds.")

        decimal_total = 1.0
        for odds in odds_values:
            odds_int = int(odds)
            decimal_total *= 1 + (odds_int / 100 if odds_int > 0 else 100 / abs(odds_int))

        if decimal_total >= 2:
            return round((decimal_total - 1) * 100)
        return round(-100 / (decimal_total - 1))

    @staticmethod
    def validate_play(units: Any, legs: Any, odds: Any) -> dict[str, str] | None:
        try:
            units_value = float(units)
        except (TypeError, ValueError, InvalidOperation):
            return {"message": "Units must be a valid number."}
        if not math.isfinite(units_value):
            return {"message": "Units must be a valid number."}

        try:
            legs_value = int(legs)
        except (TypeError, ValueError):
            return {"message": "Legs must be a valid integer."}

        try:
            odds_value = int(odds)
        except (TypeError, ValueError):
            return {"message": "Odds must be a valid integer."}

        if units_value <= 0:
            return {"message": "Units must be greater than zero."}
        if legs_value < 1:
            return {"message": "Legs must be at least 1."}
        if odds_value == 0:
            return {"message": "Odds cannot be zero."}

        return None

    @staticmethod
    def validate_capper_units(units: Any) -> str | None:
        try:
            units_value = float(units)
        except (TypeError, ValueError, InvalidOperation):
            return "Units must be a valid number."
        if not math.isfinite(units_value):
            return "Units must be a valid number."
        if units_value > MAX_CAPPER_UNITS:
            return f"Personal capper plays cannot exceed {MAX_CAPPER_UNITS} units."
        return None

    @staticmethod
    def normalize_odds(value: Any) -> int:
        text = str(value).translate({0x2212: "-", 0x2013: "-", 0x2014: "-"})
        text = re.sub(r"[\s()\[\],]", "", text)
        if text.lower() in {"even", "evens", "ev", "pk"}:
            return 100
        if text[-1:] in {"x", "X", "\u00d7"} and len(text) > 1:
            # Sleeper shows a payout multiplier (total return per 1 staked), e.g. 1.99x.
            try:
                multiplier = float(text[:-1])
            except ValueError as exc:
                raise ValueError("Odds must be an integer like -110 or +164, or a multiplier like 1.99x") from exc
            if multiplier <= 1:
                raise ValueError("A multiplier must be greater than 1.00x.")
            profit = multiplier - 1
            return round(profit * 100) if profit >= 1 else -round(100 / profit)
        if not text:
            raise ValueError("Odds cannot be empty.")
        if text.startswith("+"):
            text = text[1:]
        try:
            if "." in text:
                decimal_odds = float(text)
                if decimal_odds <= 1:
                    raise ValueError
                return round((decimal_odds - 1) * 100)
            return int(text)
        except ValueError as exc:
            raise ValueError("Odds must be an integer like -110 or +164") from exc

    @staticmethod
    def status_for_settlement(result: str) -> str:
        normalized = (result or "").strip().lower()
        if normalized in {"win", "loss", "void", "partial", "regraded"}:
            return normalized
        return "open"
