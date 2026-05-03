from dotenv import load_dotenv
load_dotenv()

import sys
from scheduler import scheduler
from server import app
from db import add_allowed_number


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

    print("\nStarting Flask server on port 5000...")
    app.run(host="0.0.0.0", port=5000)


if __name__ == "__main__":
    main()
