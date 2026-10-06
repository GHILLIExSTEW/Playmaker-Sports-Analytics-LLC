import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")


def get_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def get_int(name: str, default: int | None = None) -> int | None:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def get_str(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    return value or default


DISCORD_TOKEN = get_str("DISCORD_TOKEN")
APPLICATION_ID = get_int("APPLICATION_ID")
GUILD_ID = get_int("GUILD_ID")
SUPABASE_URL = get_str("SUPABASE_URL")
SUPABASE_KEY = get_str("SUPABASE_KEY")
API_SPORTS_KEY = get_str("API_SPORTS_KEY")
API_SPORTS_BUDGET_ENABLED = get_bool("API_SPORTS_BUDGET_ENABLED", False)
MEMBER_STATS_REFRESH_ENABLED = get_bool("MEMBER_STATS_REFRESH_ENABLED", False)
LIVE_STATS_MOD_ROLE_IDS = {1328120848992960543, 1347741218158678097, 1328149760766640190}
API_SPORTS_BASE_URL = get_str("API_SPORTS_BASE_URL", "https://v1.american-football.api-sports.io")
OPENAI_API_KEY = get_str("OPENAI_API_KEY")
OPENAI_VISION_MODEL = get_str("OPENAI_VISION_MODEL", "gpt-5.4-mini")
OPENAI_VISION_MODELS_RAW = get_str("OPENAI_VISION_MODELS", "")
OFFICIAL_CHANNEL_ID = get_int("OFFICIAL_CHANNEL_ID")
IMAGE_INPUT_CHANNEL_ID = get_int("IMAGE_INPUT_CHANNEL_ID")
MEMBER_BET_CHANNEL_ID = get_int("MEMBER_BET_CHANNEL_ID")
PAID_MEMBER_ROLE_ID = get_int("PAID_MEMBER_ROLE_ID")
WHOP_API_KEY = get_str("WHOP_API_KEY")
WHOP_ACCOUNT_ID = get_str("WHOP_ACCOUNT_ID")
WHOP_PAID_PLAN_IDS = {
    value.strip() for value in os.getenv("WHOP_PAID_PLAN_IDS", "").split(",") if value.strip()
}
WHOP_TRIAL_PLAN_IDS = {
    value.strip() for value in os.getenv("WHOP_TRIAL_PLAN_IDS", "").split(",") if value.strip()
}
WHOP_MEMBERSHIP_SYNC_ENABLED = get_bool("WHOP_MEMBERSHIP_SYNC_ENABLED", False)
CONFIRMATION_CHANNEL_ID = get_int("CONFIRMATION_CHANNEL_ID")
TEST_CHANNEL_ID = get_int("TEST_CHANNEL_ID")
TESTING = get_bool("TESTING", False)
TEAM_STATS_CHANNEL_ID = get_int("TEAM_STATS_CHANNEL_ID")
RESULT_CHANNEL_ID = get_int("RESULT_CHANNEL_ID")
FREE_CHAT_CHANNEL_ID = get_int("FREE_CHAT_CHANNEL_ID") or 1556482816974389290
VIP_CHAT_CHANNEL_ID = get_int("VIP_CHAT_CHANNEL_ID") or 1328136977463378051
TRACKER_START_DATE = get_str("TRACKER_START_DATE")

OFFICIAL_ROLE_IDS = {
    int(role_id.strip())
    for role_id in (os.getenv("OFFICIAL_ROLE_IDS", "").split(","))
    if role_id.strip().isdigit()
}
WIN_REACTION = "✅"
LOSS_REACTION = "❌"
VOID_REACTION = "🅿️"
PARTIAL_REACTION = "🌓"
OPERATOR_ROLE_IDS = {
    int(role_id.strip())
    for role_id in (os.getenv("OPERATOR_ROLE_IDS", "").split(","))
    if role_id.strip().isdigit()
}

def parse_csv_values(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


OPENAI_VISION_MODELS = [item.strip() for item in OPENAI_VISION_MODELS_RAW.split(",") if item.strip()]
if not OPENAI_VISION_MODELS:
    OPENAI_VISION_MODELS = [OPENAI_VISION_MODEL]
