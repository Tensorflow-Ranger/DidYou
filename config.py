"""
Application configuration - loads from .env and provides typed Config object.
"""
import os
from dataclasses import dataclass, field
from typing import List, Optional

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    """Typed configuration object with all environment variables."""

    # pf:ensures:config.loads_all_env_vars Returns config with all env vars
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    GREEN_API_ID: Optional[str] = None
    GREEN_API_TOKEN: Optional[str] = None
    GREEN_API_URL: str = "https://api.greenapi.com"
    TWILIO_SID: Optional[str] = None
    TWILIO_AUTH_TOKEN: Optional[str] = None
    TWILIO_PHONE: Optional[str] = None
    BASE_URL: Optional[str] = None
    DB_PATH: str = "tasks.db"
    ESCALATION_CHAIN: List[str] = field(default_factory=lambda: ["sms", "telegram", "call"])
    MAX_DAILY_CALLS: int = 5
    MAX_ATTEMPTS: int = 20
    DEFAULT_TIMEZONE: str = "Asia/Kolkata"
    ENABLED_PLUGINS: List[str] = field(default_factory=list)


# pf:never:config.never_crash_on_missing NEVER crash on missing optional env vars
# pf:ensures:config.escalation_default ESCALATION_CHAIN defaults to ['sms', 'telegram', 'call']
# pf:ensures:config.enabled_plugins ENABLED_PLUGINS derived from env var presence
def load_config() -> Config:
    """
    Load configuration from environment variables.
    Uses os.getenv() with defaults for all optional fields - never raises on missing vars.
    """
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN") or None
    green_api_id = os.getenv("GREEN_API_ID") or None
    green_api_token = os.getenv("GREEN_API_TOKEN") or None
    twilio_sid = os.getenv("TWILIO_SID") or None
    twilio_auth_token = os.getenv("TWILIO_AUTH_TOKEN") or None
    twilio_phone = os.getenv("TWILIO_PHONE") or None

    # Derive enabled plugins from env var presence
    enabled_plugins: List[str] = []
    if telegram_token:
        enabled_plugins.append("telegram")
    if green_api_id and green_api_token:
        enabled_plugins.append("whatsapp")
    if twilio_sid and twilio_auth_token:
        enabled_plugins.append("twilio")

    # Parse escalation chain from env or use default
    escalation_env = os.getenv("ESCALATION_CHAIN")
    if escalation_env:
        escalation_chain = [s.strip() for s in escalation_env.split(",") if s.strip()]
    else:
        escalation_chain = ["sms", "telegram", "call"]

    return Config(
        TELEGRAM_BOT_TOKEN=telegram_token,
        GREEN_API_ID=green_api_id,
        GREEN_API_TOKEN=green_api_token,
        GREEN_API_URL=os.getenv("GREEN_API_URL", "https://api.greenapi.com"),
        TWILIO_SID=twilio_sid,
        TWILIO_AUTH_TOKEN=twilio_auth_token,
        TWILIO_PHONE=twilio_phone,
        BASE_URL=os.getenv("BASE_URL") or None,
        DB_PATH=os.getenv("DB_PATH", "tasks.db"),
        ESCALATION_CHAIN=escalation_chain,
        MAX_DAILY_CALLS=int(os.getenv("MAX_DAILY_CALLS", "5")),
        MAX_ATTEMPTS=int(os.getenv("MAX_ATTEMPTS", "20")),
        DEFAULT_TIMEZONE=os.getenv("DEFAULT_TIMEZONE", "Asia/Kolkata"),
        ENABLED_PLUGINS=enabled_plugins,
    )
