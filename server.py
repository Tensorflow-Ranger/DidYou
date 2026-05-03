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
            update_task(task_id, "done")
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
    
    Returns dict with 'task' and 'time' or None if parsing fails.
    """
    message_lower = message_body.lower().strip()
    
    # Patterns to match
    patterns = [
        # "remind me to X at/in/on Y"
        r"remind(?:\s+me)?\s+to\s+(.+?)\s+(at|in|on|by|tomorrow|next\s+\w+)\s*(.+)?",
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
    
    return {"task": task, "time": parsed_time}


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
                "Send me a reminder like:\n"
                "• remind me to drink water at 3pm\n"
                "• call mom tomorrow at 9am\n"
                "• buy groceries in 30 minutes\n\n"
                "I'll call you until you confirm it's done!"
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
                    f"• {t['message']} (due {t['time'][:16]})"
                    for t in user_tasks[:10]  # Limit to 10
                ])
                response.message(f"Your pending tasks:\n{task_list}")
            
            return str(response), 200, {'Content-Type': 'text/xml'}
        
        # Parse as a new task
        parsed = parse_reminder(body)
        logger.debug(f"Parsed reminder: {parsed}")
        
        if parsed is None:
            response.message(
                "I couldn't understand that. Try something like:\n"
                "remind me to drink water at 3pm"
            )
            return str(response), 200, {'Content-Type': 'text/xml'}
        
        # Create the task
        task_id = add_task(
            message=parsed["task"],
            phone=phone,
            time=parsed["time"].isoformat()
        )
        
        if task_id is None:
            logger.info(f"Duplicate task rejected: {parsed['task']}")
            response.message(
                f"You already have a reminder for '{parsed['task']}'. "
                "Complete it first or use different wording."
            )
        else:
            time_str = parsed["time"].strftime("%b %d at %I:%M %p")
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
