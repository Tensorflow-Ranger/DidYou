from apscheduler.schedulers.background import BackgroundScheduler
from caller import make_call, send_sms, send_whatsapp
from db import (
    get_pending_tasks, 
    update_task_time, 
    increment_task_attempts,
    get_task_daily_calls,
    get_allowed_number,
    log_call,
    get_escalation_stage,
    advance_escalation,
)
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import random
import os

scheduler = BackgroundScheduler()

# Configuration
MAX_DAILY_CALLS_DEFAULT = int(os.getenv("MAX_DAILY_CALLS", "5"))
MAX_ATTEMPTS = int(os.getenv("MAX_ATTEMPTS", "20"))
DEFAULT_TIMEZONE = "Asia/Kolkata"  # IST
IST = ZoneInfo(DEFAULT_TIMEZONE)

# Expanding interval schedule (in minutes)
INTERVAL_SCHEDULE = [1, 5, 15, 30, 60, 120, 240]

# All-day task escalation schedule (hours after first notification)
# SMS at 8am, WhatsApp at 12pm, Call at 6pm
ALLDAY_ESCALATION_HOURS = {
    'sms': 8,       # 8 AM
    'whatsapp': 12, # 12 PM (noon)  
    'call': 18,     # 6 PM
}


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
    - All-day task escalation: SMS -> WhatsApp -> Call
    """
    tasks = get_pending_tasks()
    now = datetime.now(IST)
    current_hour = now.hour

    for task in tasks:
        task_id = task["id"]
        message = task["message"]
        phone = task["phone"]
        scheduled_time = task["time"]
        attempts = task["attempts"] or 0
        is_allday = task["is_allday"]

        task_time = datetime.fromisoformat(scheduled_time)
        # Strip timezone info for comparison — all times are IST
        if task_time.tzinfo is not None:
            task_time = task_time.replace(tzinfo=None)
        now_naive = now.replace(tzinfo=None)

        # Not yet time for this task
        if now_naive < task_time:
            continue

        # Handle all-day tasks with escalation
        if is_allday:
            handle_allday_task(task, now, current_hour)
            continue

        # Regular task handling below...
        
        # Check if we've hit max attempts
        if attempts >= MAX_ATTEMPTS:
            print(f"Task {task_id} hit max attempts ({MAX_ATTEMPTS}), skipping")
            continue

        # Check quiet hours
        if is_quiet_hours(phone):
            user = get_allowed_number(phone)
            quiet_end = user["quiet_end"] if user else "08:00"
            
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
            tomorrow = (now + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
            update_task_time(task_id, tomorrow)
            print(f"Task {task_id} delayed to tomorrow (daily cap of {max_calls} reached)")
            continue

        # Make the call
        call_sid = make_call(phone, task_id, message)
        
        if call_sid:
            log_call(task_id, phone, "call", "initiated", call_sid)
            
            new_attempts = increment_task_attempts(task_id)
            next_interval = get_next_interval(new_attempts)
            new_time = now + timedelta(minutes=next_interval)
            update_task_time(task_id, new_time)
            
            print(f"Task {task_id}: Call initiated (attempt {new_attempts}), next retry in {next_interval:.1f} min")
        else:
            new_time = now + timedelta(minutes=1)
            update_task_time(task_id, new_time)
            print(f"Task {task_id}: Call failed to initiate, retrying in 1 min")


def handle_allday_task(task, now, current_hour):
    """
    Handle all-day tasks with SMS -> WhatsApp -> Call escalation.
    - 8 AM: Send SMS
    - 12 PM: Send WhatsApp
    - 6 PM: Start calling
    """
    task_id = task["id"]
    message = task["message"]
    phone = task["phone"]
    stage = task["escalation_stage"] or 'sms'
    attempts = task["attempts"] or 0
    
    reminder_msg = f"Reminder: {message}. Reply DONE when completed."
    
    # Determine what action to take based on current hour and stage
    if stage == 'sms' and current_hour >= ALLDAY_ESCALATION_HOURS['sms']:
        # Send SMS
        sid = send_sms(phone, reminder_msg)
        if sid:
            log_call(task_id, phone, "sms", "sent", sid)
            print(f"Task {task_id}: SMS sent")
        
        # Advance to next stage for later
        advance_escalation(task_id)
        
        # Schedule next check for WhatsApp time
        next_time = now.replace(hour=ALLDAY_ESCALATION_HOURS['whatsapp'], minute=0, second=0, microsecond=0)
        if next_time <= now:
            next_time += timedelta(days=1)
        update_task_time(task_id, next_time)
        
    elif stage == 'whatsapp' and current_hour >= ALLDAY_ESCALATION_HOURS['whatsapp']:
        # Send WhatsApp
        sid = send_whatsapp(phone, reminder_msg)
        if sid:
            log_call(task_id, phone, "whatsapp", "sent", sid)
            print(f"Task {task_id}: WhatsApp sent")
        
        # Advance to call stage
        advance_escalation(task_id)
        
        # Schedule next check for call time
        next_time = now.replace(hour=ALLDAY_ESCALATION_HOURS['call'], minute=0, second=0, microsecond=0)
        if next_time <= now:
            next_time += timedelta(days=1)
        update_task_time(task_id, next_time)
        
    elif stage == 'call' and current_hour >= ALLDAY_ESCALATION_HOURS['call']:
        # Now we call (use regular call logic with expanding intervals)
        if attempts >= MAX_ATTEMPTS:
            print(f"Task {task_id} hit max attempts, skipping")
            return
        
        if is_quiet_hours(phone):
            user = get_allowed_number(phone)
            quiet_end = user["quiet_end"] if user else "08:00"
            hour, minute = map(int, quiet_end.split(":"))
            next_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if next_time <= now:
                next_time += timedelta(days=1)
            update_task_time(task_id, next_time)
            print(f"Task {task_id} delayed (quiet hours)")
            return
        
        daily_calls = get_task_daily_calls(task_id)
        max_calls = get_max_daily_calls(phone)
        if daily_calls >= max_calls:
            tomorrow = (now + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
            update_task_time(task_id, tomorrow)
            print(f"Task {task_id} delayed to tomorrow (daily cap)")
            return
        
        call_sid = make_call(phone, task_id, message)
        if call_sid:
            log_call(task_id, phone, "call", "initiated", call_sid)
            new_attempts = increment_task_attempts(task_id)
            next_interval = get_next_interval(new_attempts)
            new_time = now + timedelta(minutes=next_interval)
            update_task_time(task_id, new_time)
            print(f"Task {task_id}: Call initiated (attempt {new_attempts})")
        else:
            new_time = now + timedelta(minutes=1)
            update_task_time(task_id, new_time)
            print(f"Task {task_id}: Call failed, retrying")


# Run every 30 seconds
scheduler.add_job(check_tasks, 'interval', seconds=30, id='check_tasks', replace_existing=True)
scheduler.start()
