#!/usr/bin/env python3
"""
Helper script to add tasks and manage users.

Usage:
    # Add a one-time task
    python add_tasks.py task "Drink water" <user_id> "2026-05-03T17:00:00"

    # Add a recurring daily task (time in HH:MM IST)
    python add_tasks.py daily "Exercise" <user_id> "07:00"

    # Add a recurring weekdays task (Mon-Fri)
    python add_tasks.py weekdays "Standup meeting" <user_id> "10:00"

    # Add a recurring weekly task
    python add_tasks.py weekly "Weekly review" <user_id> "18:00"

    # Add a recurring monthly task (day of month optional)
    python add_tasks.py monthly "Pay rent" <user_id> "10:00" 1

    # Add an all-day task (escalates: SMS -> Telegram -> Call)
    python add_tasks.py allday "Submit report" <user_id>

    # Register a user
    python add_tasks.py user <user_id> "Name" <platform>

    # List pending tasks
    python add_tasks.py list

    # List users
    python add_tasks.py users
"""
from db import add_task, ensure_user, get_pending_tasks, get_user, init_db, _get_conn
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import sys
import calendar

IST = ZoneInfo("Asia/Kolkata")


def list_tasks():
    tasks = get_pending_tasks()
    if not tasks:
        print("No pending tasks.")
        return

    print(f"{'ID':<5} {'Message':<25} {'User':<15} {'Due':<20} {'Type':<10} {'Streak':<6}")
    print("-" * 90)
    for t in tasks:
        if t['is_allday']:
            task_type = f"allday/{t['escalation_stage']}"
        elif t['is_recurring']:
            task_type = t['recurrence_type'] or 'recurring'
        else:
            task_type = 'one-time'
        print(f"{t['id']:<5} {t['message'][:24]:<25} {t['user_id'][:14]:<15} {t['time'][:19]:<20} {task_type:<10} {t['streak'] or 0:<6}")


def list_users():
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users")
    users = cursor.fetchall()
    if not users:
        print("No users.")
        return

    print(f"{'User ID':<15} {'Name':<15} {'Phone':<15} {'Platform':<10}")
    print("-" * 60)
    for u in users:
        print(f"{u['user_id'][:14]:<15} {(u['name'] or '')[:14]:<15} {(u['phone'] or 'N/A'):<15} {(u['platform'] or ''):<10}")


def add_one_time_task(message, user_id, time_str):
    task_time = datetime.fromisoformat(time_str)
    if task_time.tzinfo is None:
        task_time = task_time.replace(tzinfo=IST)
    task_id = add_task(message=message, user_id=user_id, time=task_time.isoformat())
    if task_id:
        print(f"Task created: ID={task_id}, '{message}' for {user_id} at {task_time}")
    else:
        print(f"Duplicate task: '{message}' already exists for {user_id}")


def add_recurring_task(rec_type, message, user_id, time_hhmm, day=None):
    hour, minute = map(int, time_hhmm.split(":"))
    now = datetime.now(IST)

    if rec_type == "daily":
        first_time = (now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    elif rec_type == "weekdays":
        first_time = (now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        while first_time.weekday() >= 5:
            first_time += timedelta(days=1)
    elif rec_type == "weekly":
        first_time = (now + timedelta(days=7)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    elif rec_type == "monthly":
        target_day = int(day) if day else now.day
        year = now.year
        month = now.month
        last_day = calendar.monthrange(year, month)[1]
        actual_day = min(target_day, last_day)
        first_time = datetime(year, month, actual_day, hour, minute, 0, tzinfo=IST)
        if first_time <= now:
            month += 1
            if month > 12:
                month = 1
                year += 1
            actual_day = min(target_day, calendar.monthrange(year, month)[1])
            first_time = datetime(year, month, actual_day, hour, minute, 0, tzinfo=IST)
    else:
        first_time = (now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)

    task_id = add_task(
        message=message,
        user_id=user_id,
        time=first_time.isoformat(),
        is_recurring=True,
        recurrence_type=rec_type,
        recurrence_time=time_hhmm,
        recurrence_day=int(day) if day else None,
    )
    if task_id:
        print(f"Recurring task created: ID={task_id}, '{message}' {rec_type} at {time_hhmm} (first: {first_time})")
    else:
        print(f"Duplicate: '{message}' already exists for {user_id}")


def add_allday_task(message, user_id):
    now = datetime.now(IST)
    first_time = (now + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)

    task_id = add_task(
        message=message,
        user_id=user_id,
        time=first_time.isoformat(),
        is_allday=True,
    )
    if task_id:
        print(f"All-day task created: ID={task_id}, '{message}' (escalation starts {first_time})")
    else:
        print(f"Duplicate: '{message}' already exists for {user_id}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    command = sys.argv[1].lower()

    if command == "list":
        list_tasks()
    elif command == "users":
        list_users()
    elif command == "user" and len(sys.argv) >= 4:
        user_id = sys.argv[2]
        name = sys.argv[3]
        platform = sys.argv[4] if len(sys.argv) > 4 else "cli"
        created = ensure_user(user_id, name, platform)
        if created:
            print(f"User created: {user_id} ({name})")
        else:
            print(f"User already exists: {user_id}")
    elif command == "task" and len(sys.argv) >= 5:
        add_one_time_task(sys.argv[2], sys.argv[3], sys.argv[4])
    elif command in ("daily", "weekdays", "weekly") and len(sys.argv) >= 5:
        add_recurring_task(command, sys.argv[2], sys.argv[3], sys.argv[4])
    elif command == "monthly" and len(sys.argv) >= 5:
        day = sys.argv[5] if len(sys.argv) > 5 else None
        add_recurring_task("monthly", sys.argv[2], sys.argv[3], sys.argv[4], day)
    elif command == "allday" and len(sys.argv) >= 4:
        add_allday_task(sys.argv[2], sys.argv[3])
    else:
        print(f"Unknown command or missing arguments: {command}")
        print(__doc__)


if __name__ == "__main__":
    main()
