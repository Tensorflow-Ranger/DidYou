"""
Natural language reminder parser - extracted from server.py.
Pure function: no side effects, no imports from db/caller/plugins.
"""
# pf:never:parser.never_import_db NEVER import from db, caller, plugins, or any module with side effects
# pf:never:parser.never_network NEVER make network calls or access environment variables
import re
import calendar
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import dateparser

DEFAULT_TIMEZONE = "Asia/Kolkata"
IST = ZoneInfo(DEFAULT_TIMEZONE)


# pf:ensures:parser.pure_function parse_reminder is a pure function - same input always same output
# pf:ensures:parser.exact_behavior Returns exact same results as current server.py parse_reminder
def parse_reminder(message_body: str) -> Optional[dict]:
    """
    Parse natural language reminder messages.
    Examples:
      "remind me to drink water at 3pm"
      "remind me to call mom tomorrow at 9am"
      "buy groceries in 30 minutes"
      "remind me to exercise daily at 7am"
      "drink water every day at 9am"
      "standup meeting weekdays at 10am"

    Returns dict with 'task', 'time', and optionally 'is_recurring', 'recurrence_type', 'recurrence_time'
    or None if parsing fails.
    """
    if not message_body or not message_body.strip():
        return None

    message_lower = message_body.lower().strip()

    # Check for recurring patterns first
    recurrence_type = None
    recurrence_time = None
    is_recurring = False

    # Patterns: "daily at X", "every day at X", "weekdays at X", "weekly at X", "monthly at X"
    # Timed recurring patterns (require "at TIME")
    recurring_patterns = [
        (r'\b(daily|every\s*day)\b.*?\bat\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)', 'daily'),
        (r'\b(weekdays|every\s*weekday|mon(?:day)?[\s-]*fri(?:day)?)\b.*?\bat\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)', 'weekdays'),
        (r'\b(weekly|every\s*week)\b.*?\bat\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)', 'weekly'),
        (r'\b(monthly|every\s*month)\b(?:.*?\bon\s+(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?)?.*?\bat\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)', 'monthly'),
    ]

    # All-day recurring patterns (no time required — triggers escalation)
    allday_recurring_patterns = [
        (r'\b(monthly|every\s*month)\b(?:.*?\bon\s+(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?)?', 'monthly'),
        (r'\b(daily|every\s*day)\b', 'daily'),
        (r'\b(weekdays|every\s*weekday|mon(?:day)?[\s-]*fri(?:day)?)\b', 'weekdays'),
        (r'\b(weekly|every\s*week)\b', 'weekly'),
    ]

    # Try timed patterns first
    for pattern, rec_type in recurring_patterns:
        match = re.search(pattern, message_lower)
        if match:
            is_recurring = True
            recurrence_type = rec_type

            # Monthly has an extra group for day-of-month
            if recurrence_type == "monthly":
                day_str = match.group(2)  # optional day like "1", "15"
                time_str = match.group(3)
            else:
                day_str = None
                time_str = match.group(2)

            # Parse the time to get HH:MM format
            parsed = dateparser.parse(
                f"today at {time_str}",
                settings={'TIMEZONE': DEFAULT_TIMEZONE, 'RETURN_AS_TIMEZONE_AWARE': True}
            )
            if parsed:
                recurrence_time = parsed.strftime("%H:%M")

            # Extract the task by removing the recurring keywords and time
            task = re.sub(pattern, '', message_lower).strip()
            task = re.sub(r'^(remind\s+me\s+to|reminder[:\s]+)', '', task).strip()
            task = re.sub(r'\s+', ' ', task).strip()  # Clean up whitespace

            if task:
                now = datetime.now(IST)
                if recurrence_time:
                    hour, minute = map(int, recurrence_time.split(":"))
                else:
                    hour, minute = 9, 0  # Default to 9 AM

                # For monthly, determine the target day
                if recurrence_type == "monthly":
                    recurrence_day = int(day_str) if day_str else now.day
                else:
                    recurrence_day = None

                if parsed and parsed > now and recurrence_type != "monthly":
                    first_time = parsed
                else:
                    # Calculate next valid occurrence
                    if recurrence_type == "monthly":
                        # Target the specified day this month or next
                        year = now.year
                        month = now.month
                        last_day = calendar.monthrange(year, month)[1]
                        target_day = min(recurrence_day or now.day, last_day)
                        target = datetime(year, month, target_day, hour, minute, 0, tzinfo=IST)
                        if target <= now:
                            month += 1
                            if month > 12:
                                month = 1
                                year += 1
                            target_day = min(recurrence_day or now.day, calendar.monthrange(year, month)[1])
                            target = datetime(year, month, target_day, hour, minute, 0, tzinfo=IST)
                        first_time = target
                    elif recurrence_type == "weekly":
                        first_time = (now + timedelta(days=7)).replace(
                            hour=hour, minute=minute, second=0, microsecond=0
                        )
                    elif recurrence_type == "weekdays":
                        first_time = (now + timedelta(days=1)).replace(
                            hour=hour, minute=minute, second=0, microsecond=0
                        )
                        while first_time.weekday() >= 5:
                            first_time += timedelta(days=1)
                    else:
                        # daily
                        first_time = (now + timedelta(days=1)).replace(
                            hour=hour, minute=minute, second=0, microsecond=0
                        )

                return {
                    "task": task,
                    "time": first_time,
                    "is_recurring": True,
                    "recurrence_type": recurrence_type,
                    "recurrence_time": recurrence_time,
                    "recurrence_day": recurrence_day,
                }

    # Try all-day recurring patterns (no time specified — escalation through the day)
    for pattern, rec_type in allday_recurring_patterns:
        match = re.search(pattern, message_lower)
        if match:
            if rec_type == "monthly":
                day_str = match.group(2)  # optional day like "1", "15"
            else:
                day_str = None

            # Extract the task by removing the recurring keywords
            task = re.sub(pattern, '', message_lower).strip()
            task = re.sub(r'^(remind\s+me\s+to|reminder[:\s]+)', '', task).strip()
            task = re.sub(r'\s+', ' ', task).strip()

            if not task:
                continue

            now = datetime.now(IST)
            recurrence_day = int(day_str) if day_str else now.day if rec_type == "monthly" else None

            # Schedule first occurrence at 8am (start of escalation)
            if rec_type == "monthly":
                year = now.year
                month = now.month
                last_day = calendar.monthrange(year, month)[1]
                target_day = min(recurrence_day or now.day, last_day)
                first_time = datetime(year, month, target_day, 8, 0, 0, tzinfo=IST)
                if first_time <= now:
                    month += 1
                    if month > 12:
                        month = 1
                        year += 1
                    target_day = min(recurrence_day or now.day, calendar.monthrange(year, month)[1])
                    first_time = datetime(year, month, target_day, 8, 0, 0, tzinfo=IST)
            elif rec_type == "weekly":
                first_time = (now + timedelta(days=7)).replace(hour=8, minute=0, second=0, microsecond=0)
            elif rec_type == "weekdays":
                first_time = (now + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
                while first_time.weekday() >= 5:
                    first_time += timedelta(days=1)
            else:
                # daily
                first_time = (now + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)

            return {
                "task": task,
                "time": first_time,
                "is_recurring": True,
                "is_allday": True,
                "recurrence_type": rec_type,
                "recurrence_time": None,
                "recurrence_day": recurrence_day,
            }

    # Non-recurring task parsing (existing logic)
    patterns = [
        # "remind me to X on DATE at TIME" — specific date with time
        r"remind(?:\s+me)?\s+to\s+(.+?)\s+on\s+(.+?\s+at\s+.+)",
        # "X on DATE at TIME" — specific date with time
        r"(.+?)\s+on\s+((?:the\s+)?\d{1,2}(?:st|nd|rd|th)?(?:\s+\w+)?\s+at\s+.+)",
        r"(.+?)\s+on\s+(\w+\s+\d{1,2}(?:st|nd|rd|th)?\s+at\s+.+)",
        # "remind me to X on DATE" — specific date, no time (all-day)
        r"remind(?:\s+me)?\s+to\s+(.+?)\s+on\s+((?:the\s+)?\d{1,2}(?:st|nd|rd|th)?(?:\s+of\s+\w+|\s+\w+)?)\s*$",
        r"remind(?:\s+me)?\s+to\s+(.+?)\s+on\s+(\w+\s+\d{1,2}(?:st|nd|rd|th)?)\s*$",
        # "X on DATE" — specific date, no time (all-day)
        r"(.+?)\s+on\s+((?:the\s+)?\d{1,2}(?:st|nd|rd|th)?(?:\s+of\s+\w+|\s+\w+)?)\s*$",
        r"(.+?)\s+on\s+(\w+\s+\d{1,2}(?:st|nd|rd|th)?)\s*$",
        # "remind me to X at/in/by Y"
        r"remind(?:\s+me)?\s+to\s+(.+?)\s+(at|in|by|tomorrow|next\s+\w+)\s*(.+)?",
        # "X at/in Y" (simpler)
        r"(.+?)\s+(at|in)\s+(.+)",
        # "X tomorrow"
        r"(.+?)\s+(tomorrow)(?:\s+(.+))?",
    ]

    task = None
    time_str = None

    for pattern in patterns:
        match = re.search(pattern, message_lower)
        if match:
            groups = match.groups()
            task = groups[0].strip()

            # Build time string from remaining groups
            time_parts = [g for g in groups[1:] if g]
            time_str = " ".join(time_parts).strip()

            # Clean up task (remove "remind me to" etc.)
            task = re.sub(r"^(remind\s+me\s+to|reminder[:\s]+)", "", task).strip()

            break

    if not task or not time_str:
        # Try to parse the whole message as "task at time"
        # Simple fallback: look for time-like patterns at the end
        time_match = re.search(r"(at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?|\bin\s+\d+\s+\w+|tomorrow|tonight|today)", message_lower)
        if time_match:
            time_str = time_match.group(1)
            task = message_lower[:time_match.start()].strip()
            task = re.sub(r"^(remind\s+me\s+to|reminder[:\s]+)", "", task).strip()

    if not task:
        return None

    # Check if this is an all-day task (no time-of-day specified)
    time_of_day_indicators = [
        r'\bat\s+\d',              # "at 3pm", "at 15:00"
        r'\bin\s+\d+\s+\w+',       # "in 30 minutes"
        r'\b\d{1,2}:\d{2}',        # "15:00", "3:30"
        r'\b\d{1,2}\s*(?:am|pm)\b',# "3pm", "3 pm"
        r'\bmorning\b',
        r'\bevening\b',
        r'\bnoon\b',
        r'\bmidnight\b',
        r'\btonight\b',
    ]

    has_time_of_day = any(re.search(p, message_lower) for p in time_of_day_indicators)

    # Check if there's a specific date
    date_indicators = [
        r'\bon\s+(?:the\s+)?\d{1,2}(?:st|nd|rd|th)?',  # "on 15th", "on the 25th"
        r'\bon\s+\w+\s+\d{1,2}',                        # "on May 15"
        r'\btomorrow\b',
    ]
    has_specific_date = any(re.search(p, message_lower) for p in date_indicators)

    if not has_time_of_day:
        # All-day task
        now = datetime.now(IST)

        if has_specific_date and time_str:
            # Parse the date, set time to 8am for escalation start
            parsed_date = dateparser.parse(
                time_str,
                settings={
                    'PREFER_DATES_FROM': 'future',
                    'RETURN_AS_TIMEZONE_AWARE': True,
                    'TIMEZONE': DEFAULT_TIMEZONE,
                }
            )
            if parsed_date:
                # Set to 8am on that date
                first_time = parsed_date.replace(hour=8, minute=0, second=0, microsecond=0)
                if first_time.tzinfo is None:
                    first_time = first_time.replace(tzinfo=IST)
            else:
                first_time = (now + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
        else:
            # No specific date — tomorrow 8am
            first_time = (now + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)

        # Clean up task text — remove date parts
        clean_task = task
        clean_task = re.sub(r'\s+on\s+(?:the\s+)?\d{1,2}(?:st|nd|rd|th)?(?:\s+of\s+\w+|\s+\w+)?', '', clean_task)
        clean_task = re.sub(r'\s+on\s+\w+\s+\d{1,2}(?:st|nd|rd|th)?', '', clean_task)
        clean_task = re.sub(r'\s+tomorrow', '', clean_task)
        clean_task = re.sub(r'\s+', ' ', clean_task).strip()

        if clean_task:
            return {
                "task": clean_task,
                "time": first_time,
                "is_recurring": False,
                "is_allday": True,
            }

    # Parse the time string using IST
    parsed_time = dateparser.parse(
        time_str or "in 1 hour",
        settings={
            'PREFER_DATES_FROM': 'future',
            'RETURN_AS_TIMEZONE_AWARE': True,
            'TIMEZONE': DEFAULT_TIMEZONE,
        }
    )

    if parsed_time is None:
        # Default to 1 hour from now in IST if time parsing fails
        parsed_time = datetime.now(IST) + timedelta(hours=1)

    # Ensure we have IST timezone
    if parsed_time.tzinfo is None:
        parsed_time = parsed_time.replace(tzinfo=IST)

    return {"task": task, "time": parsed_time, "is_recurring": False, "is_allday": False}
