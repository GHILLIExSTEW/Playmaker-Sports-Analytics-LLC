"""Shared text for model embeds and image cards so Discord surfaces describe projections identically."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from src.services.data_model_service import ModelGame
from src.services.model_engine import ModelProjection

DEGREE = "\u00b0"
DOT = " \u00b7 "


def one_decimal(value: float) -> str:
    # Half-up like the website's toFixed(1), so cards and the site show the same lines.
    return str(Decimal(value).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def percent(value: float) -> str:
    return f"{one_decimal(value * 100)}%"


def signed(value: float) -> str:
    text = one_decimal(value)
    return text if text.startswith("-") else f"+{text}"


def summary_line(game: ModelGame, projection: ModelProjection) -> str:
    margin = projection.projected_margin
    favorite = game.home if margin >= 0 else game.away
    return f"Projected total {one_decimal(projection.projected_total)}{DOT}{favorite} by {one_decimal(abs(margin))}"


def market_lines(game: ModelGame, projection: ModelProjection) -> list[str]:
    market = projection.market
    if market is None:
        return ["Market: no sportsbook consensus stored for this game."]
    parts = []
    if market.home_spread is not None:
        parts.append(f"{game.home} {signed(market.home_spread)}")
    if market.total is not None:
        parts.append(f"Total {one_decimal(market.total)}")
    if market.home_probability is not None:
        parts.append(f"{game.home} no-vig {percent(market.home_probability)}")
    first = f"Market ({market.books} books): " + DOT.join(parts)
    model = []
    if projection.market_edge is not None:
        side = game.home if projection.market_edge >= 0 else game.away
        model.append(f"Model edge {side} +{one_decimal(abs(projection.market_edge) * 100)} pts")
    if projection.cover_probability is not None:
        model.append(f"{game.home} cover {percent(projection.cover_probability)}")
    if projection.over_probability is not None:
        model.append(f"Over {percent(projection.over_probability)}")
    return [first, DOT.join(model)] if model else [first]


def factor_line(game: ModelGame, projection: ModelProjection) -> str:
    parts = []
    home_rest, away_rest = projection.rest_days
    if home_rest is not None or away_rest is not None:
        label = lambda days: "-" if days is None else f"{days}d"
        parts.append(f"Rest {game.away} {label(away_rest)} / {game.home} {label(home_rest)}")
    away_travel, home_travel = projection.travel_km[1], projection.travel_km[0]
    if away_travel:
        parts.append(f"{game.away} travel {away_travel:,.0f} km")
    if home_travel:
        parts.append(f"{game.home} travel {home_travel:,.0f} km")
    adjustments = [*projection.margin_adjustments, *projection.total_adjustments]
    if adjustments:
        parts.append("Adj " + ", ".join(f"{name} {signed(value)}" for name, value in adjustments))
    return "Factors: " + (DOT.join(parts) if parts else "no rest, travel or efficiency adjustment applied.")


def conditions_line(game: ModelGame, projection: ModelProjection) -> str | None:
    parts = []
    weather = projection.weather
    if weather is not None:
        parts.append(f"Weather {weather.temperature_f:.0f}{DEGREE}F, wind {weather.wind_mph:.0f} mph, "
                      f"precip {weather.precipitation_mm:.1f} mm ({weather.source})")
    home_points, away_points = projection.injury_points
    if home_points or away_points:
        listed = [item for item in projection.injuries if item.points > 0]
        names = ", ".join(f"{item.player} {item.position or '?'} {item.status}" for item in listed[:4])
        more = f" +{len(listed) - 4} more" if len(listed) > 4 else ""
        parts.append(f"Injuries (heuristic) {game.away} -{one_decimal(away_points)} / {game.home} -{one_decimal(home_points)} pts: {names}{more}")
    return DOT.join(parts) if parts else None


def track_record_line(report: dict | None) -> str:
    if not report or report.get("status") != "ok":
        return "Track record: walk-forward backtest not available yet."
    model, baseline = report.get("model") or {}, report.get("baseline") or {}
    accuracy = model.get("accuracy")
    return (f"Backtest ({report.get('games', 0):,} holdout games): Brier {model.get('brier', 0):.3f} vs old "
            f"{baseline.get('brier', 0):.3f}{DOT}log loss {model.get('log_loss', 0):.3f} vs {baseline.get('log_loss', 0):.3f}"
            + (f"{DOT}picks {percent(accuracy)}" if accuracy is not None else ""))


def samples_line(game: ModelGame, projection: ModelProjection) -> str:
    return (f"Samples: {game.away} {projection.away_sample}{DOT}{game.home} {projection.home_sample}"
            f"{DOT}League {projection.league_sample}")


METHOD = ("Opponent-adjusted ratings with home edge, learned rest/travel/weather effects, "
          "NFL EPA when it helps, calibrated probabilities. Not betting advice.")
