from dotenv import load_dotenv
load_dotenv()

import os
import sys
from scheduler import scheduler
from server import app
from db import add_allowed_number
from whatsapp_receiver import start_polling


def main():
    print("DidYou Task Reminder Service Starting...")
    print(f"Scheduler running: {scheduler.running}")

    # Seed allowed numbers from CLI args
    if len(sys.argv) > 1:
        for phone in sys.argv[1:]:
            if phone.startswith("+"):
                if add_allowed_number(phone):
                    print(f"Added {phone} to allowed numbers")
                else:
                    print(f"{phone} already in allowed numbers")

    # Start GREEN-API WhatsApp polling if configured
    green_api_id = os.getenv("GREEN_API_ID")
    green_api_token = os.getenv("GREEN_API_TOKEN")
    if green_api_id and green_api_token:
        start_polling()
        print("WhatsApp polling started (GREEN-API)")
    else:
        print("GREEN-API not configured — WhatsApp polling disabled")
        print("Set GREEN_API_ID and GREEN_API_TOKEN in .env to enable")

    print("\nStarting Flask server on port 5000...")
    app.run(host="0.0.0.0", port=5000)


if __name__ == "__main__":
    main()
