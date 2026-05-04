from twilio.rest import Client
from dotenv import load_dotenv
import requests
import logging
import os

load_dotenv()

logger = logging.getLogger(__name__)

# Twilio — for voice calls and SMS
client = Client(
    os.getenv("TWILIO_SID"),
    os.getenv("TWILIO_AUTH_TOKEN")
)

TWILIO_PHONE = os.getenv("TWILIO_PHONE")
BASE_URL = os.getenv("BASE_URL")

# GREEN-API — for WhatsApp
GREEN_API_ID = os.getenv("GREEN_API_ID")
GREEN_API_TOKEN = os.getenv("GREEN_API_TOKEN")
GREEN_API_URL = f"https://api.greenapi.com/waInstance{GREEN_API_ID}"


def phone_to_chat_id(phone):
    """Convert E.164 phone number to GREEN-API chat ID format."""
    # Strip +, whatsapp: prefix, spaces
    phone = phone.replace("whatsapp:", "").replace("+", "").replace(" ", "").strip()
    return f"{phone}@c.us"


def make_call(phone, task_id, task_message=None):
    """
    Make a phone call with Answering Machine Detection.
    Returns the call SID or None on error.
    """
    try:
        call = client.calls.create(
            to=phone,
            from_=TWILIO_PHONE,
            url=f"{BASE_URL}/voice?task_id={task_id}",
            status_callback=f"{BASE_URL}/call-status?task_id={task_id}",
            status_callback_event=["completed", "busy", "no-answer", "failed"],
            # Answering Machine Detection
            machine_detection="DetectMessageEnd",
            machine_detection_timeout=30,
            async_amd=True,
            async_amd_status_callback=f"{BASE_URL}/amd-status?task_id={task_id}",
        )
        return call.sid
    except Exception as e:
        logger.error(f"Error making call to {phone}: {e}")
        return None


def send_sms(phone, message):
    """
    Send an SMS message via Twilio.
    Returns the message SID or None on error.
    """
    try:
        msg = client.messages.create(
            to=phone,
            from_=TWILIO_PHONE,
            body=message,
            status_callback=f"{BASE_URL}/message-status",
        )
        return msg.sid
    except Exception as e:
        logger.error(f"Error sending SMS to {phone}: {e}")
        return None


def send_whatsapp(phone, message):
    """
    Send a WhatsApp message via GREEN-API.
    Returns the message ID or None on error.
    """
    chat_id = phone_to_chat_id(phone)
    
    try:
        resp = requests.post(
            f"{GREEN_API_URL}/sendMessage/{GREEN_API_TOKEN}",
            json={"chatId": chat_id, "message": message},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        msg_id = data.get("idMessage")
        logger.info(f"WhatsApp sent to {chat_id}: {msg_id}")
        return msg_id
    except Exception as e:
        logger.error(f"Error sending WhatsApp to {chat_id}: {e}")
        return None
