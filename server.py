"""
Flask server - Twilio voice/SMS webhooks only.
Slimmed down from the original 768-line server.py.
parse_reminder moved to parser.py, WhatsApp moved to plugins/whatsapp_plugin.py.
"""
from flask import Flask, request, abort
from functools import wraps
from datetime import datetime
from zoneinfo import ZoneInfo
import logging
import traceback
import os

from config import load_config
from db import (
    get_task,
    complete_task,
    log_call,
    update_task,
)

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

_config = load_config()
TWILIO_AUTH_TOKEN = _config.TWILIO_AUTH_TOKEN
DEBUG_MODE = os.getenv("FLASK_DEBUG", "false").lower() == "true"


@app.before_request
def log_request():
    """Log all incoming requests for debugging."""
    logger.info(f">>> {request.method} {request.path}")
    logger.debug(f"    Args: {dict(request.args)}")
    logger.debug(f"    Form: {dict(request.form)}")


def validate_twilio_request(f):
    """Validate incoming requests are genuinely from Twilio (HMAC-SHA1)."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if DEBUG_MODE:
            return f(*args, **kwargs)

        if not TWILIO_AUTH_TOKEN:
            logger.warning("TWILIO_AUTH_TOKEN not set, skipping validation")
            return f(*args, **kwargs)

        try:
            from twilio.request_validator import RequestValidator
            validator = RequestValidator(TWILIO_AUTH_TOKEN)
        except ImportError:
            logger.warning("twilio package not installed, skipping validation")
            return f(*args, **kwargs)

        url = request.url
        if request.headers.get('X-Forwarded-Proto') == 'https':
            url = url.replace('http://', 'https://', 1)

        signature = request.headers.get('X-Twilio-Signature', '')

        if validator.validate(url, request.form, signature):
            return f(*args, **kwargs)
        else:
            logger.error(f"Invalid Twilio signature for {request.path}")
            abort(403)

    return decorated_function


# ============================================================================
# Voice Call Endpoints
# ============================================================================

@app.route("/voice", methods=["GET", "POST"])
@validate_twilio_request
def voice():
    """Initial voice prompt when call is answered."""
    try:
        from twilio.twiml.voice_response import VoiceResponse, Gather
    except ImportError:
        return "Twilio not installed", 500

    task_id = request.args.get("task_id")
    logger.info(f"Voice endpoint called for task_id={task_id}")

    try:
        task = get_task(int(task_id)) if task_id else None
        task_message = task["message"] if task else "your task"

        response = VoiceResponse()
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
        response.say("I didn't hear anything. I'll remind you again later.", voice="Polly.Joanna-Neural")

        return str(response), 200, {'Content-Type': 'text/xml'}

    except Exception as e:
        logger.error(f"Error in /voice: {e}")
        logger.error(traceback.format_exc())
        from twilio.twiml.voice_response import VoiceResponse as VR
        response = VR()
        response.say("Sorry, there was an error. Please try again later.")
        return str(response), 200, {'Content-Type': 'text/xml'}


@app.route("/handle-input", methods=["POST"])
@validate_twilio_request
def handle_input():
    """Handle DTMF or speech input from the call."""
    try:
        from twilio.twiml.voice_response import VoiceResponse
    except ImportError:
        return "Twilio not installed", 500

    task_id = request.args.get("task_id")
    digit = request.form.get("Digits")
    speech = request.form.get("SpeechResult", "").lower().strip()

    logger.info(f"Handle-input for task_id={task_id}, digit={digit}, speech='{speech}'")

    try:
        response = VoiceResponse()
        task_complete = False

        if digit == "1":
            task_complete = True
        elif digit == "2":
            task_complete = False
        elif speech:
            done_phrases = ["done", "yes", "finished", "complete", "completed", "did it", "yep", "yeah"]
            if any(phrase in speech for phrase in done_phrases):
                task_complete = True

        if task_complete and task_id:
            is_recurring, next_time, streak = complete_task(int(task_id))
            if is_recurring:
                response.say(
                    f"Great job! That's a {streak} day streak! I'll remind you again {next_time}.",
                    voice="Polly.Joanna-Neural"
                )
            else:
                response.say(
                    "Great job! Task marked as complete. Have a wonderful day!",
                    voice="Polly.Joanna-Neural"
                )
            log_call(int(task_id), "", "call", "completed_by_user")
        else:
            response.say("No problem. I'll remind you again later.", voice="Polly.Joanna-Neural")
            if task_id:
                log_call(int(task_id), "", "call", "snoozed")

        return str(response), 200, {'Content-Type': 'text/xml'}

    except Exception as e:
        logger.error(f"Error in /handle-input: {e}")
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
    if task_id:
        log_call(int(task_id), "", "call", f"amd:{answered_by}", call_sid)

    return "", 200


@app.route("/call-status", methods=["POST"])
@validate_twilio_request
def call_status():
    """Handle call status callbacks."""
    task_id = request.args.get("task_id")
    status = request.form.get("CallStatus")
    call_sid = request.form.get("CallSid")

    logger.info(f"Call status for task_id={task_id}: {status}")
    if task_id:
        log_call(int(task_id), "", "call", f"status:{status}", call_sid)

    return "", 200


@app.route("/message-status", methods=["POST"])
@validate_twilio_request
def message_status():
    """Handle SMS status callbacks."""
    message_sid = request.form.get("MessageSid")
    status = request.form.get("MessageStatus")
    logger.info(f"SMS status: {message_sid} -> {status}")
    return "", 200


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return {"status": "ok"}, 200
