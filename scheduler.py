from apscheduler.schedulers.background import BackgroundScheduler
from caller import make_call
from db import (
    get_pending_tasks, 
    update_task_time, 
    increment_task_attempts,
    get_task_daily_calls,
    get_allowed_number,
    log_call,
)
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import random
import os

scheduler = BackgroundScheduler()

# Configuration
MAX_DAILY_CALLS_DEFAULT = int(os.getenv("MAX_DAILY_CALLS", "5"))
MAX_ATTEMPTS = int(os.getenv("MAX_ATTEMPTS", "20"))  # Stop after this many total attempts
DEFAULT_TIMEZONE = "Asia/Kolkata"  # IST
IST = ZoneInfo(DEFAULT_TIMEZONE)

# Expanding interval schedule (in minutes)
# Based on cognitive science: expanding intervals are 2x more effective than fixed
INTERVAL_SCHEDULE = [1, 5, 15, 30, 60, 120, 240]  # 1min, 5min, 15min, 30min, 1hr, 2hr, 4hr


def get_next_interval(attempts):
    """
    Get the next retry interval based on attempt count.
    Uses expanding intervals with ±20% jitter.
    """
    if attempts >= len(INTERVAL_SCHEDULE):
        base_minutes = INTERVAL_SCHEDULE[-1]  # Cap at max interval
    else:
        base_minutes = INTERVAL_SCHEDULE[attempts]
    
    # Add ±20% jitter to prevent predictable patterns
    jitter = base_minutes * 0.2
    actual_minutes = base_minutes + random.uniform(-jitter, jitter)
    
    return max(1, actual_minutes)  # Minimum 1 minute


def is_quiet_hours(phone):
    """
    Check if it's currently quiet hours for this phone number.
    Uses IST (Asia/Kolkata) by default.
    Returns True if we should NOT call right now.
    """
    user = get_allowed_number(phone)
    if user is None:
        quiet_start = "22:00"
        quiet_end = "08:00"
        tz_name = DEFAULT_TIMEZONE
    else:
        quiet_start = user["quiet_start"] or "22:00"
        quiet_end = user["quiet_end"] or "08:00"
        tz_name = user["timezone"] or DEFAULT_TIMEZONE
    
    # Get current time in user's timezone (IST by default)
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo(DEFAULT_TIMEZONE)
    
    now = datetime.now(tz)
    current_time = now.strftime("%H:%M")
    
    # Handle overnight quiet hours (e.g., 22:00 - 08:00)
    if quiet_start > quiet_end:
        # Quiet if current time is after start OR before end
        return current_time >= quiet_start or current_time < quiet_end
    else:
        # Quiet if current time is between start and end
        return quiet_start <= current_time < quiet_end


def get_max_daily_calls(phone):
    """Get the max daily calls allowed for a phone number."""
    user = get_allowed_number(phone)
    if user is None:
        return MAX_DAILY_CALLS_DEFAULT
    return user["max_daily_calls"] or MAX_DAILY_CALLS_DEFAULT


def check_tasks():
    """
    Main scheduler loop: check all pending tasks and make calls as needed.
    Implements:
    - Expanding intervals (cognitive science backed)
    - Quiet hours (legal compliance)
    - Daily call caps (cost/annoyance limit)
    - Jitter (prevents predictable patterns)
    """
    tasks = get_pending_tasks()
    now = datetime.now(IST)

    for task in tasks:
        task_id = task["id"]
        message = task["message"]
        phone = task["phone"]
        scheduled_time = task["time"]
        attempts = task["attempts"] or 0

        task_time = datetime.fromisoformat(scheduled_time)
        # Ensure task_time is IST-aware for comparison
        if task_time.tzinfo is None:
            task_time = task_time.replace(tzinfo=IST)

        # Not yet time for this task
        if now < task_time:
            continue

        # Check if we've hit max attempts
        if attempts >= MAX_ATTEMPTS:
            print(f"Task {task_id} hit max attempts ({MAX_ATTEMPTS}), skipping")
            continue

        # Check quiet hours
        if is_quiet_hours(phone):
            # Reschedule to end of quiet hours
            user = get_allowed_number(phone)
            quiet_end = user["quiet_end"] if user else "08:00"
            
            # Parse quiet_end and create next available time
            hour, minute = map(int, quiet_end.split(":"))
            next_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if next_time <= now:
                next_time += timedelta(days=1)
            
            update_task_time(task_id, next_time)
            print(f"Task {task_id} delayed to {next_time} (quiet hours)")
            continue

        # Check daily call cap
        daily_calls = get_task_daily_calls(task_id)
        max_calls = get_max_daily_calls(phone)
        if daily_calls >= max_calls:
            # Reschedule to tomorrow
            tomorrow = (now + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
            update_task_time(task_id, tomorrow)
            print(f"Task {task_id} delayed to tomorrow (daily cap of {max_calls} reached)")
            continue

        # Make the call
        call_sid = make_call(phone, task_id, message)
        
        if call_sid:
            # Log the call
            log_call(task_id, phone, "call", "initiated", call_sid)
            
            # Increment attempts and schedule next retry with expanding interval
            new_attempts = increment_task_attempts(task_id)
            next_interval = get_next_interval(new_attempts)
            new_time = now + timedelta(minutes=next_interval)
            update_task_time(task_id, new_time)
            
            print(f"Task {task_id}: Call initiated (attempt {new_attempts}), next retry in {next_interval:.1f} min")
        else:
            # Call failed to initiate, retry sooner
            new_time = now + timedelta(minutes=1)
            update_task_time(task_id, new_time)
            print(f"Task {task_id}: Call failed to initiate, retrying in 1 min")


# Run every 30 seconds
scheduler.add_job(check_tasks, 'interval', seconds=30, id='check_tasks', replace_existing=True)
scheduler.start()
