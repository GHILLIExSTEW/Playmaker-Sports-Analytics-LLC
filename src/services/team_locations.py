"""Home-venue coordinates and roof types for the pro leagues the models cover.

Used for travel distance and to decide whether outdoor weather applies. Coordinates are
venue-level approximations; retractable roofs are treated as indoor because they close
in bad weather.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Venue:
    latitude: float
    longitude: float
    roof: str  # "open", "retractable" or "dome"
    timezone: str

    @property
    def outdoor(self) -> bool:
        return self.roof == "open"


def _venues(rows: dict[str, tuple[float, float, str, str]]) -> dict[str, Venue]:
    return {name: Venue(*values) for name, values in rows.items()}


NY, CHI, DEN, LA, PHX = "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles", "America/Phoenix"
TOR, DET = "America/Toronto", "America/Detroit"

NFL = _venues({
    "Arizona Cardinals": (33.5276, -112.2626, "retractable", PHX),
    "Atlanta Falcons": (33.7554, -84.4008, "retractable", NY),
    "Baltimore Ravens": (39.2780, -76.6227, "open", NY),
    "Buffalo Bills": (42.7738, -78.7870, "open", NY),
    "Carolina Panthers": (35.2258, -80.8528, "open", NY),
    "Chicago Bears": (41.8623, -87.6167, "open", CHI),
    "Cincinnati Bengals": (39.0955, -84.5161, "open", NY),
    "Cleveland Browns": (41.5061, -81.6995, "open", NY),
    "Dallas Cowboys": (32.7473, -97.0945, "retractable", CHI),
    "Denver Broncos": (39.7439, -105.0201, "open", DEN),
    "Detroit Lions": (42.3400, -83.0456, "dome", DET),
    "Green Bay Packers": (44.5013, -88.0622, "open", CHI),
    "Houston Texans": (29.6847, -95.4107, "retractable", CHI),
    "Indianapolis Colts": (39.7601, -86.1639, "retractable", "America/Indiana/Indianapolis"),
    "Jacksonville Jaguars": (30.3239, -81.6373, "open", NY),
    "Kansas City Chiefs": (39.0489, -94.4839, "open", CHI),
    "Las Vegas Raiders": (36.0909, -115.1833, "dome", LA),
    "Los Angeles Chargers": (33.9535, -118.3392, "dome", LA),
    "Los Angeles Rams": (33.9535, -118.3392, "dome", LA),
    "Miami Dolphins": (25.9580, -80.2389, "open", NY),
    "Minnesota Vikings": (44.9737, -93.2581, "dome", CHI),
    "New England Patriots": (42.0909, -71.2643, "open", NY),
    "New Orleans Saints": (29.9511, -90.0812, "dome", CHI),
    "New York Giants": (40.8135, -74.0745, "open", NY),
    "New York Jets": (40.8135, -74.0745, "open", NY),
    "Philadelphia Eagles": (39.9008, -75.1675, "open", NY),
    "Pittsburgh Steelers": (40.4468, -80.0158, "open", NY),
    "San Francisco 49ers": (37.4030, -121.9700, "open", LA),
    "Seattle Seahawks": (47.5952, -122.3316, "open", LA),
    "Tampa Bay Buccaneers": (27.9759, -82.5033, "open", NY),
    "Tennessee Titans": (36.1665, -86.7713, "open", CHI),
    "Washington Commanders": (38.9077, -76.8645, "open", NY),
})

MLB = _venues({
    "Arizona Diamondbacks": (33.4455, -112.0667, "retractable", PHX),
    "Athletics": (38.5803, -121.5133, "open", LA),
    "Atlanta Braves": (33.8908, -84.4678, "open", NY),
    "Baltimore Orioles": (39.2840, -76.6217, "open", NY),
    "Boston Red Sox": (42.3467, -71.0972, "open", NY),
    "Chicago Cubs": (41.9484, -87.6553, "open", CHI),
    "Chicago White Sox": (41.8299, -87.6338, "open", CHI),
    "Cincinnati Reds": (39.0975, -84.5066, "open", NY),
    "Cleveland Guardians": (41.4962, -81.6852, "open", NY),
    "Colorado Rockies": (39.7559, -104.9942, "open", DEN),
    "Detroit Tigers": (42.3390, -83.0485, "open", DET),
    "Houston Astros": (29.7573, -95.3555, "retractable", CHI),
    "Kansas City Royals": (39.0517, -94.4803, "open", CHI),
    "Los Angeles Angels": (33.8003, -117.8827, "open", LA),
    "Los Angeles Dodgers": (34.0739, -118.2400, "open", LA),
    "Miami Marlins": (25.7781, -80.2196, "retractable", NY),
    "Milwaukee Brewers": (43.0280, -87.9712, "retractable", CHI),
    "Minnesota Twins": (44.9817, -93.2776, "open", CHI),
    "New York Mets": (40.7571, -73.8458, "open", NY),
    "New York Yankees": (40.8296, -73.9262, "open", NY),
    "Philadelphia Phillies": (39.9061, -75.1665, "open", NY),
    "Pittsburgh Pirates": (40.4469, -80.0057, "open", NY),
    "San Diego Padres": (32.7073, -117.1566, "open", LA),
    "San Francisco Giants": (37.7786, -122.3893, "open", LA),
    "Seattle Mariners": (47.5914, -122.3325, "retractable", LA),
    "St.Louis Cardinals": (38.6226, -90.1928, "open", CHI),
    "Tampa Bay Rays": (27.7682, -82.6534, "dome", NY),
    "Texas Rangers": (32.7473, -97.0847, "retractable", CHI),
    "Toronto Blue Jays": (43.6414, -79.3894, "retractable", TOR),
    "Washington Nationals": (38.8730, -77.0074, "open", NY),
})

NHL = _venues({
    "Anaheim Ducks": (33.8078, -117.8765, "dome", LA),
    "Boston Bruins": (42.3662, -71.0621, "dome", NY),
    "Buffalo Sabres": (42.8750, -78.8764, "dome", NY),
    "Calgary Flames": (51.0374, -114.0519, "dome", "America/Edmonton"),
    "Carolina Hurricanes": (35.8033, -78.7219, "dome", NY),
    "Chicago Blackhawks": (41.8807, -87.6742, "dome", CHI),
    "Colorado Avalanche": (39.7487, -105.0077, "dome", DEN),
    "Columbus Blue Jackets": (39.9693, -83.0061, "dome", NY),
    "Dallas Stars": (32.7905, -96.8103, "dome", CHI),
    "Detroit Red Wings": (42.3411, -83.0553, "dome", DET),
    "Edmonton Oilers": (53.5469, -113.4979, "dome", "America/Edmonton"),
    "Florida Panthers": (26.1584, -80.3256, "dome", NY),
    "Los Angeles Kings": (34.0430, -118.2673, "dome", LA),
    "Minnesota Wild": (44.9448, -93.1010, "dome", CHI),
    "Montreal Canadiens": (45.4961, -73.5693, "dome", TOR),
    "Nashville Predators": (36.1592, -86.7785, "dome", CHI),
    "New Jersey Devils": (40.7335, -74.1711, "dome", NY),
    "New York Islanders": (40.7226, -73.7257, "dome", NY),
    "New York Rangers": (40.7505, -73.9934, "dome", NY),
    "Ottawa Senators": (45.2969, -75.9271, "dome", TOR),
    "Philadelphia Flyers": (39.9012, -75.1720, "dome", NY),
    "Pittsburgh Penguins": (40.4394, -79.9892, "dome", NY),
    "San Jose Sharks": (37.3327, -121.9010, "dome", LA),
    "Seattle Kraken": (47.6221, -122.3540, "dome", LA),
    "St. Louis Blues": (38.6268, -90.2027, "dome", CHI),
    "Tampa Bay Lightning": (27.9427, -82.4518, "dome", NY),
    "Toronto Maple Leafs": (43.6435, -79.3791, "dome", TOR),
    "Utah Mammoth": (40.7683, -111.9011, "dome", DEN),
    "Vancouver Canucks": (49.2778, -123.1089, "dome", "America/Vancouver"),
    "Vegas Golden Knights": (36.1029, -115.1784, "dome", LA),
    "Washington Capitals": (38.8981, -77.0209, "dome", NY),
    "Winnipeg Jets": (49.8928, -97.1436, "dome", "America/Winnipeg"),
})

NBA = _venues({
    "Atlanta Hawks": (33.7573, -84.3963, "dome", NY),
    "Boston Celtics": (42.3662, -71.0621, "dome", NY),
    "Brooklyn Nets": (40.6826, -73.9754, "dome", NY),
    "Charlotte Hornets": (35.2251, -80.8392, "dome", NY),
    "Chicago Bulls": (41.8807, -87.6742, "dome", CHI),
    "Cleveland Cavaliers": (41.4965, -81.6882, "dome", NY),
    "Dallas Mavericks": (32.7905, -96.8103, "dome", CHI),
    "Denver Nuggets": (39.7487, -105.0077, "dome", DEN),
    "Detroit Pistons": (42.3411, -83.0553, "dome", DET),
    "Golden State Warriors": (37.7680, -122.3877, "dome", LA),
    "Houston Rockets": (29.7508, -95.3621, "dome", CHI),
    "Indiana Pacers": (39.7640, -86.1555, "dome", "America/Indiana/Indianapolis"),
    "Los Angeles Clippers": (33.9447, -118.3414, "dome", LA),
    "Los Angeles Lakers": (34.0430, -118.2673, "dome", LA),
    "Memphis Grizzlies": (35.1382, -90.0506, "dome", CHI),
    "Miami Heat": (25.7814, -80.1870, "dome", NY),
    "Milwaukee Bucks": (43.0451, -87.9172, "dome", CHI),
    "Minnesota Timberwolves": (44.9795, -93.2761, "dome", CHI),
    "New Orleans Pelicans": (29.9490, -90.0821, "dome", CHI),
    "New York Knicks": (40.7505, -73.9934, "dome", NY),
    "Oklahoma City Thunder": (35.4634, -97.5151, "dome", CHI),
    "Orlando Magic": (28.5392, -81.3839, "dome", NY),
    "Philadelphia 76ers": (39.9012, -75.1720, "dome", NY),
    "Phoenix Suns": (33.4457, -112.0712, "dome", PHX),
    "Portland Trail Blazers": (45.5316, -122.6668, "dome", LA),
    "Sacramento Kings": (38.5802, -121.4997, "dome", LA),
    "San Antonio Spurs": (29.4270, -98.4375, "dome", CHI),
    "Toronto Raptors": (43.6435, -79.3791, "dome", TOR),
    "Utah Jazz": (40.7683, -111.9011, "dome", DEN),
    "Washington Wizards": (38.8981, -77.0209, "dome", NY),
})

VENUES: dict[str, dict[str, Venue]] = {
    "nfl": NFL,
    "baseball:1": MLB,
    "hockey:57": NHL,
    "basketball:12": NBA,
}


def distance_km(first: Venue, second: Venue) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (first.latitude, first.longitude, second.latitude, second.longitude))
    value = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(value)))
