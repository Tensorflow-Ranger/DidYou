from dotenv import load_dotenv
load_dotenv()

# Import scheduler (starts automatically on import)
from scheduler import scheduler
from db import add_allowed_number
import time
import sys

def main():
    print("DidYou Task Reminder Service Starting...")
    print(f"Scheduler running: {scheduler.running}")
    
    # Add some seed allowed numbers if passed as arguments
    # Usage: python main.py +15551234567 +15559876543
    if len(sys.argv) > 1:
        for phone in sys.argv[1:]:
            if phone.startswith("+"):
                if add_allowed_number(phone):
                    print(f"Added {phone} to allowed numbers")
                else:
                    print(f"{phone} already in allowed numbers")
    
    print("\nTo add allowed numbers, run:")
    print("  python main.py +15551234567")
    print("\nOr use Python:")
    print("  from db import add_allowed_number")
    print("  add_allowed_number('+15551234567', name='John')")
    
    print("\nService running. Press Ctrl+C to stop.")
    
    try:
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        print("\nShutting down...")
        scheduler.shutdown()

if __name__ == "__main__":
    main()
