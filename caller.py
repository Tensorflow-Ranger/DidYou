from twilio.rest import Client
from dotenv import load_dotenv
import os

load_dotenv()

client = Client(
    os.getenv("TWILIO_SID"),
    os.getenv("TWILIO_AUTH_TOKEN")
)

TWILIO_PHONE = os.getenv("TWILIO_PHONE")
TWILIO_WHATSAPP = os.getenv("TWILIO_WHATSAPP", f"whatsapp:{TWILIO_PHONE}")
BASE_URL = os.getenv("BASE_URL")


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
        print(f"Error making call to {phone}: {e}")
        return None


def send_sms(phone, message):
    """
    Send an SMS message.
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
        print(f"Error sending SMS to {phone}: {e}")
        return None


def send_whatsapp(phone, message):
    """
    Send a WhatsApp message.
    Note: Outbound WhatsApp outside 24h session requires pre-approved templates.
    Returns the message SID or None on error.
    """
    # Ensure proper format
    if not phone.startswith("whatsapp:"):
        phone = f"whatsapp:{phone}"
    
    try:
        msg = client.messages.create(
            to=phone,
            from_=TWILIO_WHATSAPP,
            body=message,
            status_callback=f"{BASE_URL}/message-status",
        )
        return msg.sid
    except Exception as e:
        print(f"Error sending WhatsApp to {phone}: {e}")
        return None
