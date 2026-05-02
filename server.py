from flask import Flask, request
from twilio.twiml.voice_response import VoiceResponse, Gather

app = Flask(__name__)

@app.route("/voice", methods=["GET", "POST"])
def voice():
    task_id = request.args.get("task_id")

    response = VoiceResponse()

    gather = Gather(
        num_digits=1,
        action=f"/handle-input?task_id={task_id}"
    )

    gather.say("Reminder: Did you complete your task? Press 1 for yes, 2 for no.")
    response.append(gather)

    return str(response)

from db import update_task

@app.route("/handle-input", methods=["POST"])
def handle_input():
    task_id = request.args.get("task_id")
    digit = request.form.get("Digits")

    response = VoiceResponse()

    if digit == "1":
        update_task(task_id, "done")
        response.say("Great job! Task completed.")
    else:
        response.say("Okay, I will remind you again later.")

    return str(response)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
