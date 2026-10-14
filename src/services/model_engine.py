"""Point-in-time sports projection engine.

Every game is predicted using only information available before it starts, then the
model learns from the result. Live projections and the walk-forward backtest share
this exact code path, so reported accuracy describes the model that is published.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from itertools import groupby
from statistics import median
from zoneinfo import ZoneInfo

from src.services.team_locations import Venue, distance_km

EASTERN = ZoneInfo("America/New_York")
FINAL_STATUSES = {"FT", "AOT", "AP", "COMPLETED", "FINISHED"}
SKIPPED_STATUSES = {"CANC", "PST", "ABD", "WO", "SUSP", "INTR", "AWD", "POST"}
POISSON_SPORTS = {"hockey", "baseball"}
FOOTBALL_SPORTS = {"nfl", "ncaa"}
MIN_TEAM_GAMES = 3
MIN_LEAGUE_GAMES = 30
NEW_SEASON_GAP_DAYS = 60
MODEL_VERSION = "ratings-v2"

MARGIN_FEATURES = ("Back-to-back", "Rest days", "Travel", "NFL EPA")
TOTAL_FEATURES = ("Back-to-back", "Wind", "Cold", "Precipitation", "Heat")

# Heuristic points removed from a team's projection per injured player (Out = 100%,
# Doubtful = 75%). API-Sports has no injury history, so these cannot be learned.
INJURY_POINTS = {"QB": 3.0, "RB": 0.3, "FB": 0.1, "WR": 0.5, "TE": 0.4, "OT": 0.4, "T": 0.4, "OG": 0.3, "G": 0.3,
                 "C": 0.3, "OL": 0.3, "DE": 0.4, "DT": 0.3, "NT": 0.3, "DL": 0.3, "LB": 0.3, "OLB": 0.3, "ILB": 0.3,
                 "MLB": 0.3, "CB": 0.4, "S": 0.3, "FS": 0.3, "SS": 0.3, "DB": 0.3, "K": 0.3, "PK": 0.3, "P": 0.1, "LS": 0.05}
INJURY_STATUS_WEIGHT = {"out": 1.0, "doubtful": 0.75}
INJURY_TEAM_CAP = 7.0


@dataclass(frozen=True)
class Params:
    learning_rate: float = 0.06
    season_regression: float = 0.5
    home_rate: float = 0.004
    league_rate: float = 0.004
    variance_rate: float = 0.01
    calibration_rate: float = 0.01
    use_efficiency: bool = True

    def as_dict(self) -> dict:
        return {"learning_rate": self.learning_rate, "season_regression": self.season_regression,
                "use_efficiency": self.use_efficiency}

    @classmethod
    def from_dict(cls, value: object) -> Params:
        if not isinstance(value, dict):
            return cls()
        try:
            rate, regression = float(value["learning_rate"]), float(value["season_regression"])
        except (KeyError, TypeError, ValueError):
            return cls()
        if not (0 < rate < 1 and 0 <= regression <= 1):
            return cls()
        return cls(learning_rate=rate, season_regression=regression,
                   use_efficiency=value.get("use_efficiency") is not False)


@dataclass(frozen=True)
class Weather:
    temperature_f: float
    wind_mph: float
    precipitation_mm: float
    source: str = "forecast"


@dataclass(frozen=True)
class Market:
    home_probability: float | None
    home_spread: float | None
    total: float | None
    books: int
    fetched_at: datetime


@dataclass(frozen=True)
class Injury:
    team_id: str
    player: str
    position: str | None
    status: str
    description: str

    @property
    def points(self) -> float:
        weight = INJURY_STATUS_WEIGHT.get(self.status.strip().lower(), 0.0)
        return weight * INJURY_POINTS.get((self.position or "").upper(), 0.25)


@dataclass(frozen=True)
class Efficiency:
    day: date
    offense_epa: float
    offense_plays: int
    defense_epa: float
    defense_plays: int


@dataclass
class Context:
    venues: dict[str, Venue] = field(default_factory=dict)
    weather: dict[str, Weather] = field(default_factory=dict)
    efficiency: dict[str, list[Efficiency]] = field(default_factory=dict)
    injuries: dict[str, list[Injury]] = field(default_factory=dict)
    markets: dict[str, Market] = field(default_factory=dict)
    now: datetime | None = None


@dataclass(frozen=True)
class ModelProjection:
    home_score: float
    away_score: float
    home_probability: float
    home_sample: int
    away_sample: int
    league_sample: int
    updated: datetime
    distribution: str = "normal"
    margin_sd: float = 0.0
    total_sd: float = 0.0
    margin_adjustments: tuple[tuple[str, float], ...] = ()
    total_adjustments: tuple[tuple[str, float], ...] = ()
    rest_days: tuple[int | None, int | None] = (None, None)
    travel_km: tuple[float | None, float | None] = (None, None)
    weather: Weather | None = None
    injuries: tuple[Injury, ...] = ()
    injury_points: tuple[float, float] = (0.0, 0.0)
    market: Market | None = None
    market_edge: float | None = None
    cover_probability: float | None = None
    over_probability: float | None = None

    @property
    def projected_margin(self) -> float:
        return self.home_score - self.away_score

    @property
    def projected_total(self) -> float:
        return self.home_score + self.away_score


def team_key(team_id: str | None, name: str) -> str:
    return f"id:{team_id}" if team_id else f"name:{name.strip().lower()}"


def normal_cdf(value: float) -> float:
    return 0.5 * (1 + math.erf(value / math.sqrt(2)))


def _logit(probability: float) -> float:
    probability = min(max(probability, 1e-4), 1 - 1e-4)
    return math.log(probability / (1 - probability))


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1 / (1 + math.exp(-value))
    exp = math.exp(value)
    return exp / (1 + exp)


def poisson_pmf(rate: float) -> list[float]:
    rate = max(rate, 0.05)
    size = int(rate + 10 * math.sqrt(rate) + 12)
    values = [math.exp(-rate)]
    for count in range(1, size):
        values.append(values[-1] * rate / count)
    return values


def _cdf(pmf: list[float]) -> list[float]:
    total, values = 0.0, []
    for value in pmf:
        total += value
        values.append(total)
    return values


def poisson_outcomes(home_rate: float, away_rate: float, home_line: float | None = None,
                     total_line: float | None = None) -> tuple[float, float | None, float | None]:
    home, away = poisson_pmf(home_rate), poisson_pmf(away_rate)
    home_cdf = _cdf(home)

    def home_above(threshold: float) -> float:
        # P(home > threshold) for real thresholds.
        floor = math.floor(threshold)
        if floor < 0:
            return 1.0
        return 1.0 - (home_cdf[floor] if floor < len(home_cdf) else 1.0)

    win = sum(probability * home_above(goals) for goals, probability in enumerate(away))
    tie = sum(home[goals] * away[goals] for goals in range(min(len(home), len(away))))
    # Regulation/extra-inning ties are settled later; split them evenly and let calibration learn bias.
    home_probability = win + tie / 2

    cover = None
    if home_line is not None:
        covers = sum(probability * home_above(goals - home_line) for goals, probability in enumerate(away))
        pushes = 0.0
        if float(home_line).is_integer():
            pushes = sum(probability * (home[int(goals - home_line)] if 0 <= goals - home_line < len(home) else 0.0)
                         for goals, probability in enumerate(away))
        cover = covers / (1 - pushes) if pushes < 1 else None

    over = None
    if total_line is not None:
        combined = poisson_pmf(home_rate + away_rate)
        cdf = _cdf(combined)
        floor = math.floor(total_line)
        above = 1.0 - (cdf[floor] if 0 <= floor < len(cdf) else (0.0 if floor < 0 else 1.0))
        push = combined[int(total_line)] if float(total_line).is_integer() and int(total_line) < len(combined) else 0.0
        over = above / (1 - push) if push < 1 else None
    return home_probability, cover, over


class Rls:
    """Recursive least squares with a ridge prior worth roughly 1/prior observations."""

    def __init__(self, size: int, prior: float = 0.05):
        self.weights = [0.0] * size
        self.matrix = [[prior if row == column else 0.0 for column in range(size)] for row in range(size)]
        self.updates = [0] * size

    def predict(self, features: list[float]) -> float:
        return sum(weight * value for weight, value in zip(self.weights, features))

    def contributions(self, features: list[float]) -> list[float]:
        return [weight * value for weight, value in zip(self.weights, features)]

    def update(self, features: list[float], target: float) -> None:
        if not any(features):
            return
        size = len(features)
        projected = [sum(self.matrix[row][column] * features[column] for column in range(size)) for row in range(size)]
        denominator = 1.0 + sum(features[index] * projected[index] for index in range(size))
        gain = [value / denominator for value in projected]
        error = target - self.predict(features)
        self.weights = [weight + step * error for weight, step in zip(self.weights, gain)]
        self.matrix = [[self.matrix[row][column] - gain[row] * projected[column] for column in range(size)]
                       for row in range(size)]
        for index, value in enumerate(features):
            if value:
                self.updates[index] += 1


@dataclass
class _Team:
    offense: float = 0.0
    defense: float = 0.0
    games: int = 0
    last_final: datetime | None = None
    last_start: datetime | None = None
    last_venue: Venue | None = None
    synced: datetime | None = None


def _eastern_day(value: datetime) -> date:
    return value.astimezone(EASTERN).date()


class LeagueModel:
    def __init__(self, sport: str, params: Params, context: Context):
        self.sport, self.params, self.context = sport, params, context
        self.poisson = sport in POISSON_SPORTS
        self.teams: dict[str, _Team] = {}
        self.mean: float | None = None
        self.home = 0.0
        self.league_games = 0
        self.margin_variance: float | None = None
        self.total_variance: float | None = None
        self.margin_model = Rls(len(MARGIN_FEATURES))
        self.total_model = Rls(len(TOTAL_FEATURES))
        self.calibration = [1.0, 0.0]

    def _team(self, game, home: bool) -> _Team:
        key = team_key(game.home_id, game.home) if home else team_key(game.away_id, game.away)
        return self.teams.setdefault(key, _Team())

    def _venue(self, game) -> Venue | None:
        return self.context.venues.get(game.home)

    def _rest(self, team: _Team, game) -> int | None:
        if team.last_start is None:
            return None
        days = (_eastern_day(game.start) - _eastern_day(team.last_start)).days
        return None if days > NEW_SEASON_GAP_DAYS else days

    def _travel(self, team: _Team, venue: Venue | None) -> float | None:
        if venue is None or team.last_venue is None:
            return None
        return distance_km(team.last_venue, venue)

    def _efficiency(self, team_id: str | None, day: date) -> float | None:
        rows = self.context.efficiency.get(str(team_id)) if team_id else None
        if not rows:
            return None
        prior = [row for row in rows if row.day < day][-10:]
        if len(prior) < MIN_TEAM_GAMES:
            return None
        offense_plays = sum(row.offense_plays for row in prior)
        defense_plays = sum(row.defense_plays for row in prior)
        if not offense_plays or not defense_plays:
            return None
        return sum(row.offense_epa for row in prior) / offense_plays - sum(row.defense_epa for row in prior) / defense_plays

    def features(self, game) -> dict:
        home, away = self._team(game, True), self._team(game, False)
        venue = self._venue(game)
        home_rest, away_rest = self._rest(home, game), self._rest(away, game)
        home_travel, away_travel = self._travel(home, venue), self._travel(away, venue)
        home_b2b = home_rest is not None and home_rest <= 1
        away_b2b = away_rest is not None and away_rest <= 1
        rest_value = lambda days: 4 if days is None else min(days, 4)
        epa = 0.0
        if self.sport == "nfl" and self.params.use_efficiency:
            day = _eastern_day(game.start)
            home_epa, away_epa = self._efficiency(game.home_id, day), self._efficiency(game.away_id, day)
            if home_epa is not None and away_epa is not None:
                epa = (home_epa - away_epa) * 10
        weather = self.context.weather.get(game.id) if venue is not None and venue.outdoor else None
        wind = cold = rain = heat = 0.0
        if weather is not None:
            wind = max(0.0, weather.wind_mph - 10) / 10
            cold = max(0.0, 45 - weather.temperature_f) / 20
            rain = min(weather.precipitation_mm, 10) / 5
            heat = (weather.temperature_f - 70) / 20 if self.sport == "baseball" else 0.0
        return {
            "margin": [float(away_b2b) - float(home_b2b), (rest_value(home_rest) - rest_value(away_rest)) / 3,
                       ((away_travel or 0.0) - (home_travel or 0.0)) / 1000, epa],
            "total": [float(home_b2b) + float(away_b2b), wind, cold, rain, heat],
            "rest": (home_rest, away_rest), "travel": (home_travel, away_travel), "weather": weather,
        }

    def _base(self, game) -> tuple[float, float]:
        home, away = self._team(game, True), self._team(game, False)
        assert self.mean is not None
        return (self.mean + self.home / 2 + home.offense + away.defense,
                self.mean - self.home / 2 + away.offense + home.defense)

    def ready(self, game) -> bool:
        return (self.league_games >= MIN_LEAGUE_GAMES and self._team(game, True).games >= MIN_TEAM_GAMES
                and self._team(game, False).games >= MIN_TEAM_GAMES and bool(self.margin_variance)
                and bool(self.total_variance))

    def _injuries(self, game) -> tuple[tuple[Injury, ...], float, float]:
        if self.sport not in FOOTBALL_SPORTS or game.status.upper() in FINAL_STATUSES:
            return (), 0.0, 0.0
        now = self.context.now
        if now is not None and (game.start - now).days > 8:
            return (), 0.0, 0.0
        home = tuple(self.context.injuries.get(str(game.home_id), ())) if game.home_id else ()
        away = tuple(self.context.injuries.get(str(game.away_id), ())) if game.away_id else ()
        return (home + away, min(INJURY_TEAM_CAP, sum(item.points for item in home)),
                min(INJURY_TEAM_CAP, sum(item.points for item in away)))

    def predict(self, game, features: dict) -> ModelProjection | None:
        if not self.ready(game):
            return None
        base_home, base_away = self._base(game)
        margin_parts = self.margin_model.contributions(features["margin"])
        total_parts = self.total_model.contributions(features["total"])
        margin_shift, total_shift = sum(margin_parts), sum(total_parts)
        injuries, home_injury, away_injury = self._injuries(game)
        floor = max(0.05, 0.05 * (self.mean or 1))
        home_score = max(floor, base_home + margin_shift / 2 + total_shift / 2 - home_injury)
        away_score = max(floor, base_away - margin_shift / 2 + total_shift / 2 - away_injury)
        assert self.margin_variance is not None and self.total_variance is not None
        margin_sd, total_sd = math.sqrt(self.margin_variance), math.sqrt(self.total_variance)
        market = self.context.markets.get(game.id)
        home_line = market.home_spread if market else None
        total_line = market.total if market else None
        if self.poisson:
            raw, cover, over = poisson_outcomes(home_score, away_score, home_line, total_line)
        else:
            margin = home_score - away_score
            raw = normal_cdf(margin / margin_sd)
            cover = normal_cdf((margin + home_line) / margin_sd) if home_line is not None else None
            over = 1 - normal_cdf((total_line - (home_score + away_score)) / total_sd) if total_line is not None else None
        probability = _sigmoid(self.calibration[0] * _logit(raw) + self.calibration[1])
        home, away = self._team(game, True), self._team(game, False)
        synced = [value for value in (home.synced, away.synced) if value is not None]
        edge = probability - market.home_probability if market and market.home_probability is not None else None
        return ModelProjection(
            home_score, away_score, probability, home.games, away.games, self.league_games,
            max(synced) if synced else game.synced,
            distribution="poisson" if self.poisson else "normal", margin_sd=margin_sd, total_sd=total_sd,
            margin_adjustments=tuple((name, value) for name, value in zip(MARGIN_FEATURES, margin_parts) if abs(value) >= 0.005),
            total_adjustments=tuple((name, value) for name, value in zip(TOTAL_FEATURES, total_parts) if abs(value) >= 0.005),
            rest_days=features["rest"], travel_km=features["travel"], weather=features["weather"],
            injuries=injuries, injury_points=(home_injury, away_injury), market=market, market_edge=edge,
            cover_probability=cover, over_probability=over,
        )

    def _rate(self, rate: float, count: int) -> float:
        return max(rate, 1 / (count + 1))

    def learn(self, game, features: dict, raw_probability: float | None) -> None:
        home_points, away_points = float(game.home_score), float(game.away_score)
        home, away = self._team(game, True), self._team(game, False)
        for team in (home, away):
            if team.last_final is not None and (game.start - team.last_final).days > NEW_SEASON_GAP_DAYS:
                team.offense *= 1 - self.params.season_regression
                team.defense *= 1 - self.params.season_regression
        if self.mean is None:
            self.mean = (home_points + away_points) / 2
        base_home, base_away = self._base(game)
        margin_shift = self.margin_model.predict(features["margin"])
        total_shift = self.total_model.predict(features["total"])
        expected_home = base_home + margin_shift / 2 + total_shift / 2
        expected_away = base_away - margin_shift / 2 + total_shift / 2
        home_error, away_error = home_points - expected_home, away_points - expected_away
        margin_error = (home_points - away_points) - (expected_home - expected_away)
        total_error = (home_points + away_points) - (expected_home + expected_away)

        if raw_probability is not None:
            outcome = 1.0 if home_points > away_points else 0.0 if away_points > home_points else 0.5
            logit = _logit(raw_probability)
            predicted = _sigmoid(self.calibration[0] * logit + self.calibration[1])
            step = self.params.calibration_rate
            self.calibration[0] = min(2.0, max(0.5, self.calibration[0] - step * (predicted - outcome) * logit))
            self.calibration[1] = min(1.0, max(-1.0, self.calibration[1] - step * (predicted - outcome)))

        home_rate = self._rate(self.params.learning_rate, home.games)
        away_rate = self._rate(self.params.learning_rate, away.games)
        home.offense += home_rate * home_error
        away.defense += away_rate * home_error
        away.offense += away_rate * away_error
        home.defense += home_rate * away_error
        self.home += self._rate(self.params.home_rate, self.league_games) * margin_error
        self.mean += self._rate(self.params.league_rate, self.league_games) * (home_error + away_error) / 2
        self.margin_model.update(features["margin"], (home_points - away_points) - (base_home - base_away))
        self.total_model.update(features["total"], (home_points + away_points) - (base_home + base_away))
        variance_rate = self._rate(self.params.variance_rate, self.league_games)
        self.margin_variance = (margin_error ** 2 if self.margin_variance is None
                                else self.margin_variance + variance_rate * (margin_error ** 2 - self.margin_variance))
        self.total_variance = (total_error ** 2 if self.total_variance is None
                               else self.total_variance + variance_rate * (total_error ** 2 - self.total_variance))
        self.league_games += 1
        for team in (home, away):
            team.games += 1
            team.last_final = game.start
            team.synced = max(filter(None, (team.synced, game.synced)))

    def schedule(self, game, venue: Venue | None) -> None:
        for team in (self._team(game, True), self._team(game, False)):
            team.last_start = game.start
            if venue is not None:
                team.last_venue = venue

    def raw_probability(self, projection: ModelProjection) -> float:
        a, b = self.calibration
        return _sigmoid((_logit(projection.home_probability) - b) / a)


def _scored(game) -> bool:
    return game.status.upper() in FINAL_STATUSES and game.home_score is not None and game.away_score is not None


def _eligible(game, venues: dict[str, Venue]) -> bool:
    if game.status.upper() in SKIPPED_STATUSES:
        return False
    # All-star and placeholder teams are absent from the pro venue tables.
    return not venues or (game.home in venues and game.away in venues)


def run_model(sport: str, games: list, context: Context | None = None, params: Params | None = None) -> tuple[dict[str, ModelProjection], LeagueModel]:
    context = context or Context()
    model = LeagueModel(sport, params or Params(), context)
    projections: dict[str, ModelProjection] = {}
    ordered = sorted((game for game in games if _eligible(game, context.venues)), key=lambda game: (game.start, game.id))
    for _, group in groupby(ordered, key=lambda game: game.start):
        batch = list(group)
        prepared = []
        for game in batch:
            features = model.features(game)
            projection = model.predict(game, features)
            if projection is not None:
                projections[game.id] = projection
            prepared.append((game, features, projection))
        for game, features, projection in prepared:
            if _scored(game):
                model.learn(game, features, model.raw_probability(projection) if projection else None)
            model.schedule(game, model._venue(game))
    return projections, model


def baseline_predictions(games: list, venues: dict[str, Venue] | None = None) -> dict[str, tuple[float, float, float]]:
    """The original score-history model, computed incrementally for backtests."""
    history: dict[str, deque] = {}
    margins: deque = deque(maxlen=1000)
    result: dict[str, tuple[float, float, float]] = {}
    ordered = sorted((game for game in games if _eligible(game, venues or {})), key=lambda game: (game.start, game.id))
    for _, group in groupby(ordered, key=lambda game: game.start):
        batch = list(group)
        for game in batch:
            home = history.get(team_key(game.home_id, game.home), ())
            away = history.get(team_key(game.away_id, game.away), ())
            if len(home) < MIN_TEAM_GAMES or len(away) < MIN_TEAM_GAMES or len(margins) < MIN_LEAGUE_GAMES:
                continue
            mean = sum(margins) / len(margins)
            deviation = math.sqrt(sum((value - mean) ** 2 for value in margins) / len(margins))
            if not deviation:
                continue
            home_score = (sum(row[0] for row in home) / len(home) + sum(row[1] for row in away) / len(away)) / 2
            away_score = (sum(row[0] for row in away) / len(away) + sum(row[1] for row in home) / len(home)) / 2
            result[game.id] = (home_score, away_score, normal_cdf((home_score - away_score) / deviation))
        for game in batch:
            if not _scored(game):
                continue
            home_points, away_points = float(game.home_score), float(game.away_score)
            history.setdefault(team_key(game.home_id, game.home), deque(maxlen=10)).appendleft((home_points, away_points))
            history.setdefault(team_key(game.away_id, game.away), deque(maxlen=10)).appendleft((away_points, home_points))
            margins.append(home_points - away_points)
    return result


def _metrics(rows: list[tuple[float, float, float, float, float]]) -> dict:
    # rows: probability, outcome, predicted margin, actual margin, predicted total - actual total
    if not rows:
        return {}
    count = len(rows)
    clipped = [(min(max(p, 1e-6), 1 - 1e-6), y) for p, y, *_ in rows]
    decided = [(p, y) for p, y, *_ in rows if y != 0.5]
    return {
        "brier": sum((p - y) ** 2 for p, y, *_ in rows) / count,
        "log_loss": -sum(y * math.log(p) + (1 - y) * math.log(1 - p) for p, y in clipped) / count,
        "accuracy": sum((p > 0.5) == (y == 1.0) for p, y in decided) / len(decided) if decided else None,
        "margin_mae": sum(abs(row[2] - row[3]) for row in rows) / count,
        "total_mae": sum(abs(row[4]) for row in rows) / count,
    }


CALIBRATION_BUCKETS = ((0.5, 0.55), (0.55, 0.6), (0.6, 0.65), (0.65, 0.7), (0.7, 0.8), (0.8, 1.0001))


def _calibration(rows: list[tuple[float, float, float, float, float]]) -> list[dict]:
    buckets = []
    for low, high in CALIBRATION_BUCKETS:
        members = [(max(p, 1 - p), y if p >= 0.5 else 1 - y) for p, y, *_ in rows if low <= max(p, 1 - p) < high]
        if members:
            buckets.append({
                "low": low, "high": min(high, 1.0), "games": len(members),
                "predicted": sum(p for p, _ in members) / len(members),
                "actual": sum(y for _, y in members) / len(members),
            })
    return buckets


def _outcome(game) -> float:
    home, away = float(game.home_score), float(game.away_score)
    return 1.0 if home > away else 0.0 if away > home else 0.5


PARAMETER_GRID = tuple(Params(learning_rate=rate, season_regression=regression)
                       for rate in (0.015, 0.03, 0.05, 0.08, 0.12, 0.18) for regression in (0.0, 0.15, 0.3, 0.5))
EFFICIENCY_MIN_GAIN = 0.005


def parameter_grid(sport: str) -> tuple[Params, ...]:
    if sport != "nfl":
        return PARAMETER_GRID
    # EPA is kept only when it improves the tuning-window log loss.
    return PARAMETER_GRID + tuple(replace(params, use_efficiency=False) for params in PARAMETER_GRID)


def backtest(sport: str, games: list, context: Context | None = None,
             grid: tuple[Params, ...] | None = None) -> dict:
    """Tune on the earlier half of comparable games and report the later (holdout) half."""
    grid = grid or parameter_grid(sport)
    context = replace(context or Context(), injuries={}, markets={})
    scored = {game.id: game for game in games if _scored(game)}
    baseline = baseline_predictions(games, context.venues)
    runs = [(params, run_model(sport, games, context, params)) for params in grid]
    comparable = sorted(
        (game for game_id, game in scored.items() if game_id in baseline and all(game_id in run[1][0] for run in runs)),
        key=lambda game: (game.start, game.id),
    )
    if len(comparable) < 40:
        return {"status": "insufficient", "games": len(comparable), "model_version": MODEL_VERSION}
    split = len(comparable) // 2
    tuning, holdout = comparable[:split], comparable[split:]

    def rows(projections: dict[str, ModelProjection], selected: list) -> list:
        return [(projections[game.id].home_probability, _outcome(game), projections[game.id].projected_margin,
                 float(game.home_score) - float(game.away_score),
                 projections[game.id].projected_total - float(game.home_score) - float(game.away_score)) for game in selected]

    scores = [(_metrics(rows(run[1][0], tuning))["log_loss"], index) for index, run in enumerate(runs)]
    best_index = min(scores)[1]
    if runs[best_index][0].use_efficiency and sport == "nfl":
        simpler = [(score, index) for score, index in scores if not runs[index][0].use_efficiency]
        if simpler and min(simpler)[0] - min(scores)[0] < EFFICIENCY_MIN_GAIN:
            best_index = min(simpler)[1]
    best_params, (best_projections, best_model) = runs[best_index]
    baseline_rows = [(baseline[game.id][2], _outcome(game), baseline[game.id][0] - baseline[game.id][1],
                      float(game.home_score) - float(game.away_score),
                      baseline[game.id][0] + baseline[game.id][1] - float(game.home_score) - float(game.away_score))
                     for game in holdout]
    model_rows = rows(best_projections, holdout)
    return {
        "status": "ok",
        "model_version": MODEL_VERSION,
        "games": len(holdout),
        "tuning_games": len(tuning),
        "window": {"start": holdout[0].start.isoformat(), "end": holdout[-1].start.isoformat()},
        "params": best_params.as_dict(),
        "model": _metrics(model_rows),
        "baseline": _metrics(baseline_rows),
        "calibration": _calibration(model_rows),
        "effects": {
            "home_advantage": best_model.home,
            "margin": {name: weight for name, weight, count in zip(MARGIN_FEATURES, best_model.margin_model.weights, best_model.margin_model.updates) if count},
            "total": {name: weight for name, weight, count in zip(TOTAL_FEATURES, best_model.total_model.weights, best_model.total_model.updates) if count},
        },
    }


def consensus_market(bookmakers: list, fetched_at: datetime) -> Market | None:
    """Median no-vig moneyline probability, main spread and main total across bookmakers."""
    probabilities, spreads, totals = [], [], []

    def decimal(value) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if number > 1 else None

    def even_pair(pairs: dict[float, dict[str, float]], first: str, second: str) -> float | None:
        best = None
        for line, prices in pairs.items():
            if first in prices and second in prices:
                share = (1 / prices[first]) / (1 / prices[first] + 1 / prices[second])
                if best is None or abs(share - 0.5) < best[0]:
                    best = (abs(share - 0.5), line)
        return best[1] if best else None

    for book in bookmakers or []:
        bets = {bet.get("name"): bet.get("values") or [] for bet in book.get("bets") or [] if isinstance(bet, dict)}
        moneyline = {str(item.get("value")): decimal(item.get("odd")) for item in bets.get("Home/Away", [])}
        if moneyline.get("Home") and moneyline.get("Away"):
            home, away = 1 / moneyline["Home"], 1 / moneyline["Away"]
            probabilities.append(home / (home + away))
        handicap: dict[float, dict[str, float]] = {}
        for item in bets.get("Asian Handicap", []):
            parts = str(item.get("value", "")).split()
            price = decimal(item.get("odd"))
            if len(parts) == 2 and parts[0] in {"Home", "Away"} and price:
                try:
                    handicap.setdefault(float(parts[1]), {})[parts[0]] = price
                except ValueError:
                    continue
        line = even_pair(handicap, "Home", "Away")
        if line is not None:
            spreads.append(line)
        totals_by_line: dict[float, dict[str, float]] = {}
        for item in bets.get("Over/Under", []):
            parts = str(item.get("value", "")).split()
            price = decimal(item.get("odd"))
            if len(parts) == 2 and parts[0] in {"Over", "Under"} and price:
                try:
                    totals_by_line.setdefault(float(parts[1]), {})[parts[0]] = price
                except ValueError:
                    continue
        line = even_pair(totals_by_line, "Over", "Under")
        if line is not None:
            totals.append(line)
    if not probabilities and not spreads and not totals:
        return None
    return Market(
        median(probabilities) if probabilities else None,
        median(spreads) if spreads else None,
        median(totals) if totals else None,
        max(len(probabilities), len(spreads), len(totals)),
        fetched_at,
    )
