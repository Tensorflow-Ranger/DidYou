"""
DidYou Task Reminder Service - Entry Point.
Loads config, initializes DB, registers plugins, starts scheduler + Flask.
"""
from dotenv import load_dotenv
load_dotenv()

import logging
import sys

from config import load_config
from db import init_db
from plugins import PluginRegistry
from plugins.telegram_plugin import TelegramPlugin
from plugins.whatsapp_plugin import WhatsAppPlugin
from plugins.twilio_plugin import TwilioPlugin, TwilioCallPlugin
from scheduler import start_scheduler
from server import app

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def main():
    """Boot the system: config -> DB -> plugins -> scheduler -> Flask."""
    print("DidYou Task Reminder Service Starting...")

    # 1. Load config
    config = load_config()
    print(f"  Config loaded: enabled_plugins={config.ENABLED_PLUGINS}")
    print(f"  Escalation chain: {config.ESCALATION_CHAIN}")

    # 2. Initialize database
    init_db(config.DB_PATH)
    print(f"  Database initialized: {config.DB_PATH}")

    # 3. Register plugins
    registry = PluginRegistry()

    if config.TELEGRAM_BOT_TOKEN:
        registry.register(TelegramPlugin(config))

    if config.GREEN_API_ID and config.GREEN_API_TOKEN:
        registry.register(WhatsAppPlugin(config))

    if config.TWILIO_SID and config.TWILIO_AUTH_TOKEN:
        twilio = TwilioPlugin(config)
        registry.register(twilio)
        # Register call channel as separate plugin (same Twilio backend)
        registry.register(TwilioCallPlugin(twilio))

    # 4. Start plugins (each may spawn daemon threads)
    registry.start_all()
    active = registry.list_active()
    print(f"  Plugins active: {active}")

    if not active:
        logger.warning("No plugins active! Set TELEGRAM_BOT_TOKEN in .env")

    # 5. Start scheduler
    scheduler = start_scheduler(registry, config)
    print(f"  Scheduler running: {scheduler.running}")

    # 6. Start Flask (blocks)
    print("\nStarting Flask server on port 5000...")
    print("  Endpoints: /voice, /handle-input, /call-status, /amd-status, /health")
    app.run(host="0.0.0.0", port=5000)


if __name__ == "__main__":
    main()
