from twilio.rest import Client
from dotenv import load_dotenv
import os

load_dotenv()

client = Client(
    os.getenv("TWILIO_SID"),
    os.getenv("TWILIO_AUTH_TOKEN")
)

def make_call(phone, task_id):
    client.calls.create(
        to=phone,
        from_=os.getenv("TWILIO_PHONE"),
        url=f"{os.getenv('BASE_URL')}/voice?task_id={task_id}"
    )
