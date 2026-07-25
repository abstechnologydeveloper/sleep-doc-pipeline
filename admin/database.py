import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
DATABASE_PATH = DATA_DIR / "admin.sqlite3"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def initialize() -> None:
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL CHECK(kind IN ('automatic', 'manual')),
                status TEXT NOT NULL,
                topic TEXT,
                minutes REAL,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                hashtags TEXT NOT NULL DEFAULT '',
                platforms TEXT NOT NULL,
                scheduled_at TEXT,
                source_path TEXT,
                video_path TEXT,
                log_path TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS jobs_status_schedule
            ON jobs(status, scheduled_at);
            CREATE TABLE IF NOT EXISTS publications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                platform TEXT NOT NULL,
                status TEXT NOT NULL,
                remote_id TEXT,
                remote_url TEXT,
                error TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE(job_id, platform)
            );
            """
        )


def create_job(**values) -> int:
    now = utc_now()
    with connect() as db:
        cursor = db.execute(
            """INSERT INTO jobs
            (kind, status, topic, minutes, title, description, hashtags,
             platforms, scheduled_at, source_path, created_at, updated_at)
            VALUES (?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                values["kind"], values.get("topic"), values.get("minutes"),
                values["title"], values.get("description", ""),
                values.get("hashtags", ""), json.dumps(values["platforms"]),
                values.get("scheduled_at"), values.get("source_path"), now, now,
            ),
        )
        job_id = cursor.lastrowid
        for platform in values["platforms"]:
            db.execute(
                "INSERT INTO publications (job_id, platform, status, updated_at) VALUES (?, ?, 'pending', ?)",
                (job_id, platform, now),
            )
        return int(job_id)


def list_jobs(limit: int = 100):
    with connect() as db:
        return db.execute(
            "SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()


def get_job(job_id: int):
    with connect() as db:
        job = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        publications = db.execute(
            "SELECT * FROM publications WHERE job_id = ? ORDER BY platform", (job_id,)
        ).fetchall()
        return job, publications


def claim_job():
    now = utc_now()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        job = db.execute(
            """SELECT * FROM jobs WHERE status = 'queued'
            AND (scheduled_at IS NULL OR scheduled_at = '' OR scheduled_at <= ?)
            ORDER BY id LIMIT 1""", (now,)
        ).fetchone()
        if not job:
            return None
        db.execute(
            "UPDATE jobs SET status = 'processing', updated_at = ? WHERE id = ?",
            (now, job["id"]),
        )
        return job


def update_job(job_id: int, status: str, **fields) -> None:
    allowed = {"video_path", "log_path", "error"}
    updates = {key: value for key, value in fields.items() if key in allowed}
    updates.update(status=status, updated_at=utc_now())
    assignments = ", ".join(f"{key} = ?" for key in updates)
    with connect() as db:
        db.execute(
            f"UPDATE jobs SET {assignments} WHERE id = ?",
            (*updates.values(), job_id),
        )


def retry_job(job_id: int) -> None:
    with connect() as db:
        db.execute(
            "UPDATE jobs SET status='queued', error=NULL, updated_at=? WHERE id=?",
            (utc_now(), job_id),
        )
        db.execute(
            "UPDATE publications SET status='pending', error=NULL, updated_at=? WHERE job_id=?",
            (utc_now(), job_id),
        )


def update_publication(job_id: int, platform: str, status: str, **fields) -> None:
    allowed = {"remote_id", "remote_url", "error"}
    updates = {key: value for key, value in fields.items() if key in allowed}
    updates.update(status=status, updated_at=utc_now())
    assignments = ", ".join(f"{key} = ?" for key in updates)
    with connect() as db:
        db.execute(
            f"UPDATE publications SET {assignments} WHERE job_id=? AND platform=?",
            (*updates.values(), job_id, platform),
        )
