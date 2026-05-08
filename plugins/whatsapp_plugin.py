"""
GREEN-API WhatsApp plugin - refactored from whatsapp_receiver.py into plugin interface.
"""
import logging
import re
import time
from threading import Thread
from typing import Optional

import requests

from config import Config
from db import (
    add_task,
    clear_all_tasks,
    clear_task,
    complete_task,
    ensure_user,
    get_user_tasks,
    log_call,
)
from parser import parse_reminder
from plugins import PlatformPlugin

logger = logging.getLogger(__name__)

POLL_TIMEOUT = 5  # seconds to wait for new notification


# pf:api_contract:whatsapp.plugin_class WhatsAppPlugin implements PlatformPlugin
class WhatsAppPlugin(PlatformPlugin):
    """GREEN-API WhatsApp plugin - polling + send."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._api_id = config.GREEN_API_ID
        self._api_token = config.GREEN_API_TOKEN
        self._api_url = config.GREEN_API_URL
        self._running = False
        self._thread: Optional[Thread] = None

    @property
    def _base_url(self) -> str:
        return f"{self._api_url}/waInstance{self._api_id}"

    @property
    def name(self) -> str:
        return "whatsapp"

    @property
    def channel_name(self) -> str:
        return "whatsapp"

    @property
    def is_running(self) -> bool:
        return self._running

    # pf:requires:whatsapp.requires_creds GREEN_API_ID and GREEN_API_TOKEN must be set
    # pf:never:whatsapp.never_start_without_creds NEVER attempt poll without valid creds
    def start(self) -> None:
        """Start polling. Raises if creds are missing."""
        if not self._api_id or not self._api_token:
            raise ValueError("GREEN_API_ID and GREEN_API_TOKEN must be set")
        self._running = True
        self._thread = Thread(target=self._poll_loop, daemon=True, name="whatsapp-poller")
        self._thread.start()
        logger.info("WhatsApp plugin started (GREEN-API polling)")

    def stop(self) -> None:
        """Stop polling."""
        self._running = False
        logger.info("WhatsApp plugin stopped")

    # pf:ensures:whatsapp.send_converts_target Converts target phone to GREEN-API chatId format
    def send_message(self, target: str, message: str, **kwargs) -> Optional[str]:
        """Send WhatsApp message. Target is phone like +91XXX, converted to GREEN-API format."""
        chat_id = self._phone_to_chat_id(target)
        try:
            resp = requests.post(
                f"{self._base_url}/sendMessage/{self._api_token}",
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

    # pf:ensures:whatsapp.same_behavior Identical behavior to original whatsapp_receiver.py
    # pf:never:whatsapp.never_process_own_messages Only process incomingMessageReceived
    # pf:never:whatsapp.never_leak_api_token NEVER log GREEN_API_TOKEN value
    def _poll_loop(self) -> None:
        """Main polling loop - fetches notifications from GREEN-API queue."""
        logger.info("WhatsApp poll loop started")

        while self._running:
            try:
                receipt_id, body = self._receive_notification()

                if receipt_id is None:
                    continue

                # Always delete the notification after receiving
                self._delete_notification(receipt_id)

                if body is None:
                    continue

                # Only process incoming text messages
                type_webhook = body.get("typeWebhook")
                if type_webhook != "incomingMessageReceived":
                    continue

                message_data = body.get("messageData", {})
                type_message = message_data.get("typeMessage")
                if type_message != "textMessage":
                    continue

                sender_data = body.get("senderData", {})
                sender_chat_id = sender_data.get("chatId", "")
                sender_name = sender_data.get("senderName", "")
                text = message_data.get("textMessageData", {}).get("textMessage", "")

                if not text or not sender_chat_id:
                    continue

                self._handle_message(sender_chat_id, sender_name, text)

            except Exception as e:
                logger.error(f"WhatsApp poll loop error: {e}")
                time.sleep(5)

    def _receive_notification(self):
        """Fetch one notification from GREEN-API queue."""
        try:
            resp = requests.get(
                f"{self._base_url}/receiveNotification/{self._api_token}",
                params={"receiveTimeout": POLL_TIMEOUT},
                timeout=POLL_TIMEOUT + 5,
            )
            resp.raise_for_status()
            data = resp.json()
            if data is None:
                return None, None
            return data.get("receiptId"), data.get("body")
        except Exception as e:
            logger.error(f"Error polling GREEN-API: {e}")
            return None, None

    def _delete_notification(self, receipt_id: str) -> None:
        """Delete a processed notification from GREEN-API queue."""
        try:
            resp = requests.delete(
                f"{self._base_url}/deleteNotification/{self._api_token}/{receipt_id}",
                timeout=10,
            )
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"Error deleting notification {receipt_id}: {e}")

    # pf:ensures:whatsapp.chat_id_to_user_id Converts GREEN-API chatId to user_id format
    @staticmethod
    def _chat_id_to_user_id(chat_id: str) -> str:
        """Convert GREEN-API chat ID to user_id: '918296552126@c.us' -> '+918296552126'."""
        return "+" + chat_id.replace("@c.us", "").replace("@g.us", "")

    @staticmethod
    def _phone_to_chat_id(phone: str) -> str:
        """Convert E.164 phone to GREEN-API chatId: '+918296552126' -> '918296552126@c.us'."""
        return phone.replace("+", "").replace(" ", "").strip() + "@c.us"

    def _handle_message(self, sender_chat_id: str, sender_name: str, text: str) -> None:
        """Process an incoming WhatsApp message."""
        user_id = self._chat_id_to_user_id(sender_chat_id)
        body = text.strip()
        body_lower = body.lower()

        logger.info(f"WhatsApp from {user_id} ({sender_name}): {body}")

        # Auto-register user
        ensure_user(user_id, sender_name, "whatsapp")

        try:
            # Help
            if body_lower in ("help", "?"):
                self.send_message(user_id,
                    "*DidYou Reminder Bot*\n\n"
                    "*One-time reminders:*\n"
                    "- _drink water at 3pm_\n"
                    "- _call mom tomorrow at 9am_\n\n"
                    "*All-day (no time = escalates):*\n"
                    "SMS 8am -> Telegram noon -> calls 6pm\n"
                    "- _submit report tomorrow_\n\n"
                    "*Recurring:*\n"
                    "- _exercise daily at 7am_\n"
                    "- _standup weekdays at 10am_\n"
                    "- _pay rent monthly on 1st at 10am_\n\n"
                    "*Commands:*\n"
                    "- *list* - see pending tasks\n"
                    "- *clear 123* - clear by ID\n"
                    "- *clear all* - clear all tasks"
                )
                return

            # List
            if body_lower in ("list", "tasks", "pending"):
                tasks = get_user_tasks(user_id)
                if not tasks:
                    self.send_message(user_id, "You have no pending tasks.")
                else:
                    task_list = "\n".join([
                        f"- [{t['id']}] {t['message']} (due {t['time'][:16]})"
                        for t in tasks[:10]
                    ])
                    self.send_message(user_id,
                        f"Your pending tasks:\n{task_list}\n\nTo clear: _clear 123_ or _clear all_"
                    )
                return

            # Clear all
            if body_lower == "clear all":
                count = clear_all_tasks(user_id)
                if count > 0:
                    self.send_message(user_id, f"Cleared {count} task{'s' if count != 1 else ''}.")
                else:
                    self.send_message(user_id, "No pending tasks to clear.")
                return

            # Clear specific
            clear_match = re.match(r'^clear\s+(\d+)$', body_lower)
            if clear_match:
                task_id = int(clear_match.group(1))
                if clear_task(task_id, user_id):
                    self.send_message(user_id, f"Task {task_id} cleared.")
                else:
                    self.send_message(user_id, f"Task {task_id} not found or doesn't belong to you.")
                return

            # Done
            done_match = re.match(r'^done\s+(\d+)$', body_lower)
            if done_match:
                task_id = int(done_match.group(1))
                is_recurring, next_time, streak = complete_task(task_id)
                if is_recurring:
                    self.send_message(user_id,
                        f"Task {task_id} completed! Streak: {streak}\nNext: {next_time}"
                    )
                else:
                    self.send_message(user_id, f"Task {task_id} marked done!")
                return

            # Parse as new task
            parsed = parse_reminder(body)
            if parsed is None:
                self.send_message(user_id,
                    "I couldn't understand that. Try:\n"
                    "remind me to drink water at 3pm\n"
                    "exercise daily at 7am"
                )
                return

            # Create the task
            is_recurring = parsed.get("is_recurring", False)
            is_allday = parsed.get("is_allday", False)
            task_id = add_task(
                message=parsed["task"],
                user_id=user_id,
                time=parsed["time"].isoformat(),
                is_recurring=is_recurring,
                recurrence_type=parsed.get("recurrence_type"),
                recurrence_time=parsed.get("recurrence_time"),
                recurrence_day=parsed.get("recurrence_day"),
                is_allday=is_allday,
            )

            if task_id is None:
                self.send_message(user_id,
                    f"You already have a reminder for '{parsed['task']}'. "
                    "Complete it first or use different wording."
                )
            else:
                time_str = parsed["time"].strftime("%b %d at %I:%M %p")
                if is_recurring:
                    rec_type = parsed.get("recurrence_type", "daily")
                    self.send_message(user_id,
                        f"Got it! I'll remind you to '{parsed['task']}' {rec_type} "
                        f"starting {time_str}. I'll track your streak!"
                    )
                elif is_allday:
                    self.send_message(user_id,
                        f"Got it! I'll remind you to '{parsed['task']}' on {time_str[:6]}. "
                        "SMS in the morning, Telegram at noon, then calls in the evening!"
                    )
                else:
                    self.send_message(user_id,
                        f"Got it! I'll remind you to '{parsed['task']}' on {time_str}. "
                        "I'll keep calling until you confirm it's done!"
                    )
                log_call(task_id, user_id, "whatsapp", "task_created")

        except Exception as e:
            logger.error(f"Error handling WhatsApp message: {e}", exc_info=True)
            self.send_message(user_id, "Sorry, something went wrong. Please try again.")
