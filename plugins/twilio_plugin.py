"""
Twilio plugin - SMS and Voice calls via Twilio API.
Webhook-based (no polling thread) - Flask handles callbacks.
"""
import logging
import re
from typing import Optional

import requests

from config import Config
from plugins import PlatformPlugin

logger = logging.getLogger(__name__)


def _valid_e164(phone: str) -> bool:
    """Validate E.164 phone format: + followed by 7-15 digits."""
    return bool(re.match(r'^\+\d{7,15}$', phone))


# pf:api_contract:twilio.plugin_class TwilioPlugin implements PlatformPlugin
class TwilioPlugin(PlatformPlugin):
    """Twilio SMS + Voice calls plugin. Webhook-based, no polling."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._sid = config.TWILIO_SID
        self._auth_token = config.TWILIO_AUTH_TOKEN
        self._phone = config.TWILIO_PHONE
        self._base_url = config.BASE_URL
        self._running = False

    @property
    def name(self) -> str:
        return "twilio"

    @property
    def channel_name(self) -> str:
        return "sms"

    @property
    def is_running(self) -> bool:
        return self._running

    # pf:requires:twilio.requires_creds TWILIO_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE must be set
    # pf:never:twilio.never_start_polling NEVER start a polling thread - Twilio is webhook-based
    # pf:never:twilio.never_log_auth_token NEVER log TWILIO_AUTH_TOKEN
    def start(self) -> None:
        """Mark plugin as active. No polling thread - Twilio pushes via webhooks."""
        if not self._sid or not self._auth_token or not self._phone:
            raise ValueError("TWILIO_SID, TWILIO_AUTH_TOKEN, and TWILIO_PHONE must be set")
        self._running = True
        logger.info("Twilio plugin started (webhook-based, no polling)")

    def stop(self) -> None:
        """Stop plugin."""
        self._running = False
        logger.info("Twilio plugin stopped")

    # pf:ensures:twilio.send_routes_by_kwargs Dispatches call or SMS based on kwargs
    # pf:ensures:twilio.call_needs_phone Validates E.164 before Twilio API call
    # pf:never:twilio.never_call_without_phone NEVER attempt call with invalid phone
    def send_message(self, target: str, message: str, **kwargs) -> Optional[str]:
        """
        Send SMS or initiate voice call.
        If kwargs has call=True, initiates voice call.
        Otherwise sends SMS.
        Target must be valid E.164 phone number.
        """
        if not _valid_e164(target):
            logger.error(f"Invalid phone for Twilio: target does not match E.164 format")
            return None

        if kwargs.get("call"):
            return self._make_call(target, kwargs.get("task_id"), message)
        else:
            return self._send_sms(target, message)

    def _send_sms(self, phone: str, message: str) -> Optional[str]:
        """Send SMS via Twilio REST API."""
        try:
            url = f"https://api.twilio.com/2010-04-01/Accounts/{self._sid}/Messages.json"
            resp = requests.post(
                url,
                data={
                    "To": phone,
                    "From": self._phone,
                    "Body": message,
                    "StatusCallback": f"{self._base_url}/message-status" if self._base_url else None,
                },
                auth=(self._sid, self._auth_token),
                timeout=10,
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                sid = data.get("sid")
                logger.info(f"SMS sent to ***{phone[-4:]}: {sid}")
                return sid
            else:
                logger.error(f"Twilio SMS failed: {resp.status_code} {resp.text[:200]}")
                return None
        except Exception as e:
            logger.error(f"Error sending SMS: {e}")
            return None

    def _make_call(self, phone: str, task_id: Optional[int], message: str) -> Optional[str]:
        """Initiate voice call via Twilio REST API."""
        try:
            url = f"https://api.twilio.com/2010-04-01/Accounts/{self._sid}/Calls.json"
            voice_url = f"{self._base_url}/voice?task_id={task_id}" if self._base_url and task_id else None
            if not voice_url:
                logger.error("Cannot make call: BASE_URL not configured")
                return None

            resp = requests.post(
                url,
                data={
                    "To": phone,
                    "From": self._phone,
                    "Url": voice_url,
                    "StatusCallback": f"{self._base_url}/call-status?task_id={task_id}",
                    "StatusCallbackEvent": "completed busy no-answer failed",
                    "MachineDetection": "DetectMessageEnd",
                    "MachineDetectionTimeout": 30,
                    "AsyncAmd": "true",
                    "AsyncAmdStatusCallback": f"{self._base_url}/amd-status?task_id={task_id}",
                },
                auth=(self._sid, self._auth_token),
                timeout=10,
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                sid = data.get("sid")
                logger.info(f"Call initiated to ***{phone[-4:]}: {sid}")
                return sid
            else:
                logger.error(f"Twilio call failed: {resp.status_code} {resp.text[:200]}")
                return None
        except Exception as e:
            logger.error(f"Error making call: {e}")
            return None


class TwilioCallPlugin(PlatformPlugin):
    """Twilio voice call channel - shares same Twilio plugin but different channel_name."""

    def __init__(self, twilio_plugin: TwilioPlugin) -> None:
        self._twilio = twilio_plugin

    @property
    def name(self) -> str:
        return "twilio_call"

    @property
    def channel_name(self) -> str:
        return "call"

    @property
    def is_running(self) -> bool:
        return self._twilio.is_running

    def start(self) -> None:
        """No-op - main TwilioPlugin handles start."""
        pass

    def stop(self) -> None:
        """No-op - main TwilioPlugin handles stop."""
        pass

    def send_message(self, target: str, message: str, **kwargs) -> Optional[str]:
        """Route to Twilio plugin with call=True."""
        kwargs.pop("call", None)  # Remove if passed by scheduler to avoid duplicate
        return self._twilio.send_message(target, message, call=True, **kwargs)
