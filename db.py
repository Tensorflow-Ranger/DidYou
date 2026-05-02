import sqlite3

conn = sqlite3.connect("tasks.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message TEXT,
    phone TEXT,
    time TEXT,
    status TEXT,
    UNIQUE(message, phone)
)
""")

cursor.execute("""
CREATE UNIQUE INDEX IF NOT EXISTS unique_task
ON tasks(message, phone)
""")

def update_task_time(task_id, new_time):
    cursor.execute(
        "UPDATE tasks SET time=? WHERE id=?",
        (new_time.isoformat(), task_id)
    )
    conn.commit()

def add_task(message, phone, time):
    cursor.execute(
        "INSERT INTO tasks (message, phone, time, status) VALUES (?, ?, ?, ?)",
        (message, phone, time, "pending")
    )
    conn.commit()
    return cursor.lastrowid

def get_pending_tasks():
    cursor.execute("SELECT * FROM tasks WHERE status='pending'")
    return cursor.fetchall()

def update_task(task_id, status):
    cursor.execute("UPDATE tasks SET status=? WHERE id=?", (status, task_id))
    conn.commit()
