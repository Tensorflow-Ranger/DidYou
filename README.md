# DidYou

A personal accountability tool that calls you repeatedly until you confirm you've completed your task. Built for my family to help us actually get things done instead of endlessly snoozing reminders.

## Why?

Regular reminders are easy to ignore. A notification pops up, you swipe it away, and forget about it. But a phone call? That's harder to ignore. And if you don't pick up or say you haven't done it yet, it calls back. And keeps calling. With expanding intervals. Until you do the thing.

## Features

- **Persistent phone calls** — Calls you repeatedly with expanding intervals (1min → 5min → 15min → 30min → 1hr → 2hr → 4hr)
- **Voice + Speech recognition** — Say "done" or press 1 to mark complete
- **WhatsApp task creation** — Text your tasks in natural language
- **Recurring tasks** — Daily, weekdays, weekly, monthly with streak tracking
- **All-day tasks** — No specific time? Get SMS at 8am, WhatsApp at noon, calls at 6pm
- **Quiet hours** — No calls between 10pm-8am (configurable per user)
- **Daily call cap** — Max 5 calls per day per task (configurable)
- **IST timezone** — All times in Indian Standard Time

## Setup

### Requirements

- Python 3.9+
- Twilio account with:
  - Phone number (for calls/SMS)
  - WhatsApp sender (sandbox or approved number)
- Public URL for webhooks (ngrok, EC2 with Elastic IP, etc.)

### Installation

```bash
git clone <repo>
cd DidYou
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your Twilio credentials
```

### Environment Variables

```bash
TWILIO_SID=ACxxxxx              # Account SID (starts with AC)
TWILIO_AUTH_TOKEN=xxxxx         # 32-char auth token
TWILIO_PHONE=+15551234567       # Your Twilio number (E.164)
TWILIO_WHATSAPP=whatsapp:+15551234567  # Optional, defaults to TWILIO_PHONE
BASE_URL=http://your-domain:5000       # Your public URL (HTTP works for voice)
```

### Twilio Webhook Configuration

Set these webhooks in your Twilio console:

| Channel | Webhook URL |
|---------|-------------|
| Voice | `http://your-domain:5000/voice` |
| WhatsApp | `http://your-domain:5000/whatsapp` |

### Run (Development)

```bash
source venv/bin/activate
python main.py
```

This starts both the Flask server (port 5000) and the APScheduler.

### Run (Production with systemd)

Create a systemd service for auto-start on boot:

```bash
sudo nano /etc/systemd/system/didyou.service
```

Paste this (adjust paths if needed):

```ini
[Unit]
Description=DidYou Reminder Service
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/DidYou
ExecStart=/home/ubuntu/DidYou/venv/bin/python main.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable didyou    # Auto-start on boot
sudo systemctl start didyou
```

**Useful commands:**
```bash
sudo systemctl status didyou      # Check status
sudo systemctl restart didyou     # Restart after code changes
sudo journalctl -u didyou -f      # View logs (live)
sudo journalctl -u didyou -n 50   # Last 50 log lines
```

## Usage

### CLI — Add Tasks

```bash
# One-time task
python add_tasks.py task "Drink water" +919876543210 "2026-05-03T17:00:00"

# Recurring tasks
python add_tasks.py daily "Exercise" +919876543210 "07:00"
python add_tasks.py weekdays "Standup" +919876543210 "10:00"
python add_tasks.py weekly "Weekly review" +919876543210 "18:00"
python add_tasks.py monthly "Pay rent" +919876543210 "10:00"
python add_tasks.py monthly "Pay rent" +919876543210 "10:00" 1  # 1st of month

# All-day task (escalates through SMS -> WhatsApp -> Call)
python add_tasks.py allday "Submit report" +919876543210

# Manage allowed numbers (for WhatsApp)
python add_tasks.py allow +919876543210 "Mom"

# View tasks
python add_tasks.py list
python add_tasks.py allowed
```

### WhatsApp — Natural Language

Send messages to your Twilio WhatsApp number:

```
remind me to drink water at 3pm
call mom tomorrow at 9am
buy groceries in 30 minutes
exercise daily at 7am
standup weekdays at 10am
review weekly at 6pm
pay rent monthly at 10am
submit report              # all-day task (no time = escalation)
```

**Commands:**
- `help` — Show usage examples
- `list` — Show your pending tasks with IDs
- `clear 123` — Clear a specific task by ID
- `clear all` — Clear all your pending tasks
- `DONE` — Mark task complete (in response to reminder)

### Query Parsing Rules

**Recurring tasks** — keyword order is flexible:
```
exercise daily at 7am       ✓
daily exercise at 7am       ✓
remind me daily at 7am to exercise  ✓
```
Keywords: `daily`, `every day`, `weekdays`, `weekly`, `every week`, `monthly`, `every month`

**Recurring all-day tasks** — frequency keyword without time:
```
pay rent monthly on 1st        ✓ (escalates on the 1st each month)
exercise daily                 ✓ (escalates every day)
clean weekly                   ✓ (escalates every week)
```

**One-time tasks** — task must come before time:
```
call mom at 3pm             ✓
call mom tomorrow           ✓
at 3pm call mom             ✗ (won't parse)
```

**All-day tasks** — avoid time words:
```
submit report               ✓ (all-day)
submit report tomorrow      ✗ (parsed as timed task)
```

## How It Works

### Call Flow

1. Scheduler checks tasks every 30 seconds
2. If task is due → make call with TwiML voice prompt
3. User says "done" / presses 1 → task marked complete
4. User says "snooze" / presses 2 → retry with reset intervals
5. No answer → retry with expanding interval
6. Repeat until done or max attempts (20)

### All-Day Escalation

For tasks with no specific time:

| Time | Channel |
|------|---------|
| 8 AM | SMS |
| 12 PM | WhatsApp |
| 6 PM | Phone calls (with expanding intervals) |

### Recurring Tasks

When you complete a recurring task:
1. Streak counter increments
2. Task reschedules to next occurrence
3. Voice prompt tells you your current streak

## Configuration

Per-user settings in `allowed_numbers` table:
- `quiet_start` / `quiet_end` — No calls during these hours (default: 22:00-08:00)
- `max_daily_calls` — Cap per task per day (default: 5)
- `timezone` — User's timezone (default: Asia/Kolkata)

## Files

```
main.py          # Entry point, starts Flask server + scheduler
server.py        # Flask app with Twilio webhooks
scheduler.py     # APScheduler job that checks tasks
caller.py        # Twilio API calls (voice, SMS, WhatsApp)
db.py            # SQLite schema and queries
add_tasks.py     # CLI for task management
tasks.db         # SQLite database (created on first run)
```

## License

MIT
