#!/usr/bin/env python3
"""
Helper script to add tasks and manage allowed numbers.

Usage:
    # Add a one-time task
    python add_tasks.py task "Drink water" +15551234567 "2026-05-03T17:00:00"
    
    # Add a recurring daily task (time in HH:MM IST)
    python add_tasks.py daily "Exercise" +15551234567 "07:00"
    
    # Add a recurring weekdays task (Mon-Fri)
    python add_tasks.py weekdays "Standup meeting" +15551234567 "10:00"
    
    # Add a recurring weekly task
    python add_tasks.py weekly "Weekly review" +15551234567 "18:00"
    
    # Add a recurring monthly task (day of month optional, defaults to today)
    python add_tasks.py monthly "Pay rent" +15551234567 "10:00"
    python add_tasks.py monthly "Pay rent" +15551234567 "10:00" 1  # 1st of each month
    
    # Add an all-day task (no specific time - escalates: SMS 8am -> WhatsApp 12pm -> Call 6pm)
    python add_tasks.py allday "Submit report" +15551234567
    
    # Add an allowed number
    python add_tasks.py allow +15551234567 "John"
    
    # List pending tasks
    python add_tasks.py list
    
    # List allowed numbers
    python add_tasks.py allowed
"""
from db import add_task, add_allowed_number, get_pending_tasks
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import sys

IST = ZoneInfo("Asia/Kolkata")

def list_tasks():
    tasks = get_pending_tasks()
    if not tasks:
        print("No pending tasks.")
        return
    
    print(f"{'ID':<5} {'Message':<25} {'Phone':<15} {'Due':<20} {'Type':<10} {'Streak':<6}")
    print("-" * 90)
    for t in tasks:
        if t['is_allday']:
            task_type = f"allday/{t['escalation_stage']}"
        elif t['recurrence_type']:
            task_type = t['recurrence_type']
        else:
            task_type = 'once'
        streak = t['streak'] or 0
        print(f"{t['id']:<5} {t['message'][:23]:<25} {t['phone']:<15} {t['time'][:19]:<20} {task_type:<10} {streak:<6}")

def list_allowed():
    from db import cursor
    cursor.execute("SELECT * FROM allowed_numbers")
    numbers = cursor.fetchall()
    
    if not numbers:
        print("No allowed numbers.")
        return
    
    print(f"{'Phone':<15} {'Name':<20} {'Quiet Hours':<15} {'Max Calls':<10}")
    print("-" * 60)
    for n in numbers:
        quiet = f"{n['quiet_start']}-{n['quiet_end']}"
        print(f"{n['phone']:<15} {(n['name'] or '-'):<20} {quiet:<15} {n['max_daily_calls']:<10}")

def add_recurring_task(message, phone, time_str, recurrence_type, recurrence_day=None):
    """Add a recurring task with first occurrence calculated."""
    import calendar
    
    hour, minute = map(int, time_str.split(":"))
    now = datetime.now(IST)
    
    # Calculate first occurrence
    first_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    
    if recurrence_type == "monthly":
        # Default recurrence_day to today's day if not specified
        if recurrence_day is None:
            recurrence_day = now.day
        
        # If the time already passed this month for that day, go to next month
        target_this_month = now.replace(day=min(recurrence_day, calendar.monthrange(now.year, now.month)[1]),
                                        hour=hour, minute=minute, second=0, microsecond=0)
        if target_this_month > now:
            first_time = target_this_month
        else:
            year = now.year
            month = now.month + 1
            if month > 12:
                month = 1
                year += 1
            day = min(recurrence_day, calendar.monthrange(year, month)[1])
            first_time = datetime(year, month, day, hour, minute, 0, tzinfo=IST)
    else:
        # Daily, weekdays, weekly
        if first_time <= now:
            first_time += timedelta(days=1)
        
        if recurrence_type == "weekdays":
            while first_time.weekday() >= 5:
                first_time += timedelta(days=1)
    
    task_id = add_task(
        message=message,
        phone=phone,
        time=first_time.isoformat(),
        is_recurring=True,
        recurrence_type=recurrence_type,
        recurrence_time=time_str,
        recurrence_day=recurrence_day,
    )
    
    if task_id:
        print(f"Added {recurrence_type} task {task_id}: '{message}' for {phone}")
        print(f"  First reminder: {first_time.strftime('%b %d at %I:%M %p')} IST")
        print(f"  Recurs: {recurrence_type} at {time_str} IST")
        if recurrence_day:
            print(f"  Day of month: {recurrence_day}")
    else:
        print("Failed to add task (might be duplicate)")

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    
    cmd = sys.argv[1].lower()
    
    if cmd == "task" and len(sys.argv) >= 5:
        message = sys.argv[2]
        phone = sys.argv[3]
        time = sys.argv[4]
        
        task_id = add_task(message, phone, time)
        if task_id:
            print(f"Added task {task_id}: '{message}' for {phone} at {time}")
        else:
            print("Failed to add task (might be duplicate)")
    
    elif cmd == "daily" and len(sys.argv) >= 5:
        message = sys.argv[2]
        phone = sys.argv[3]
        time_str = sys.argv[4]  # HH:MM format
        add_recurring_task(message, phone, time_str, "daily")
    
    elif cmd == "weekdays" and len(sys.argv) >= 5:
        message = sys.argv[2]
        phone = sys.argv[3]
        time_str = sys.argv[4]  # HH:MM format
        add_recurring_task(message, phone, time_str, "weekdays")
    
    elif cmd == "weekly" and len(sys.argv) >= 5:
        message = sys.argv[2]
        phone = sys.argv[3]
        time_str = sys.argv[4]  # HH:MM format
        add_recurring_task(message, phone, time_str, "weekly")
    
    elif cmd == "monthly" and len(sys.argv) >= 5:
        message = sys.argv[2]
        phone = sys.argv[3]
        time_str = sys.argv[4]  # HH:MM format
        recurrence_day = int(sys.argv[5]) if len(sys.argv) > 5 else None
        add_recurring_task(message, phone, time_str, "monthly", recurrence_day)
    
    elif cmd == "allday" and len(sys.argv) >= 4:
        message = sys.argv[2]
        phone = sys.argv[3]
        
        # All-day tasks start at 8 AM tomorrow
        now = datetime.now(IST)
        tomorrow_8am = (now + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
        
        task_id = add_task(
            message=message,
            phone=phone,
            time=tomorrow_8am.isoformat(),
            is_allday=True,
        )
        
        if task_id:
            print(f"Added all-day task {task_id}: '{message}' for {phone}")
            print(f"  Starts: {tomorrow_8am.strftime('%b %d at %I:%M %p')} IST")
            print(f"  Escalation: SMS (8am) -> WhatsApp (12pm) -> Call (6pm)")
        else:
            print("Failed to add task (might be duplicate)")
    
    elif cmd == "allow" and len(sys.argv) >= 3:
        phone = sys.argv[2]
        name = sys.argv[3] if len(sys.argv) > 3 else None
        
        if add_allowed_number(phone, name):
            print(f"Added {phone} to allowed numbers")
        else:
            print(f"{phone} already allowed")
    
    elif cmd == "list":
        list_tasks()
    
    elif cmd == "allowed":
        list_allowed()
    
    else:
        print(__doc__)

if __name__ == "__main__":
    main()
