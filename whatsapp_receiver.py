"""
GREEN-API WhatsApp polling receiver.
Polls for incoming WhatsApp messages and processes them as task commands.
Replaces the Twilio /whatsapp webhook endpoint.
"""
import requests
import logging
import re
import os
import time
from threading import Thread

from caller import GREEN_API_URL, GREEN_API_TOKEN, send_whatsapp, phone_to_chat_id
from db import (
    add_task,
    get_task,
    get_pending_tasks,
    is_number_allowed,
    log_call,
    clear_task,
    clear_all_tasks,
)
from server import parse_reminder

logger = logging.getLogger(__name__)

POLL_TIMEOUT = 5  # seconds to wait for new notification


def chat_id_to_phone(chat_id):
    """Convert GREEN-API chat ID to E.164 phone number."""
    # "918296552126@c.us" -> "+918296552126"
    return "+" + chat_id.replace("@c.us", "").replace("@g.us", "")


def receive_notification():
    """Fetch one notification from GREEN-API queue. Returns (receipt_id, body) or (None, None)."""
    try:
        resp = requests.get(
            f"{GREEN_API_URL}/receiveNotification/{GREEN_API_TOKEN}",
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


def delete_notification(receipt_id):
    """Delete a processed notification from GREEN-API queue."""
    try:
        resp = requests.delete(
            f"{GREEN_API_URL}/deleteNotification/{GREEN_API_TOKEN}/{receipt_id}",
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"Error deleting notification {receipt_id}: {e}")


def reply(chat_id, message):
    """Send a reply to a chat."""
    phone = chat_id_to_phone(chat_id)
    send_whatsapp(phone, message)


def handle_message(sender_chat_id, sender_name, text):
    """Process an incoming WhatsApp message — same logic as the old /whatsapp endpoint."""
    phone = chat_id_to_phone(sender_chat_id)
    body = text.strip()
    body_lower = body.lower()

    logger.info(f"WhatsApp from {phone} ({sender_name}): {body}")

    # Check allowlist
    if not is_number_allowed(phone):
        logger.warning(f"Rejected message from non-allowed number: {phone}")
        return

    try:
        # Help
        if body_lower in ("help", "?"):
            reply(sender_chat_id,
                "*DidYou Reminder Bot*\n\n"
                "*One-time reminders:*\n"
                "• _drink water at 3pm_\n"
                "• _call mom tomorrow at 9am_\n"
                "• _pay rent on May 15th at 10am_\n\n"
                "*All-day (no time = escalates):*\n"
                "SMS 8am → WhatsApp noon → calls 6pm\n"
                "• _submit report_\n"
                "• _pay rent on May 15th_\n"
                "• _clean on the 25th_\n\n"
                "*Recurring:*\n"
                "• _exercise daily at 7am_\n"
                "• _standup weekdays at 10am_\n"
                "• _pay rent monthly on 1st at 10am_\n"
                "• _pay rent monthly on 1st_ (all-day)\n\n"
                "*Commands:*\n"
                "• *list* — see pending tasks\n"
                "• *clear 123* — clear by ID\n"
                "• *clear all* — clear all tasks"
            )
            return

        # List
        if body_lower in ("list", "tasks", "pending"):
            tasks = get_pending_tasks()
            user_tasks = [t for t in tasks if t["phone"] == phone]

            if not user_tasks:
                reply(sender_chat_id, "You have no pending tasks.")
            else:
                task_list = "\n".join([
                    f"• [{t['id']}] {t['message']} (due {t['time'][:16]})"
                    for t in user_tasks[:10]
                ])
                reply(sender_chat_id,
                    f"Your pending tasks:\n{task_list}\n\nTo clear: _clear 123_ or _clear all_"
                )
            return

        # Clear all
        if body_lower == "clear all":
            count = clear_all_tasks(phone)
            if count > 0:
                reply(sender_chat_id, f"Cleared {count} task{'s' if count != 1 else ''}.")
                logger.info(f"Cleared all {count} tasks for {phone}")
            else:
                reply(sender_chat_id, "You have no pending tasks to clear.")
            return

        # Clear specific
        clear_match = re.match(r'^clear\s+(\d+)$', body_lower)
        if clear_match:
            task_id = int(clear_match.group(1))
            if clear_task(task_id, phone):
                reply(sender_chat_id, f"Task {task_id} cleared.")
                logger.info(f"Cleared task {task_id} for {phone}")
            else:
                reply(sender_chat_id, f"Task {task_id} not found or doesn't belong to you.")
            return

        # Parse as new task
        parsed = parse_reminder(body)
        logger.debug(f"Parsed reminder: {parsed}")

        if parsed is None:
            reply(sender_chat_id,
                "I couldn't understand that. Try something like:\n"
                "remind me to drink water at 3pm\n"
                "exercise daily at 7am"
            )
            return

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
            reply(sender_chat_id,
                f"You already have a reminder for '{parsed['task']}'. "
                "Complete it first or use different wording."
            )
        else:
            time_str = parsed["time"].strftime("%b %d at %I:%M %p")
            if is_recurring:
                rec_type = parsed.get("recurrence_type", "daily")
                logger.info(f"Created recurring task {task_id}: '{parsed['task']}' {rec_type}")
                reply(sender_chat_id,
                    f"Got it! I'll remind you to '{parsed['task']}' {rec_type} starting {time_str}. "
                    "I'll track your streak!"
                )
            elif is_allday:
                logger.info(f"Created all-day task {task_id}: '{parsed['task']}'")
                reply(sender_chat_id,
                    f"Got it! I'll remind you to '{parsed['task']}' on {time_str[:6]}. "
                    "I'll SMS you in the morning, WhatsApp at noon, "
                    "then call you in the evening if it's not done!"
                )
            else:
                logger.info(f"Created task {task_id}: '{parsed['task']}' for {time_str}")
                reply(sender_chat_id,
                    f"Got it! I'll remind you to '{parsed['task']}' on {time_str}. "
                    "I'll keep calling until you confirm it's done!"
                )
            log_call(task_id, phone, "whatsapp", "task_created")

    except Exception as e:
        logger.error(f"Error handling WhatsApp message: {e}", exc_info=True)
        reply(sender_chat_id, "Sorry, something went wrong. Please try again.")


def poll_loop():
    """Main polling loop — runs in a background thread."""
    logger.info("GREEN-API WhatsApp polling started")

    while True:
        try:
            receipt_id, body = receive_notification()

            if receipt_id is None:
                continue

            # Always delete the notification after receiving it
            delete_notification(receipt_id)

            # Only process incoming text messages
            if body is None:
                continue

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

            handle_message(sender_chat_id, sender_name, text)

        except Exception as e:
            logger.error(f"Error in poll loop: {e}", exc_info=True)
            time.sleep(5)


def start_polling():
    """Start the polling loop in a daemon thread."""
    thread = Thread(target=poll_loop, daemon=True, name="green-api-poller")
    thread.start()
    logger.info("GREEN-API polling thread started")
    return thread
