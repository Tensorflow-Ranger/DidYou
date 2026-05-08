"""
Database layer - SQLite with user_id-based schema.
Fresh schema: users table with user_id TEXT primary key, optional phone.
"""
import sqlite3
import logging
import calendar
from datetime import datetime, timedelta
from typing import Optional, List
from zoneinfo import ZoneInfo

from config import load_config

logger = logging.getLogger(__name__)

_config = load_config()
DB_PATH = _config.DB_PATH
IST = ZoneInfo(_config.DEFAULT_TIMEZONE)

# Module-level connection (lazy init via init_db)
_conn: Optional[sqlite3.Connection] = None


def _get_conn() -> sqlite3.Connection:
    """Get the module-level connection, initializing if needed."""
    global _conn
    if _conn is None:
        init_db(DB_PATH)
    assert _conn is not None
    return _conn


# pf:ensures:db.creates_users_table Creates users table with user_id TEXT PK
# pf:ensures:db.creates_tasks_table Creates tasks table with user_id FK
# pf:ensures:db.creates_call_log_table Creates call_log table
def init_db(db_path: str) -> None:
    """Create tables if not exist, set pragmas."""
    global _conn, DB_PATH
    DB_PATH = db_path

    _conn = sqlite3.connect(db_path, check_same_thread=False)
    _conn.row_factory = sqlite3.Row

    cursor = _conn.cursor()

    # Pragmas for performance
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")

    # Users table (replaces allowed_numbers)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id TEXT PRIMARY KEY,
        name TEXT,
        phone TEXT,
        platform TEXT,
        timezone TEXT DEFAULT 'Asia/Kolkata',
        quiet_start TEXT DEFAULT '22:00',
        quiet_end TEXT DEFAULT '08:00',
        max_daily_calls INTEGER DEFAULT 5,
        created_at TEXT DEFAULT (datetime('now'))
    )
    """)

    # Tasks table (user_id replaces phone)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        message TEXT NOT NULL,
        user_id TEXT NOT NULL,
        time TEXT NOT NULL,
        status TEXT DEFAULT 'pending',
        attempts INTEGER DEFAULT 0,
        daily_calls INTEGER DEFAULT 0,
        last_call_date TEXT,
        is_recurring INTEGER DEFAULT 0,
        recurrence_type TEXT,
        recurrence_time TEXT,
        recurrence_day INTEGER,
        streak INTEGER DEFAULT 0,
        is_allday INTEGER DEFAULT 0,
        escalation_stage TEXT DEFAULT 'sms',
        created_at TEXT DEFAULT (datetime('now')),
        UNIQUE(message, user_id),
        FOREIGN KEY (user_id) REFERENCES users(user_id)
    )
    """)

    # Call log
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS call_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id INTEGER,
        user_id TEXT,
        channel TEXT NOT NULL,
        status TEXT,
        external_id TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (task_id) REFERENCES tasks(id)
    )
    """)

    _conn.commit()


# pf:ensures:db.ensure_user_upsert Only inserts if new, never overwrites
# pf:never:db.never_overwrite_user NEVER overwrite existing user data on re-registration
def ensure_user(user_id: str, name: str, platform: str) -> bool:
    """
    Ensure user exists. INSERT OR IGNORE - never overwrites existing data.
    Returns True if created (new user), False if already exists.
    """
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO users (user_id, name, platform) VALUES (?, ?, ?)",
        (user_id, name, platform)
    )
    conn.commit()
    return cursor.rowcount > 0


def get_user(user_id: str) -> Optional[sqlite3.Row]:
    """Get user record by user_id."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    return cursor.fetchone()


# pf:never:db.never_log_phone_plaintext NEVER log phone numbers at DEBUG level
def update_user_phone(user_id: str, phone: str) -> bool:
    """Update phone for user. Returns True if updated."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET phone=? WHERE user_id=?",
        (phone, user_id)
    )
    conn.commit()
    updated = cursor.rowcount > 0
    if updated:
        logger.info(f"Updated phone for user {user_id} to ***{phone[-4:]}")
    return updated


# pf:ensures:db.add_task_returns_id_or_none Returns task_id or None on duplicate
# pf:never:db.never_raise_on_duplicate NEVER raise exception on duplicate task
def add_task(
    message: str,
    user_id: str,
    time: str,
    is_recurring: bool = False,
    recurrence_type: Optional[str] = None,
    recurrence_time: Optional[str] = None,
    recurrence_day: Optional[int] = None,
    is_allday: bool = False,
) -> Optional[int]:
    """
    Add a new task. Returns task ID or None if duplicate (UNIQUE constraint).
    Never raises on duplicate - catches IntegrityError.
    """
    conn = _get_conn()
    cursor = conn.cursor()
    escalation_stage = 'sms' if is_allday else None
    try:
        cursor.execute(
            """INSERT INTO tasks
               (message, user_id, time, status, attempts, is_recurring,
                recurrence_type, recurrence_time, recurrence_day, streak,
                is_allday, escalation_stage)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (message, user_id, time, "pending", 0,
             1 if is_recurring else 0, recurrence_type, recurrence_time,
             recurrence_day, 0, 1 if is_allday else 0, escalation_stage)
        )
        conn.commit()
        return cursor.lastrowid
    except sqlite3.IntegrityError:
        return None


def get_task(task_id: int) -> Optional[sqlite3.Row]:
    """Get a single task by ID."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tasks WHERE id=?", (task_id,))
    return cursor.fetchone()


def get_pending_tasks() -> List[sqlite3.Row]:
    """Get all pending tasks."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tasks WHERE status='pending'")
    return cursor.fetchall()


def get_user_tasks(user_id: str) -> List[sqlite3.Row]:
    """Get pending tasks for one user."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM tasks WHERE user_id=? AND status='pending' ORDER BY time",
        (user_id,)
    )
    return cursor.fetchall()


def clear_task(task_id: int, user_id: str) -> bool:
    """
    Cancel a task by ID (ownership check).
    Returns True if cleared, False if not found or not owned.
    """
    conn = _get_conn()
    cursor = conn.cursor()
    task = get_task(task_id)
    if task is None or task["user_id"] != user_id:
        return False
    cursor.execute("UPDATE tasks SET status='cancelled' WHERE id=?", (task_id,))
    conn.commit()
    return True


def clear_all_tasks(user_id: str) -> int:
    """Cancel all pending tasks for a user. Returns count cleared."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM tasks WHERE user_id=? AND status='pending'",
        (user_id,)
    )
    count = cursor.fetchone()[0]
    cursor.execute(
        "UPDATE tasks SET status='cancelled' WHERE user_id=? AND status='pending'",
        (user_id,)
    )
    conn.commit()
    return count


def complete_task(task_id: int):
    """
    Complete a task. For recurring tasks, reschedules to next occurrence.
    For one-time tasks, marks as done.
    Returns: (is_recurring, next_time_str or None, new_streak or None)
    """
    conn = _get_conn()
    cursor = conn.cursor()
    task = get_task(task_id)
    if task is None:
        return (False, None, None)

    is_recurring = task["is_recurring"]
    is_allday = task["is_allday"]

    if not is_recurring:
        cursor.execute("UPDATE tasks SET status='done' WHERE id=?", (task_id,))
        conn.commit()
        return (False, None, None)

    # Recurring task: increment streak and reschedule
    recurrence_type = task["recurrence_type"]
    recurrence_time_str = task["recurrence_time"] or "09:00"
    recurrence_day = task["recurrence_day"]
    current_streak = (task["streak"] or 0) + 1

    now = datetime.now(IST)

    if is_allday:
        hour, minute = 8, 0
    else:
        hour, minute = map(int, recurrence_time_str.split(":"))

    if recurrence_type == "daily":
        next_date = now.date() + timedelta(days=1)
    elif recurrence_type == "weekdays":
        next_date = now.date() + timedelta(days=1)
        while next_date.weekday() >= 5:
            next_date += timedelta(days=1)
    elif recurrence_type == "weekly":
        next_date = now.date() + timedelta(days=7)
    elif recurrence_type == "monthly":
        year = now.year
        month = now.month + 1
        if month > 12:
            month = 1
            year += 1
        day = recurrence_day or now.day
        last_day = calendar.monthrange(year, month)[1]
        day = min(day, last_day)
        next_date = datetime(year, month, day).date()
    else:
        next_date = now.date() + timedelta(days=1)

    next_time = datetime(
        next_date.year, next_date.month, next_date.day,
        hour, minute, 0, tzinfo=IST
    )

    escalation_stage = 'sms' if is_allday else None

    cursor.execute(
        """UPDATE tasks
           SET time=?, status='pending', attempts=0, daily_calls=0, streak=?, escalation_stage=?
           WHERE id=?""",
        (next_time.isoformat(), current_streak, escalation_stage, task_id)
    )
    conn.commit()

    return (True, next_time.strftime("%b %d at %I:%M %p"), current_streak)


def advance_escalation(task_id: int) -> Optional[str]:
    """
    Advance to next escalation stage based on config chain.
    Returns the new stage, or None if already at last.
    """
    conn = _get_conn()
    cursor = conn.cursor()
    task = get_task(task_id)
    if task is None:
        return None

    current = task["escalation_stage"]
    chain = _config.ESCALATION_CHAIN

    if current not in chain:
        current = chain[0]

    current_idx = chain.index(current)
    if current_idx >= len(chain) - 1:
        return chain[-1]  # Already at final stage

    new_stage = chain[current_idx + 1]
    cursor.execute("UPDATE tasks SET escalation_stage=? WHERE id=?", (new_stage, task_id))
    conn.commit()
    return new_stage


def update_task(task_id: int, status: str) -> None:
    """Update task status."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("UPDATE tasks SET status=? WHERE id=?", (status, task_id))
    conn.commit()


def update_task_time(task_id: int, new_time) -> None:
    """Update the next reminder time for a task."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE tasks SET time=? WHERE id=?",
        (new_time.isoformat(), task_id)
    )
    conn.commit()


def increment_task_attempts(task_id: int) -> int:
    """Increment attempt counter and daily call counter. Returns new attempt count."""
    conn = _get_conn()
    cursor = conn.cursor()
    today = datetime.now(IST).strftime("%Y-%m-%d")

    task = get_task(task_id)
    if task is None:
        return 0

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


def get_task_daily_calls(task_id: int) -> int:
    """Get the number of calls made today for a task."""
    today = datetime.now(IST).strftime("%Y-%m-%d")
    task = get_task(task_id)
    if task is None:
        return 0
    if task["last_call_date"] != today:
        return 0
    return task["daily_calls"] or 0


def log_call(task_id: int, user_id: str, channel: str, status: Optional[str] = None, external_id: Optional[str] = None) -> int:
    """Log a call/message attempt for auditing."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO call_log (task_id, user_id, channel, status, external_id) VALUES (?, ?, ?, ?, ?)",
        (task_id, user_id, channel, status, external_id)
    )
    conn.commit()
    return cursor.lastrowid or 0


def get_calls_today(user_id: str) -> int:
    """Get number of calls made to a user today."""
    conn = _get_conn()
    cursor = conn.cursor()
    today = datetime.now(IST).strftime("%Y-%m-%d")
    cursor.execute(
        "SELECT COUNT(*) FROM call_log WHERE user_id=? AND channel='call' AND created_at LIKE ?",
        (user_id, f"{today}%")
    )
    result = cursor.fetchone()
    return result[0] if result else 0


def get_allday_tasks_for_escalation() -> List[sqlite3.Row]:
    """Get all-day tasks that are pending and due today."""
    conn = _get_conn()
    cursor = conn.cursor()
    today = datetime.now(IST).strftime("%Y-%m-%d")
    cursor.execute(
        """SELECT * FROM tasks
           WHERE status='pending' AND is_allday=1 AND time LIKE ?""",
        (f"{today}%",)
    )
    return cursor.fetchall()
