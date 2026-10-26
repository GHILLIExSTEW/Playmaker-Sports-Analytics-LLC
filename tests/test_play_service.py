import math

import pytest

from src.services.play_service import PlayService


def test_calculate_to_win_for_positive_odds():
    service = PlayService()
    assert math.isclose(service.calculate_to_win(10, 150), 15.0, rel_tol=1e-9)


def test_calculate_to_win_for_negative_odds():
    service = PlayService()
    assert math.isclose(service.calculate_to_win(10, -110), 9.09, rel_tol=1e-9)


def test_validate_play_rejects_bad_input():
    service = PlayService()
    bad = service.validate_play(units=0, legs=0, odds=0)
    assert bad is not None
    assert "units" in bad["message"].lower()


def test_validate_play_accepts_valid_input():
    service = PlayService()
    ok = service.validate_play(units=2.5, legs=2, odds=164)
    assert ok is None


def test_validate_play_accepts_five_units():
    service = PlayService()
    assert service.validate_play(units=5, legs=1, odds=-125) is None
    assert service.calculate_to_win(5, -125) == 4.0


def test_combine_american_odds_for_multiple_legs():
    service = PlayService()
    assert service.combine_american_odds([-110, -110]) == 264


@pytest.mark.parametrize("units", [3.01, 10, float("nan"), float("inf")])
def test_personal_capper_stake_limit_rejects_invalid_or_over_three_units(units):
    assert PlayService.validate_capper_units(units) is not None


@pytest.mark.parametrize("units", [0.1, 1.25, 3])
def test_personal_capper_stake_limit_allows_up_to_three_units(units):
    assert PlayService.validate_capper_units(units) is None


def test_normalize_decimal_odds_to_american():
    service = PlayService()
    assert service.normalize_odds("1.44") == 44
    assert service.normalize_odds("1.57") == 57
    assert service.normalize_odds("\u2212110") == -110
    assert service.normalize_odds("(+150)") == 150
    assert service.normalize_odds("EVEN") == 100
    assert service.normalize_odds("1.99x") == -101
    assert service.normalize_odds("3.5x") == 250
    assert service.normalize_odds("2x") == 100
