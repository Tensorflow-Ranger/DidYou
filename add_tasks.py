#!/usr/bin/env python3
"""
Helper script to add tasks and manage allowed numbers.

Usage:
    # Add a task
    python add_tasks.py task "Drink water" +15551234567 "2026-05-03T17:00:00"
    
    # Add an allowed number
    python add_tasks.py allow +15551234567 "John"
    
    # List pending tasks
    python add_tasks.py list
    
    # List allowed numbers
    python add_tasks.py allowed
"""
from db import add_task, add_allowed_number, get_pending_tasks
import sqlite3
import sys

def list_tasks():
    tasks = get_pending_tasks()
    if not tasks:
        print("No pending tasks.")
        return
    
    print(f"{'ID':<5} {'Message':<30} {'Phone':<15} {'Due':<20} {'Attempts':<8}")
    print("-" * 80)
    for t in tasks:
        print(f"{t['id']:<5} {t['message'][:28]:<30} {t['phone']:<15} {t['time'][:19]:<20} {t['attempts'] or 0:<8}")

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
