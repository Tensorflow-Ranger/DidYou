from apscheduler.schedulers.background import BackgroundScheduler
from caller import make_call
from db import get_pending_tasks, update_task_time
from datetime import datetime, timedelta

scheduler = BackgroundScheduler()

def check_tasks():
    tasks = get_pending_tasks()

    now = datetime.now()

    for task in tasks:
        task_id, message, phone, time, status = task

        task_time = datetime.fromisoformat(time)

        if now >= task_time:
            make_call(phone, task_id)

            # push next retry
def check_tasks():
    tasks = get_pending_tasks()

    now = datetime.now()

    for task in tasks:
        task_id, message, phone, time, status = task

        task_time = datetime.fromisoformat(time)

        if now >= task_time:
            make_call(phone, task_id)

            # push next retry
            new_time = now + timedelta(minutes=30)

            update_task_time(task_id, new_time)

scheduler.add_job(check_tasks, 'interval', seconds=30)
scheduler.start()
