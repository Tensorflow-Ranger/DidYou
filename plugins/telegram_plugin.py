"""
Telegram Bot API plugin - getUpdates long-polling + sendMessage.
Uses raw requests (no python-telegram-bot dependency).
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
    update_user_phone,
)
from parser import parse_reminder
from plugins import PlatformPlugin

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


def valid_e164(phone: str) -> bool:
    """Validate phone number is E.164 format: + followed by 7-15 digits."""
    return bool(re.match(r'^\+\d{7,15}$', phone))


# pf:api_contract:telegram.plugin_class TelegramPlugin implements PlatformPlugin
class TelegramPlugin(PlatformPlugin):
    """Telegram Bot API plugin using getUpdates long-polling."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._token = config.TELEGRAM_BOT_TOKEN
        self._running = False
        self._offset = 0
        self._thread: Optional[Thread] = None

    @property
    def name(self) -> str:
        return "telegram"

    @property
    def channel_name(self) -> str:
        return "telegram"

    @property
    def is_running(self) -> bool:
        return self._running

    # pf:requires:telegram.requires_token TELEGRAM_BOT_TOKEN must be set and non-empty
    # pf:ensures:telegram.daemon_thread poll_loop runs in daemon thread named 'telegram-poller'
    def start(self) -> None:
        """Start long-polling in a daemon thread. Raises if token is empty."""
        if not self._token:
            raise ValueError("TELEGRAM_BOT_TOKEN is not set")
        self._running = True
        self._thread = Thread(target=self._poll_loop, daemon=True, name="telegram-poller")
        self._thread.start()
        logger.info("Telegram plugin started (long-polling)")

    def stop(self) -> None:
        """Stop polling."""
        self._running = False
        logger.info("Telegram plugin stopped")

    # pf:ensures:telegram.send_message_impl Posts to /sendMessage with chat_id, text, parse_mode
    def send_message(self, target: str, message: str, **kwargs) -> Optional[str]:
        """Send a message via Telegram Bot API. Returns message_id or None."""
        try:
            url = TELEGRAM_API.format(token=self._token, method="sendMessage")
            resp = requests.post(url, json={
                "chat_id": target,
                "text": message,
                "parse_mode": "Markdown",
            }, timeout=10)
            # Retry without parse_mode if Markdown causes 400
            if resp.status_code == 400:
                resp = requests.post(url, json={
                    "chat_id": target,
                    "text": message,
                }, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                return str(data.get("result", {}).get("message_id", ""))
            else:
                logger.error(f"Telegram sendMessage failed: {resp.status_code}")
                return None
        except Exception as e:
            logger.error(f"Telegram sendMessage error: {e}")
            return None

    # pf:ensures:telegram.offset_tracking Maintains offset to prevent duplicate processing
    # pf:never:telegram.never_process_duplicate NEVER process update with update_id <= offset
    # pf:ensures:telegram.long_poll_timeout Uses timeout=30 in getUpdates
    # pf:never:telegram.never_crash_on_unknown_update NEVER crash on unexpected update types
    # pf:never:telegram.never_block_poll NEVER let message handling block poll >5s
    # pf:never:telegram.never_log_token NEVER log the bot token
    def _poll_loop(self) -> None:
        """Main polling loop - getUpdates with long-polling."""
        logger.info("Telegram poll loop started")

        while self._running:
            try:
                url = TELEGRAM_API.format(token=self._token, method="getUpdates")
                resp = requests.get(url, params={
                    "offset": self._offset,
                    "timeout": 30,
                }, timeout=35)

                if resp.status_code == 401:
                    logger.critical("Telegram token invalid (401). Stopping plugin.")
                    self._running = False
                    return

                if resp.status_code == 409:
                    logger.error("Telegram 409 Conflict - another instance running. Retrying in 10s.")
                    time.sleep(10)
                    continue

                if resp.status_code == 429:
                    retry_after = resp.json().get("parameters", {}).get("retry_after", 30)
                    logger.warning(f"Telegram rate limited. Sleeping {retry_after}s.")
                    time.sleep(retry_after)
                    continue

                if resp.status_code != 200:
                    logger.error(f"Telegram getUpdates error: {resp.status_code}")
                    time.sleep(5)
                    continue

                data = resp.json()
                updates = data.get("result", [])

                for update in updates:
                    update_id = update.get("update_id", 0)
                    # Advance offset immediately to prevent reprocessing
                    self._offset = update_id + 1

                    # Only process updates with a message containing text
                    message = update.get("message")
                    if message is None:
                        continue
                    text = message.get("text")
                    if not text:
                        continue

                    chat_id = str(message.get("chat", {}).get("id", ""))
                    first_name = message.get("from", {}).get("first_name", "User")

                    if chat_id:
                        try:
                            self._handle_message(chat_id, first_name, text.strip())
                        except Exception as e:
                            logger.error(f"Error handling message from {chat_id}: {e}")

            except requests.exceptions.Timeout:
                continue  # Normal for long-polling
            except requests.exceptions.ConnectionError:
                logger.error("Telegram connection error. Retrying in 5s.")
                time.sleep(5)
            except Exception as e:
                logger.error(f"Telegram poll loop error: {e}")
                time.sleep(5)

    # pf:ensures:telegram.start_command /start calls ensure_user and replies welcome
    # pf:ensures:telegram.setphone_command /setphone validates E.164 and stores phone
    # pf:ensures:telegram.auto_register Auto-register user before processing any message
    def _handle_message(self, chat_id: str, name: str, text: str) -> None:
        """Process an incoming Telegram message."""
        text_lower = text.lower()

        # Auto-register user on any message
        ensure_user(chat_id, name, "telegram")

        # Command routing
        if text_lower == "/start":
            self.send_message(chat_id,
                "Welcome to *DidYou*! I'll remind you to complete tasks.\n\n"
                "Just send me a reminder like:\n"
                "- _drink water at 3pm_\n"
                "- _exercise daily at 7am_\n"
                "- _pay rent monthly on 1st at 10am_\n\n"
                "Commands:\n"
                "/help - Show this help\n"
                "/list - Show pending tasks\n"
                "/done N - Mark task N done\n"
                "/clear N - Cancel task N\n"
                "/clear all - Cancel all tasks\n"
                "/setphone +XXXX - Set phone for SMS/calls"
            )
            return

        if text_lower == "/help":
            self.send_message(chat_id,
                "*DidYou Reminder Bot*\n\n"
                "*One-time reminders:*\n"
                "- _drink water at 3pm_\n"
                "- _call mom tomorrow at 9am_\n\n"
                "*All-day (no time = escalates):*\n"
                "SMS 8am -> Telegram noon -> Call 6pm\n"
                "- _submit report tomorrow_\n\n"
                "*Recurring:*\n"
                "- _exercise daily at 7am_\n"
                "- _standup weekdays at 10am_\n"
                "- _pay rent monthly on 1st_\n\n"
                "*Commands:*\n"
                "/list - pending tasks\n"
                "/done N - mark done\n"
                "/clear N - cancel task\n"
                "/clear all - cancel all\n"
                "/setphone +XX - for SMS/calls"
            )
            return

        # /setphone
        if text_lower.startswith("/setphone"):
            parts = text.split(maxsplit=1)
            if len(parts) < 2 or not parts[1].strip():
                self.send_message(chat_id, "Usage: /setphone +918296552126")
                return
            phone = parts[1].strip()
            if not valid_e164(phone):
                self.send_message(chat_id,
                    "Invalid phone format. Use E.164: + followed by 7-15 digits.\n"
                    "Example: /setphone +918296552126"
                )
                return
            update_user_phone(chat_id, phone)
            self.send_message(chat_id, f"Phone saved (ending {phone[-4:]}). SMS/call escalation now available.")
            return

        # /list
        if text_lower in ("/list", "list", "tasks"):
            tasks = get_user_tasks(chat_id)
            if not tasks:
                self.send_message(chat_id, "You have no pending tasks.")
            else:
                task_list = "\n".join([
                    f"- [{t['id']}] {t['message']} (due {t['time'][:16]})"
                    for t in tasks[:10]
                ])
                self.send_message(chat_id,
                    f"Your pending tasks:\n{task_list}\n\n"
                    "/done N - mark done\n/clear N - cancel"
                )
            return

        # /clear all
        if text_lower in ("/clear all", "clear all"):
            count = clear_all_tasks(chat_id)
            if count > 0:
                self.send_message(chat_id, f"Cleared {count} task{'s' if count != 1 else ''}.")
            else:
                self.send_message(chat_id, "No pending tasks to clear.")
            return

        # /clear N
        clear_match = re.match(r'^/?(clear|cancel)\s+(\d+)$', text_lower)
        if clear_match:
            task_id = int(clear_match.group(2))
            if clear_task(task_id, chat_id):
                self.send_message(chat_id, f"Task {task_id} cleared.")
            else:
                self.send_message(chat_id, f"Task {task_id} not found or doesn't belong to you.")
            return

        # /done N or just "done" (completes most recent task if only one)
        done_match = re.match(r'^/?(done|complete)\s+(\d+)$', text_lower)
        if done_match:
            task_id = int(done_match.group(2))
            is_recurring, next_time, streak = complete_task(task_id)
            if is_recurring:
                self.send_message(chat_id,
                    f"Task {task_id} completed! Streak: {streak}\n"
                    f"Next: {next_time}"
                )
            else:
                self.send_message(chat_id, f"Task {task_id} marked done!")
            return

        if text_lower in ("done", "/done", "complete"):
            tasks = get_user_tasks(chat_id)
            if len(tasks) == 1:
                task_id = tasks[0]["id"]
                is_recurring, next_time, streak = complete_task(task_id)
                if is_recurring:
                    self.send_message(chat_id,
                        f"Task {task_id} completed! Streak: {streak}\nNext: {next_time}"
                    )
                else:
                    self.send_message(chat_id, f"Task {task_id} marked done!")
            elif len(tasks) == 0:
                self.send_message(chat_id, "No pending tasks.")
            else:
                task_list = "\n".join([f"- [{t['id']}] {t['message']}" for t in tasks[:10]])
                self.send_message(chat_id,
                    f"Which task? Reply /done N\n\n{task_list}"
                )
            return

        # Natural language task creation
        parsed = parse_reminder(text)
        if parsed is None:
            self.send_message(chat_id,
                "I couldn't parse that. Try:\n"
                "- _drink water at 3pm_\n"
                "- _exercise daily at 7am_\n"
                "- _submit report tomorrow_\n\n"
                "Or type /help for more examples."
            )
            return

        # Create the task
        is_recurring = parsed.get("is_recurring", False)
        is_allday = parsed.get("is_allday", False)
        task_id = add_task(
            message=parsed["task"],
            user_id=chat_id,
            time=parsed["time"].isoformat(),
            is_recurring=is_recurring,
            recurrence_type=parsed.get("recurrence_type"),
            recurrence_time=parsed.get("recurrence_time"),
            recurrence_day=parsed.get("recurrence_day"),
            is_allday=is_allday,
        )

        if task_id is None:
            self.send_message(chat_id,
                f"You already have a reminder for '{parsed['task']}'. "
                "Complete it first or use different wording."
            )
        else:
            time_str = parsed["time"].strftime("%b %d at %I:%M %p")
            if is_recurring:
                rec_type = parsed.get("recurrence_type", "daily")
                self.send_message(chat_id,
                    f"Got it! I'll remind you to '{parsed['task']}' {rec_type} "
                    f"starting {time_str}. I'll track your streak!"
                )
            elif is_allday:
                self.send_message(chat_id,
                    f"Got it! I'll remind you to '{parsed['task']}' on {time_str[:6]}. "
                    "SMS in the morning, Telegram at noon, then calls in the evening!"
                )
            else:
                self.send_message(chat_id,
                    f"Got it! I'll remind you to '{parsed['task']}' on {time_str}."
                )
            log_call(task_id, chat_id, "telegram", "task_created")
