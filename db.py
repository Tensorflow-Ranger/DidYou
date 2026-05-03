import sqlite3
import os
from datetime import datetime

DB_PATH = os.getenv("DB_PATH", "tasks.db")

conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# Enable WAL mode for better concurrency
cursor.execute("PRAGMA journal_mode=WAL")
cursor.execute("PRAGMA busy_timeout=5000")

# Tasks table with new columns for smart escalation
cursor.execute("""
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message TEXT NOT NULL,
    phone TEXT NOT NULL,
    time TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    attempts INTEGER DEFAULT 0,
    daily_calls INTEGER DEFAULT 0,
    last_call_date TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(message, phone)
)
""")

# Add new columns if they don't exist (migration for existing DBs)
try:
    cursor.execute("ALTER TABLE tasks ADD COLUMN attempts INTEGER DEFAULT 0")
except sqlite3.OperationalError:
    pass

try:
    cursor.execute("ALTER TABLE tasks ADD COLUMN daily_calls INTEGER DEFAULT 0")
except sqlite3.OperationalError:
    pass

try:
    cursor.execute("ALTER TABLE tasks ADD COLUMN last_call_date TEXT")
except sqlite3.OperationalError:
    pass

try:
    cursor.execute("ALTER TABLE tasks ADD COLUMN created_at TEXT DEFAULT (datetime('now'))")
except sqlite3.OperationalError:
    pass

# Allowed phone numbers for WhatsApp task creation
cursor.execute("""
CREATE TABLE IF NOT EXISTS allowed_numbers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT UNIQUE NOT NULL,
    name TEXT,
    timezone TEXT DEFAULT 'Asia/Kolkata',
    quiet_start TEXT DEFAULT '22:00',
    quiet_end TEXT DEFAULT '08:00',
    max_daily_calls INTEGER DEFAULT 5,
    created_at TEXT DEFAULT (datetime('now'))
)
""")

# Call log for auditing and rate limiting
cursor.execute("""
CREATE TABLE IF NOT EXISTS call_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER,
    phone TEXT NOT NULL,
    channel TEXT NOT NULL,
    status TEXT,
    twilio_sid TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (task_id) REFERENCES tasks(id)
)
""")

conn.commit()


def add_task(message, phone, time):
    """Add a new task. Returns task ID or None if duplicate."""
    try:
        cursor.execute(
            "INSERT INTO tasks (message, phone, time, status, attempts) VALUES (?, ?, ?, ?, ?)",
            (message, phone, time, "pending", 0)
        )
        conn.commit()
        return cursor.lastrowid
    except sqlite3.IntegrityError:
        return None


def get_task(task_id):
    """Get a single task by ID."""
    cursor.execute("SELECT * FROM tasks WHERE id=?", (task_id,))
    return cursor.fetchone()


def get_pending_tasks():
    """Get all pending tasks."""
    cursor.execute("SELECT * FROM tasks WHERE status='pending'")
    return cursor.fetchall()


def update_task(task_id, status):
    """Update task status."""
    cursor.execute("UPDATE tasks SET status=? WHERE id=?", (status, task_id))
    conn.commit()


def update_task_time(task_id, new_time):
    """Update the next reminder time for a task."""
    cursor.execute(
        "UPDATE tasks SET time=? WHERE id=?",
        (new_time.isoformat(), task_id)
    )
    conn.commit()


def increment_task_attempts(task_id):
    """Increment attempt counter and daily call counter. Returns new attempt count."""
    today = datetime.now().strftime("%Y-%m-%d")
    
    task = get_task(task_id)
    if task is None:
        return 0
    
    # Reset daily counter if it's a new day
    if task["last_call_date"] != today:
        cursor.execute(
            "UPDATE tasks SET attempts=attempts+1, daily_calls=1, last_call_date=? WHERE id=?",
            (today, task_id)
        )
    else:
        cursor.execute(
            "UPDATE tasks SET attempts=attempts+1, daily_calls=daily_calls+1 WHERE id=?",
            (task_id,)
        )
    conn.commit()
    
    task = get_task(task_id)
    return task["attempts"] if task else 0


def get_task_daily_calls(task_id):
    """Get the number of calls made today for a task."""
    today = datetime.now().strftime("%Y-%m-%d")
    task = get_task(task_id)
    if task is None:
        return 0
    if task["last_call_date"] != today:
        return 0
    return task["daily_calls"] or 0


def reset_task_attempts(task_id):
    """Reset attempt counter (e.g., when user snoozes)."""
    cursor.execute("UPDATE tasks SET attempts=0 WHERE id=?", (task_id,))
    conn.commit()


# Allowed numbers management

def is_number_allowed(phone):
    """Check if a phone number is in the allowlist."""
    # Normalize phone number (remove whatsapp: prefix if present)
    phone = phone.replace("whatsapp:", "").strip()
    cursor.execute("SELECT * FROM allowed_numbers WHERE phone=?", (phone,))
    return cursor.fetchone() is not None


def get_allowed_number(phone):
    """Get allowed number record with preferences."""
    phone = phone.replace("whatsapp:", "").strip()
    cursor.execute("SELECT * FROM allowed_numbers WHERE phone=?", (phone,))
    return cursor.fetchone()


def add_allowed_number(phone, name=None, timezone="Asia/Kolkata", quiet_start="22:00", quiet_end="08:00", max_daily_calls=5):
    """Add a phone number to the allowlist."""
    phone = phone.replace("whatsapp:", "").strip()
    try:
        cursor.execute(
            "INSERT INTO allowed_numbers (phone, name, timezone, quiet_start, quiet_end, max_daily_calls) VALUES (?, ?, ?, ?, ?, ?)",
            (phone, name, timezone, quiet_start, quiet_end, max_daily_calls)
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


# Call logging

def log_call(task_id, phone, channel, status=None, twilio_sid=None):
    """Log a call/message attempt for auditing."""
    cursor.execute(
        "INSERT INTO call_log (task_id, phone, channel, status, twilio_sid) VALUES (?, ?, ?, ?, ?)",
        (task_id, phone, channel, status, twilio_sid)
    )
    conn.commit()
    return cursor.lastrowid


def get_calls_today(phone):
    """Get number of calls made to a phone number today."""
    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute(
        "SELECT COUNT(*) FROM call_log WHERE phone=? AND channel='call' AND created_at LIKE ?",
        (phone, f"{today}%")
    )
    result = cursor.fetchone()
    return result[0] if result else 0
