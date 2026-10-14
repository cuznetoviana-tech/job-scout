"""Your saved job history, stored in a small SQLite database on your computer.

Every job any search finds is kept (one row per posting URL), along with whether you
saved it, applied, hid it, and its application kit. The file lives in my_files/,
which is in .gitignore.
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "my_files" / "jobscout.db"

# Columns that come from the agents; everything else in the jobs table is your own data.
JOB_FIELDS = ["title", "company", "location", "posted", "deadline", "fit_score",
              "why_it_fits", "watch_out", "source_scout", "category"]


@contextmanager
def connect():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init():
    with connect() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS searches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ran_at TEXT NOT NULL,
                job_count INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS jobs (
                url TEXT PRIMARY KEY,
                data TEXT NOT NULL,              -- the job details as JSON
                first_search INTEGER,            -- search that first found it
                last_search INTEGER,             -- most recent search that found it
                first_seen TEXT NOT NULL,
                saved INTEGER NOT NULL DEFAULT 0,
                applied_at TEXT,                 -- NULL means not applied
                hidden INTEGER NOT NULL DEFAULT 0,
                kit TEXT                         -- application kit as JSON
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _job_data(job: dict) -> str:
    return json.dumps({k: job.get(k) for k in JOB_FIELDS})


def save_search_results(jobs: list[dict]) -> None:
    """Add a search's jobs to your history. Jobs already there get fresh details but keep your marks."""
    with connect() as db:
        search_id = db.execute(
            "INSERT INTO searches (ran_at, job_count) VALUES (?, ?)", (_now(), len(jobs))
        ).lastrowid
        for job in jobs:
            if not job.get("url"):
                continue
            db.execute(
                """INSERT INTO jobs (url, data, first_search, last_search, first_seen)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(url) DO UPDATE SET data = excluded.data, last_search = excluded.last_search""",
                (job["url"], _job_data(job), search_id, search_id, _now()),
            )


def list_jobs() -> dict:
    with connect() as db:
        searches = db.execute("SELECT id FROM searches ORDER BY id").fetchall()
        latest = searches[-1]["id"] if searches else None
        rows = db.execute("SELECT * FROM jobs WHERE hidden = 0").fetchall()
    jobs = []
    for r in rows:
        job = json.loads(r["data"])
        job.update(
            url=r["url"],
            saved=bool(r["saved"]),
            applied_at=r["applied_at"],
            first_seen=r["first_seen"],
            kit=json.loads(r["kit"]) if r["kit"] else None,
            in_latest=latest is not None and r["last_search"] == latest,
            # "New" only means something once you've searched more than once
            is_new=len(searches) > 1 and r["first_search"] == latest,
        )
        jobs.append(job)
    return {"jobs": jobs, "search_count": len(searches)}


def get_job(url: str) -> dict | None:
    with connect() as db:
        row = db.execute("SELECT data FROM jobs WHERE url = ?", (url,)).fetchone()
    return {**json.loads(row["data"]), "url": url} if row else None


def mark(url: str, saved: bool | None = None, applied: bool | None = None, hidden: bool | None = None) -> None:
    with connect() as db:
        if saved is not None:
            db.execute("UPDATE jobs SET saved = ? WHERE url = ?", (int(saved), url))
        if applied is not None:
            db.execute("UPDATE jobs SET applied_at = ? WHERE url = ?", (_now() if applied else None, url))
        if hidden is not None:
            db.execute("UPDATE jobs SET hidden = ? WHERE url = ?", (int(hidden), url))


def save_kit(url: str, kit: dict | None) -> None:
    with connect() as db:
        db.execute("UPDATE jobs SET kit = ? WHERE url = ?", (json.dumps(kit) if kit else None, url))


def get_setting(key: str, default=None):
    with connect() as db:
        row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_setting(key: str, value) -> None:
    with connect() as db:
        db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )


def import_from_browser(jobs: list[dict], marks: dict, kits: dict) -> int:
    """One-time copy of what the old version kept in your browser. Returns how many jobs were added."""
    added = 0
    with connect() as db:
        for job in jobs:
            url = job.get("url")
            if not url:
                continue
            m = marks.get(url, {})
            cur = db.execute(
                """INSERT OR IGNORE INTO jobs (url, data, first_seen, saved, applied_at, kit)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (url, _job_data(job), _now(), int(bool(m.get("saved"))),
                 _now() if m.get("applied") else None, json.dumps(kits[url]) if url in kits else None),
            )
            added += cur.rowcount
    return added
