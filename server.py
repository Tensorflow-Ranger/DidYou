from flask import Flask, request, abort
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.twiml.messaging_response import MessagingResponse
from twilio.request_validator import RequestValidator
from functools import wraps
from datetime import datetime
from zoneinfo import ZoneInfo
import dateparser
import logging
import traceback
import os
import re

from db import (
    add_task, 
    update_task, 
    get_task,
    is_number_allowed, 
    log_call,
    reset_task_attempts,
    complete_task,
)

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Load auth token for request validation
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
DEBUG_MODE = os.getenv("FLASK_DEBUG", "false").lower() == "true"
DEFAULT_TIMEZONE = "Asia/Kolkata"  # IST
IST = ZoneInfo(DEFAULT_TIMEZONE)


@app.before_request
def log_request():
    """Log all incoming requests for debugging."""
    logger.info(f">>> {request.method} {request.path}")
    logger.debug(f"    Args: {dict(request.args)}")
    logger.debug(f"    Form: {dict(request.form)}")
    logger.debug(f"    Headers: X-Twilio-Signature={request.headers.get('X-Twilio-Signature', 'MISSING')}")


def validate_twilio_request(f):
    """
    Decorator to validate that incoming requests genuinely originated from Twilio.
    Uses HMAC-SHA1 signature validation.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if DEBUG_MODE:
            logger.debug("Skipping Twilio validation (DEBUG_MODE=true)")
            return f(*args, **kwargs)
        
        if not TWILIO_AUTH_TOKEN:
            logger.warning("TWILIO_AUTH_TOKEN not set, skipping validation")
            return f(*args, **kwargs)
        
        validator = RequestValidator(TWILIO_AUTH_TOKEN)
        
        # Get the full URL (handle proxy scenarios)
        url = request.url
        if request.headers.get('X-Forwarded-Proto') == 'https':
            url = url.replace('http://', 'https://', 1)
        
        signature = request.headers.get('X-Twilio-Signature', '')
        
        logger.debug(f"Validating signature for URL: {url}")
        
        # Validate the request
        if validator.validate(url, request.form, signature):
            logger.debug("Signature valid")
            return f(*args, **kwargs)
        else:
            logger.error(f"Invalid Twilio signature for {request.path}")
            logger.error(f"  URL used: {url}")
            logger.error(f"  Signature: {signature}")
            abort(403)
    
    return decorated_function


# ============================================================================
# Voice Call Endpoints
# ============================================================================

@app.route("/voice", methods=["GET", "POST"])
@validate_twilio_request
def voice():
    """Initial voice prompt when call is answered."""
    task_id = request.args.get("task_id")
    logger.info(f"Voice endpoint called for task_id={task_id}")
    
    try:
        task = get_task(task_id)
        if task is None:
            logger.error(f"Task {task_id} not found in database")
            task_message = "your task"
        else:
            task_message = task["message"]
            logger.info(f"Task found: {task_message}")
        
        response = VoiceResponse()

        # Use speech recognition + DTMF fallback
        gather = Gather(
            num_digits=1,
            input="speech dtmf",
            action=f"/handle-input?task_id={task_id}",
            speech_timeout="auto",
            hints="done, yes, finished, complete, completed, no, not yet, skip, snooze",
            language="en-US",
        )

        gather.say(
            f"Reminder: {task_message}. "
            "Say done or press 1 if you completed it. "
            "Say snooze or press 2 to be reminded later.",
            voice="Polly.Joanna-Neural"
        )
        response.append(gather)

        # If no input, assume not done
        response.say("I didn't hear anything. I'll remind you again later.", voice="Polly.Joanna-Neural")

        twiml = str(response)
        logger.debug(f"Returning TwiML: {twiml[:200]}...")
        return twiml, 200, {'Content-Type': 'text/xml'}
    
    except Exception as e:
        logger.error(f"Error in /voice: {e}")
        logger.error(traceback.format_exc())
        # Return a basic error response so the call doesn't just die
        response = VoiceResponse()
        response.say("Sorry, there was an error. Please try again later.")
        return str(response), 200, {'Content-Type': 'text/xml'}


@app.route("/handle-input", methods=["POST"])
@validate_twilio_request
def handle_input():
    """Handle DTMF or speech input from the call."""
    task_id = request.args.get("task_id")
    
    # Get input (DTMF digits or speech result)
    digit = request.form.get("Digits")
    speech = request.form.get("SpeechResult", "").lower().strip()
    
    logger.info(f"Handle-input for task_id={task_id}, digit={digit}, speech='{speech}'")
    
    try:
        response = VoiceResponse()
        
        # Determine if task is complete
        task_complete = False
        
        if digit == "1":
            task_complete = True
        elif digit == "2":
            task_complete = False
        elif speech:
            # Check for completion phrases
            done_phrases = ["done", "yes", "finished", "complete", "completed", "did it", "yep", "yeah"]
            if any(phrase in speech for phrase in done_phrases):
                task_complete = True
        
        if task_complete:
            logger.info(f"Task {task_id} marked as complete")
            is_recurring, next_time, streak = complete_task(task_id)
            
            if is_recurring:
                response.say(
                    f"Great job! That's a {streak} day streak! "
                    f"I'll remind you again {next_time}.",
                    voice="Polly.Joanna-Neural"
                )
                log_call(task_id, "", "call", f"completed_recurring_streak:{streak}")
            else:
                response.say(
                    "Great job! Task marked as complete. Have a wonderful day!",
                    voice="Polly.Joanna-Neural"
                )
                log_call(task_id, "", "call", "completed_by_user")
        else:
            logger.info(f"Task {task_id} snoozed")
            # User said no or snooze - reset attempts for gentler retry schedule
            reset_task_attempts(task_id)
            response.say(
                "No problem. I'll remind you again later.",
                voice="Polly.Joanna-Neural"
            )
            log_call(task_id, "", "call", "snoozed")

        return str(response), 200, {'Content-Type': 'text/xml'}
    
    except Exception as e:
        logger.error(f"Error in /handle-input: {e}")
        logger.error(traceback.format_exc())
        response = VoiceResponse()
        response.say("Sorry, there was an error processing your response.")
        return str(response), 200, {'Content-Type': 'text/xml'}


@app.route("/amd-status", methods=["POST"])
@validate_twilio_request
def amd_status():
    """Handle Answering Machine Detection callback."""
    task_id = request.args.get("task_id")
    answered_by = request.form.get("AnsweredBy", "unknown")
    call_sid = request.form.get("CallSid")
    
    logger.info(f"AMD status for task_id={task_id}: {answered_by}")
    
    # Log the AMD result
    log_call(task_id, "", "call", f"amd:{answered_by}", call_sid)
    
    if answered_by in ("machine_end_beep", "machine_end_silence", "machine_end_other"):
        logger.info(f"Task {task_id}: Voicemail detected, will retry")
    elif answered_by == "human":
        logger.info(f"Task {task_id}: Human answered")
    else:
        logger.info(f"Task {task_id}: AMD result: {answered_by}")
    
    return "", 200


@app.route("/call-status", methods=["POST"])
@validate_twilio_request
def call_status():
    """Handle call status callbacks (completed, busy, no-answer, failed)."""
    task_id = request.args.get("task_id")
    status = request.form.get("CallStatus")
    call_sid = request.form.get("CallSid")
    error_code = request.form.get("ErrorCode")
    error_message = request.form.get("ErrorMessage")
    
    logger.info(f"Call status for task_id={task_id}: {status}")
    if error_code:
        logger.error(f"  Error {error_code}: {error_message}")
    
    log_call(task_id, "", "call", f"status:{status}", call_sid)
    
    return "", 200


# ============================================================================
# WhatsApp Task Creation
# ============================================================================

def parse_reminder(message_body):
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
    # e.g. "pay rent monthly", "pay rent monthly on 1st", "clean daily", "review weekly"
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
                from datetime import timedelta
                import calendar
                
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
            from datetime import timedelta
            import calendar
            
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
        # "remind me to X on DATE at TIME" — specific date
        r"remind(?:\s+me)?\s+to\s+(.+?)\s+on\s+(.+?\s+at\s+.+)",
        # "X on DATE at TIME" — specific date without remind me
        r"(.+?)\s+on\s+((?:the\s+)?\d{1,2}(?:st|nd|rd|th)?(?:\s+\w+)?\s+at\s+.+)",
        r"(.+?)\s+on\s+(\w+\s+\d{1,2}(?:st|nd|rd|th)?\s+at\s+.+)",
        # "remind me to X at/in/on Y"
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
    
    # Check if this is an all-day task (no time specified)
    # All-day if no time-like patterns found in original message
    time_indicators = [
        r'\bat\s+\d',          # "at 3pm", "at 15:00"
        r'\bin\s+\d+\s+\w+',   # "in 30 minutes"
        r'\b\d{1,2}:\d{2}',    # "15:00", "3:30"
        r'\b\d{1,2}\s*(?:am|pm)\b',  # "3pm", "3 pm"
        r'\btomorrow\b',       # implies a specific time context
        r'\btonight\b',
        r'\bmorning\b',
        r'\bevening\b',
        r'\bnoon\b',
        r'\bmidnight\b',
    ]
    
    has_time = any(re.search(p, message_lower) for p in time_indicators)
    
    if not has_time:
        # All-day task: schedule for 8am tomorrow, will escalate through the day
        from datetime import timedelta
        now = datetime.now(IST)
        tomorrow_8am = (now + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
        
        # Clean up task text
        task = re.sub(r"^(remind\s+me\s+to|reminder[:\s]+)", "", message_lower).strip()
        task = re.sub(r'\s+', ' ', task).strip()
        
        if task:
            return {
                "task": task,
                "time": tomorrow_8am,
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
        from datetime import timedelta
        parsed_time = datetime.now(IST) + timedelta(hours=1)
    
    # Ensure we have IST timezone
    if parsed_time.tzinfo is None:
        parsed_time = parsed_time.replace(tzinfo=IST)
    
    return {"task": task, "time": parsed_time, "is_recurring": False, "is_allday": False}


@app.route("/whatsapp", methods=["POST"])
@validate_twilio_request
def whatsapp_webhook():
    """
    Handle incoming WhatsApp messages for task creation.
    Only processes messages from allowed phone numbers.
    """
    sender = request.form.get("From", "")  # e.g., "whatsapp:+14017122661"
    body = request.form.get("Body", "").strip()
    profile_name = request.form.get("ProfileName", "")
    
    logger.info(f"WhatsApp from {sender} ({profile_name}): {body}")
    
    response = MessagingResponse()
    
    # Extract phone number (remove whatsapp: prefix)
    phone = sender.replace("whatsapp:", "").strip()
    
    # Check allowlist
    if not is_number_allowed(phone):
        logger.warning(f"Rejected message from non-allowed number: {phone}")
        # Don't respond to non-allowed numbers (silent reject)
        return str(response), 200, {'Content-Type': 'text/xml'}
    
    try:
        # Handle special commands
        body_lower = body.lower()
        
        if body_lower in ("help", "?"):
            response.message(
                "*DidYou Reminder Bot*\n\n"
                
                "*One-time reminders:*\n"
                "• _remind me to drink water at 3pm_\n"
                "• _call mom tomorrow at 9am_\n"
                "• _buy groceries in 30 minutes_\n"
                "• _pay rent on May 15th at 10am_\n"
                "• _doctor on the 25th at 2pm_\n\n"
                
                "*Recurring reminders:*\n"
                "• _exercise daily at 7am_\n"
                "• _standup weekdays at 10am_\n"
                "• _review weekly at 6pm_\n"
                "• _pay rent monthly at 10am_\n"
                "• _pay rent monthly on 1st at 10am_\n\n"
                
                "*All-day reminders:*\n"
                "No time = SMS 8am, WhatsApp noon, calls 6pm\n"
                "• _submit report_\n"
                "• _exercise daily_\n"
                "• _pay rent monthly on 1st_\n\n"
                
                "*Commands:*\n"
                "• *list* — see pending tasks\n"
                "• *clear 123* — clear by ID\n"
                "• *clear all* — clear all tasks\n"
                "• *help* — show this"
            )
            return str(response), 200, {'Content-Type': 'text/xml'}
        
        if body_lower in ("list", "tasks", "pending"):
            from db import get_pending_tasks
            tasks = get_pending_tasks()
            user_tasks = [t for t in tasks if t["phone"] == phone]
            
            if not user_tasks:
                response.message("You have no pending tasks.")
            else:
                task_list = "\n".join([
                    f"• [{t['id']}] {t['message']} (due {t['time'][:16]})"
                    for t in user_tasks[:10]  # Limit to 10
                ])
                response.message(f"Your pending tasks:\n{task_list}\n\nTo clear: _clear 123_ or _clear all_")
            
            return str(response), 200, {'Content-Type': 'text/xml'}
        
        # Handle clear commands
        if body_lower == "clear all":
            from db import clear_all_tasks
            count = clear_all_tasks(phone)
            if count > 0:
                response.message(f"Cleared {count} task{'s' if count != 1 else ''}.")
                logger.info(f"Cleared all {count} tasks for {phone}")
            else:
                response.message("You have no pending tasks to clear.")
            return str(response), 200, {'Content-Type': 'text/xml'}
        
        clear_match = re.match(r'^clear\s+(\d+)$', body_lower)
        if clear_match:
            from db import clear_task
            task_id = int(clear_match.group(1))
            if clear_task(task_id, phone):
                response.message(f"Task {task_id} cleared.")
                logger.info(f"Cleared task {task_id} for {phone}")
            else:
                response.message(f"Task {task_id} not found or doesn't belong to you.")
            return str(response), 200, {'Content-Type': 'text/xml'}
        
        # Parse as a new task
        parsed = parse_reminder(body)
        logger.debug(f"Parsed reminder: {parsed}")
        
        if parsed is None:
            response.message(
                "I couldn't understand that. Try something like:\n"
                "remind me to drink water at 3pm\n"
                "exercise daily at 7am"
            )
            return str(response), 200, {'Content-Type': 'text/xml'}
        
        # Create the task
        is_recurring = parsed.get("is_recurring", False)
        is_allday = parsed.get("is_allday", False)
        task_id = add_task(
            message=parsed["task"],
            phone=phone,
            time=parsed["time"].isoformat(),
            is_recurring=is_recurring,
            recurrence_type=parsed.get("recurrence_type"),
            recurrence_time=parsed.get("recurrence_time"),
            recurrence_day=parsed.get("recurrence_day"),
            is_allday=is_allday,
        )
        
        if task_id is None:
            logger.info(f"Duplicate task rejected: {parsed['task']}")
            response.message(
                f"You already have a reminder for '{parsed['task']}'. "
                "Complete it first or use different wording."
            )
        else:
            time_str = parsed["time"].strftime("%b %d at %I:%M %p")
            if is_recurring:
                rec_type = parsed.get("recurrence_type", "daily")
                logger.info(f"Created recurring task {task_id}: '{parsed['task']}' {rec_type} at {parsed.get('recurrence_time')}")
                response.message(
                    f"Got it! I'll remind you to '{parsed['task']}' {rec_type} starting {time_str}. "
                    "I'll track your streak!"
                )
            elif is_allday:
                logger.info(f"Created all-day task {task_id}: '{parsed['task']}'")
                response.message(
                    f"Got it! I'll remind you to '{parsed['task']}' tomorrow. "
                    "I'll SMS you in the morning, WhatsApp at noon, "
                    "then call you in the evening if it's not done!"
                )
            else:
                logger.info(f"Created task {task_id}: '{parsed['task']}' for {time_str}")
                response.message(
                    f"Got it! I'll remind you to '{parsed['task']}' on {time_str}. "
                    "I'll keep calling until you confirm it's done!"
                )
            log_call(task_id, phone, "whatsapp", "task_created")
        
        return str(response), 200, {'Content-Type': 'text/xml'}
    
    except Exception as e:
        logger.error(f"Error in /whatsapp: {e}")
        logger.error(traceback.format_exc())
        response.message("Sorry, something went wrong. Please try again.")
        return str(response), 200, {'Content-Type': 'text/xml'}


@app.route("/message-status", methods=["POST"])
@validate_twilio_request
def message_status():
    """Handle SMS/WhatsApp delivery status callbacks."""
    message_sid = request.form.get("MessageSid")
    status = request.form.get("MessageStatus")
    
    logger.info(f"Message {message_sid}: {status}")
    return "", 200


# ============================================================================
# Health Check
# ============================================================================

@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint (no auth required)."""
    return {"status": "ok", "timestamp": datetime.now().isoformat()}, 200


@app.errorhandler(Exception)
def handle_exception(e):
    """Global error handler."""
    logger.error(f"Unhandled exception: {e}")
    logger.error(traceback.format_exc())
    return {"error": "Internal server error"}, 500


if __name__ == "__main__":
    logger.info("Starting DidYou server on port 5000")
    app.run(host="0.0.0.0", port=5000, debug=DEBUG_MODE)
