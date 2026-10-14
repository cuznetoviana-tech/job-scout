"""Job Scout web server. Run it with:  python app.py   then open http://localhost:5000"""

import threading
import uuid
from datetime import datetime

import anthropic
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory

load_dotenv()  # loads ANTHROPIC_API_KEY from the .env file

import db  # noqa: E402
from agents import JOB_TYPES, RESUME_PDF, prep_application, run_search  # noqa: E402  (must come after load_dotenv)

db.init()
app = Flask(__name__, static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # resume uploads up to 10 MB

# Agent tasks in progress, kept in memory: {task_id: {"status", "log", "result", "error"}}
tasks: dict[str, dict] = {}


def _run(task_id: str, fn, *args) -> None:
    state = tasks[task_id]
    try:
        state["result"] = fn(*args, state["log"].append)
        state["status"] = "done"
    except anthropic.AuthenticationError:
        state["error"] = "Your API key was rejected. Check the ANTHROPIC_API_KEY in your .env file."
    except anthropic.RateLimitError:
        state["error"] = "Rate limited by the API. Wait a minute and try again."
    except anthropic.APIConnectionError:
        state["error"] = "Couldn't reach the Claude API. Check your internet connection."
    except Exception as e:  # show anything else in the page instead of failing silently
        state["error"] = str(e)
    if state["error"]:
        state["status"] = "error"


def _start(fn, *args):
    task_id = uuid.uuid4().hex
    tasks[task_id] = {"status": "running", "log": [], "result": None, "error": None}
    threading.Thread(target=_run, args=(task_id, fn, *args), daemon=True).start()
    return jsonify({"id": task_id})


@app.get("/")
def index():
    return send_from_directory("static", "index.html")


@app.get("/api/job-types")
def job_types():
    return jsonify(JOB_TYPES)


def search_and_save(profile: dict, log) -> int:
    jobs = run_search(profile, log)
    db.save_search_results(jobs)
    return len(jobs)


def prep_and_save(url: str, profile: dict, log) -> dict:
    job = db.get_job(url)
    if job is None:
        raise RuntimeError("That job isn't in your list anymore.")
    kit = prep_application(job, profile, log)
    db.save_kit(url, kit)
    return kit


@app.post("/api/search")
def start_search():
    profile = request.get_json(force=True)
    db.set_setting("profile", profile)
    return _start(search_and_save, profile)


@app.post("/api/prep")
def start_prep():
    body = request.get_json(force=True)
    return _start(prep_and_save, body["url"], body.get("profile", {}))


@app.get("/api/jobs")
def list_jobs():
    return jsonify(db.list_jobs())


@app.post("/api/jobs/mark")
def mark_job():
    body = request.get_json(force=True)
    db.mark(body["url"], saved=body.get("saved"), applied=body.get("applied"), hidden=body.get("hidden"))
    return jsonify({"ok": True})


@app.put("/api/kit")
def update_kit():
    body = request.get_json(force=True)
    db.save_kit(body["url"], body.get("kit"))
    return jsonify({"ok": True})


@app.get("/api/profile")
def get_profile():
    return jsonify(db.get_setting("profile", {}))


@app.put("/api/profile")
def put_profile():
    db.set_setting("profile", request.get_json(force=True))
    return jsonify({"ok": True})


@app.post("/api/import")
def import_from_browser():
    body = request.get_json(force=True)
    added = db.import_from_browser(body.get("jobs", []), body.get("marks", {}), body.get("kits", {}))
    if body.get("profile") and not db.get_setting("profile"):
        db.set_setting("profile", body["profile"])
    return jsonify({"added": added})


@app.get("/api/task/<task_id>")
def get_task(task_id: str):
    state = tasks.get(task_id)
    if state is None:
        return jsonify({"error": "Task not found. The server may have restarted; try again."}), 404
    return jsonify(state)


@app.get("/api/resume")
def resume_status():
    if not RESUME_PDF.exists():
        return jsonify({"uploaded": False})
    saved = datetime.fromtimestamp(RESUME_PDF.stat().st_mtime).strftime("%b %d, %Y")
    return jsonify({"uploaded": True, "saved": saved})


@app.post("/api/resume")
def upload_resume():
    file = request.files.get("file")
    if file is None or not file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Please choose a PDF file."}), 400
    data = file.read()
    if not data.startswith(b"%PDF"):
        return jsonify({"error": "That file doesn't look like a PDF."}), 400
    RESUME_PDF.parent.mkdir(exist_ok=True)
    RESUME_PDF.write_bytes(data)
    return resume_status()


@app.delete("/api/resume")
def delete_resume():
    RESUME_PDF.unlink(missing_ok=True)
    return resume_status()


if __name__ == "__main__":
    app.run(debug=True, port=5000)
