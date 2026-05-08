"""
Scheduler - APScheduler-based task checker with escalation via plugin registry.
"""
# pf:never:scheduler.never_import_caller NEVER import from caller.py directly
# pf:never:scheduler.never_hardcode_escalation NEVER hardcode escalation stages as literals
import logging
import random
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler

from config import Config
from db import (
    advance_escalation,
    get_allday_tasks_for_escalation,
    get_pending_tasks,
    get_task_daily_calls,
    get_user,
    increment_task_attempts,
    log_call,
    update_task_time,
)
from plugins import PluginRegistry

logger = logging.getLogger(__name__)

# Expanding interval schedule (in minutes)
INTERVAL_SCHEDULE = [1, 5, 15, 30, 60, 120, 240]


def get_next_interval(attempts: int) -> float:
    """Get the next retry interval with +/-20% jitter."""
    if attempts >= len(INTERVAL_SCHEDULE):
        base_minutes = INTERVAL_SCHEDULE[-1]
    else:
        base_minutes = INTERVAL_SCHEDULE[attempts]
    jitter = base_minutes * 0.2
    actual_minutes = base_minutes + random.uniform(-jitter, jitter)
    return max(1, actual_minutes)


def is_quiet_hours(user) -> bool:
    """Check if it's currently quiet hours for this user."""
    if user is None:
        quiet_start = "22:00"
        quiet_end = "08:00"
        tz_name = "Asia/Kolkata"
    else:
        quiet_start = user["quiet_start"] or "22:00"
        quiet_end = user["quiet_end"] or "08:00"
        tz_name = user["timezone"] or "Asia/Kolkata"

    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("Asia/Kolkata")

    now = datetime.now(tz)
    current_time = now.strftime("%H:%M")

    if quiet_start > quiet_end:
        return current_time >= quiet_start or current_time < quiet_end
    else:
        return quiet_start <= current_time < quiet_end


# pf:requires:scheduler.registry_initialized Registry must be initialized before check_tasks
# pf:ensures:scheduler.dispatches_via_registry All sending goes through registry.send()
def check_tasks(registry: PluginRegistry, config: Config) -> None:
    """
    Main scheduler loop: check all pending tasks and dispatch reminders.
    All message sending goes through registry.send() - never imports caller directly.
    """
    IST = ZoneInfo(config.DEFAULT_TIMEZONE)
    now = datetime.now(IST)
    current_hour = now.hour
    tasks = get_pending_tasks()

    for task in tasks:
        task_id = task["id"]
        message = task["message"]
        user_id = task["user_id"]
        scheduled_time = task["time"]
        attempts = task["attempts"] or 0
        is_allday = task["is_allday"]

        task_time = datetime.fromisoformat(scheduled_time)
        if task_time.tzinfo is not None:
            task_time = task_time.replace(tzinfo=None)
        now_naive = now.replace(tzinfo=None)

        if now_naive < task_time:
            continue

        if is_allday:
            handle_allday_escalation(task, registry, config, now, current_hour)
            continue

        # Regular task handling
        if attempts >= config.MAX_ATTEMPTS:
            logger.info(f"Task {task_id} hit max attempts ({config.MAX_ATTEMPTS}), skipping")
            continue

        user = get_user(user_id)
        logger.debug(f"Task {task_id}: user_id={user_id}, user_found={user is not None}, phone={user['phone'] if user else 'N/A'}")

        # Check quiet hours (needs phone for call/sms)
        if user and user["phone"] and is_quiet_hours(user):
            quiet_end = user["quiet_end"] or "08:00"
            hour, minute = map(int, quiet_end.split(":"))
            next_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if next_time <= now:
                next_time += timedelta(days=1)
            update_task_time(task_id, next_time)
            continue

        # Check daily call cap
        daily_calls = get_task_daily_calls(task_id)
        if daily_calls >= config.MAX_DAILY_CALLS:
            tomorrow = (now + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
            update_task_time(task_id, tomorrow)
            continue

        # Determine target and channel for regular tasks
        # Use phone for calls/SMS if available, otherwise Telegram
        if user and user["phone"]:
            target = user["phone"]
            channel = "call"
        else:
            target = user_id
            channel = "telegram"

        reminder_msg = f"Reminder: {message}. Reply DONE when completed."
        logger.info(f"Task {task_id}: sending via {channel} to {target[:6]}...")
        success = registry.send(channel, target, reminder_msg, task_id=task_id, call=(channel == "call"))

        if success:
            log_call(task_id, user_id, channel, "sent")
            new_attempts = increment_task_attempts(task_id)
            next_interval = get_next_interval(new_attempts)
            new_time = now + timedelta(minutes=next_interval)
            update_task_time(task_id, new_time)
        else:
            # Call failed — fall back to Telegram immediately
            if channel == "call":
                fallback = registry.send("telegram", user_id, reminder_msg)
                if fallback:
                    log_call(task_id, user_id, "telegram", "fallback_from_call")
            new_attempts = increment_task_attempts(task_id)
            next_interval = get_next_interval(new_attempts)
            new_time = now + timedelta(minutes=next_interval)
            update_task_time(task_id, new_time)


# pf:ensures:scheduler.configurable_escalation Escalation chain from config.ESCALATION_CHAIN
# pf:ensures:scheduler.skip_unavailable_channel Skips sms/call if user has no phone
def handle_allday_escalation(task, registry: PluginRegistry, config: Config, now: datetime, current_hour: int) -> None:
    """
    Handle all-day tasks with configurable escalation chain.
    Reads chain from config (default: sms -> telegram -> call).
    Skips channels requiring phone if user has no phone.
    """
    task_id = task["id"]
    message = task["message"]
    user_id = task["user_id"]
    stage = task["escalation_stage"] or config.ESCALATION_CHAIN[0]
    attempts = task["attempts"] or 0

    user = get_user(user_id)
    if user is None:
        logger.error(f"Task {task_id}: user {user_id} not found")
        return

    chain = config.ESCALATION_CHAIN
    if stage not in chain:
        stage = chain[0]

    current_idx = chain.index(stage)
    channel = chain[current_idx]

    # Escalation hours based on position in chain
    # Distribute across day: first stage at 8am, subsequent stages spaced out
    escalation_hours = {}
    base_hour = 8
    hour_step = max(1, (18 - 8) // max(1, len(chain) - 1)) if len(chain) > 1 else 0
    for i, ch in enumerate(chain):
        escalation_hours[ch] = base_hour + (i * hour_step)

    target_hour = escalation_hours.get(channel, 8)

    # Not yet time for this stage
    if current_hour < target_hour:
        return

    # Check channel availability (sms/call need phone)
    phone_channels = ("sms", "call")
    if channel in phone_channels and (not user["phone"]):
        if channel == "sms":
            # Skip to next stage
            advance_escalation(task_id)
            return
        if channel == "call":
            # Fall back to telegram
            reminder_msg = f"Reminder: {message}. Reply DONE when completed."
            registry.send("telegram", user_id, reminder_msg)
            return

    # Dispatch
    reminder_msg = f"Reminder: {message}. Reply DONE when completed."
    if channel in phone_channels:
        target = user["phone"]
    else:
        target = user_id

    kwargs = {}
    if channel == "call":
        kwargs["call"] = True
        kwargs["task_id"] = task_id

        # Check call-specific constraints
        if attempts >= config.MAX_ATTEMPTS:
            logger.info(f"Task {task_id} hit max attempts, skipping")
            return
        if is_quiet_hours(user):
            quiet_end = user["quiet_end"] or "08:00"
            hour, minute = map(int, quiet_end.split(":"))
            next_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if next_time <= now:
                next_time += timedelta(days=1)
            update_task_time(task_id, next_time)
            return
        daily_calls = get_task_daily_calls(task_id)
        if daily_calls >= config.MAX_DAILY_CALLS:
            tomorrow = (now + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
            update_task_time(task_id, tomorrow)
            return

    success = registry.send(channel, target, reminder_msg, **kwargs)
    if success:
        log_call(task_id, user_id, channel, "sent")

    # Advance stage and schedule next
    if channel != "call":
        advance_escalation(task_id)
        # Schedule for next stage's hour
        next_idx = current_idx + 1
        if next_idx < len(chain):
            next_channel = chain[next_idx]
            next_hour = escalation_hours.get(next_channel, current_hour + 4)
            next_time = now.replace(hour=min(next_hour, 23), minute=0, second=0, microsecond=0)
            if next_time <= now:
                next_time += timedelta(days=1)
            update_task_time(task_id, next_time)
    else:
        # In call stage: use expanding intervals
        if success:
            new_attempts = increment_task_attempts(task_id)
            next_interval = get_next_interval(new_attempts)
            new_time = now + timedelta(minutes=next_interval)
            update_task_time(task_id, new_time)
        else:
            new_time = now + timedelta(minutes=1)
            update_task_time(task_id, new_time)


def start_scheduler(registry: PluginRegistry, config: Config) -> BackgroundScheduler:
    """Create and start the background scheduler."""
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        check_tasks,
        'interval',
        seconds=30,
        id='check_tasks',
        replace_existing=True,
        args=[registry, config],
    )
    scheduler.start()
    logger.info("Scheduler started (30s interval)")
    return scheduler
