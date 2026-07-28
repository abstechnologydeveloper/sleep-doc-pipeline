import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row

from project_paths import DATA_DIR


LEGACY_DATABASE_PATH = DATA_DIR / "admin.sqlite3"


class ConnectionAdapter:
    """Keep the existing parameterized query style while using psycopg."""

    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql: str, parameters=()):
        return self.connection.execute(sql.replace("?", "%s"), parameters)

    def executescript(self, sql: str) -> None:
        for statement in sql.split(";"):
            if statement.strip():
                self.connection.execute(statement)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL must point to the Sleep Studio PostgreSQL database")
    connection = psycopg.connect(database_url, row_factory=dict_row)
    try:
        yield ConnectionAdapter(connection)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize() -> None:
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id BIGSERIAL PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL DEFAULT '',
                avatar_url TEXT NOT NULL DEFAULT '',
                role TEXT NOT NULL DEFAULT 'creator' CHECK(role IN ('admin', 'creator')),
                plan TEXT NOT NULL DEFAULT 'free',
                status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'suspended')),
                monthly_job_limit INTEGER NOT NULL DEFAULT 3,
                max_minutes_per_job REAL NOT NULL DEFAULT 10,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_login_at TEXT
            );
            CREATE TABLE IF NOT EXISTS auth_identities (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                provider TEXT NOT NULL,
                provider_subject TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(provider, provider_subject)
            );
            CREATE TABLE IF NOT EXISTS magic_links (
                id BIGSERIAL PRIMARY KEY,
                email TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                requested_ip_hash TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                used_at TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS magic_links_email_created
            ON magic_links(email, created_at);
            CREATE TABLE IF NOT EXISTS jobs (
                id BIGSERIAL PRIMARY KEY,
                owner_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
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
                script_path TEXT,
                share_token TEXT,
                log_path TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS jobs_status_schedule
            ON jobs(status, scheduled_at);
            CREATE INDEX IF NOT EXISTS jobs_owner_id ON jobs(owner_id);
            CREATE TABLE IF NOT EXISTS publications (
                id BIGSERIAL PRIMARY KEY,
                job_id BIGINT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                platform TEXT NOT NULL,
                status TEXT NOT NULL,
                remote_id TEXT,
                remote_url TEXT,
                error TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE(job_id, platform)
            );
            CREATE TABLE IF NOT EXISTS social_connections (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                platform TEXT NOT NULL,
                external_account_id TEXT NOT NULL DEFAULT '',
                account_name TEXT NOT NULL DEFAULT '',
                access_token_encrypted TEXT NOT NULL,
                refresh_token_encrypted TEXT NOT NULL,
                token_expires_at TEXT,
                scopes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(user_id, platform)
            );
            CREATE TABLE IF NOT EXISTS app_migrations (
                name TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            """
        )
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS jobs_share_token ON jobs(share_token)"
        )
    import_legacy_sqlite()
    ensure_configured_accounts()


def ensure_configured_accounts() -> None:
    """Seed only the configured administration account."""
    configured = ((os.getenv("ADMIN_EMAIL", "").strip().lower(), "admin"),)
    now = utc_now()
    with connect() as db:
        for email, role in configured:
            if not email or "@" not in email:
                continue
            existing = db.execute(
                "SELECT id, role FROM users WHERE LOWER(email)=LOWER(?)", (email,)
            ).fetchone()
            if existing:
                if role == "admin" and existing["role"] != "admin":
                    db.execute(
                        "UPDATE users SET role='admin', updated_at=? WHERE id=?",
                        (now, existing["id"]),
                    )
                continue
            db.execute(
                """INSERT INTO users
                (email, role, created_at, updated_at)
                VALUES (?, ?, ?, ?)""",
                (email, role, now, now),
            )


def create_creator(email: str):
    normalized_email = email.strip().lower()
    now = utc_now()
    with connect() as db:
        existing = db.execute(
            "SELECT * FROM users WHERE LOWER(email)=LOWER(?)", (normalized_email,)
        ).fetchone()
        if existing:
            return existing, False
        creator = db.execute(
            """INSERT INTO users (email, role, created_at, updated_at)
            VALUES (?, 'creator', ?, ?) RETURNING *""",
            (normalized_email, now, now),
        ).fetchone()
        return creator, True


def import_legacy_sqlite() -> None:
    """Import the existing SQLite data once when PostgreSQL is first introduced."""
    if not LEGACY_DATABASE_PATH.is_file():
        return
    with connect() as db:
        imported = db.execute(
            "SELECT 1 AS present FROM app_migrations WHERE name = ?",
            ("sqlite_import_v1",),
        ).fetchone()
        if imported:
            return

        legacy = sqlite3.connect(LEGACY_DATABASE_PATH)
        legacy.row_factory = sqlite3.Row
        try:
            tables = ("users", "auth_identities", "magic_links", "jobs", "publications")
            for table in tables:
                exists = legacy.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone()
                if not exists:
                    continue
                source_columns = [
                    row["name"] for row in legacy.execute(f"PRAGMA table_info({table})")
                ]
                target_columns = {
                    row["column_name"]
                    for row in db.execute(
                        """SELECT column_name FROM information_schema.columns
                        WHERE table_schema='public' AND table_name=?""",
                        (table,),
                    ).fetchall()
                }
                columns = [column for column in source_columns if column in target_columns]
                if not columns:
                    continue
                quoted = ", ".join(f'"{column}"' for column in columns)
                placeholders = ", ".join("?" for _column in columns)
                for row in legacy.execute(f"SELECT {quoted} FROM {table}"):
                    db.execute(
                        f"INSERT INTO {table} ({quoted}) VALUES ({placeholders}) ON CONFLICT DO NOTHING",
                        tuple(row[column] for column in columns),
                    )
                if "id" in columns:
                    db.execute(
                        f"""SELECT setval(pg_get_serial_sequence('{table}', 'id'),
                        COALESCE((SELECT MAX(id) FROM {table}), 1),
                        EXISTS(SELECT 1 FROM {table}))"""
                    )
        finally:
            legacy.close()
        db.execute(
            "INSERT INTO app_migrations (name, applied_at) VALUES (?, ?)",
            ("sqlite_import_v1", utc_now()),
        )


def create_job(**values) -> int:
    now = utc_now()
    owner_id = int(values["owner_id"])
    with connect() as db:
        cursor = db.execute(
            """INSERT INTO jobs
            (kind, status, topic, minutes, title, description, hashtags,
             platforms, scheduled_at, source_path, created_at, updated_at)
            VALUES (?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id""",
            (
                values["kind"], values.get("topic"), values.get("minutes"),
                values["title"], values.get("description", ""),
                values.get("hashtags", ""), json.dumps(values["platforms"]),
                values.get("scheduled_at"), values.get("source_path"), now, now,
            ),
        )
        job_id = cursor.fetchone()["id"]
        db.execute(
            "UPDATE jobs SET owner_id = ? WHERE id = ?",
            (owner_id, job_id),
        )
        for platform in values["platforms"]:
            db.execute(
                "INSERT INTO publications (job_id, platform, status, updated_at) VALUES (?, ?, 'pending', ?)",
                (job_id, platform, now),
            )
        return int(job_id)


def create_story_job(
    *,
    owner_id: int,
    topic: str,
    minutes: float,
    title: str,
    description: str,
    hashtags: str,
    platforms: list[str],
    scheduled_at: str | None,
    active_limit: int = 1,
) -> tuple[int | None, str | None]:
    """Validate creator limits and queue a story in one write transaction."""
    now = utc_now()
    month_start = datetime.now(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    ).isoformat(timespec="seconds")
    with connect() as db:
        user = db.execute(
            "SELECT * FROM users WHERE id = ? FOR UPDATE", (owner_id,)
        ).fetchone()
        if not user or user["status"] != "active":
            return None, "Your account is not active."
        if minutes < 0.5:
            return None, "Duration must be at least 0.5 minutes."

        if user["role"] != "admin":
            if minutes > float(user["max_minutes_per_job"]):
                return (
                    None,
                    f"Duration cannot exceed {user['max_minutes_per_job']:g} minutes.",
                )
            monthly_usage = db.execute(
                """SELECT COUNT(*) AS total FROM jobs WHERE owner_id = ?
                AND kind = 'automatic' AND created_at >= ?""",
                (owner_id, month_start),
            ).fetchone()["total"]
            if monthly_usage >= int(user["monthly_job_limit"]):
                return None, "Your monthly story limit has been reached."
            active_jobs = db.execute(
                """SELECT COUNT(*) AS total FROM jobs WHERE owner_id = ?
                AND status IN ('queued', 'processing', 'publishing')""",
                (owner_id,),
            ).fetchone()["total"]
            if active_jobs >= active_limit:
                return (
                    None,
                    "Wait for your active story to finish before starting another.",
                )

        cursor = db.execute(
            """INSERT INTO jobs
            (owner_id, kind, status, topic, minutes, title, description, hashtags,
             platforms, scheduled_at, source_path, created_at, updated_at)
            VALUES (?, 'automatic', 'queued', ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
            RETURNING id""",
            (
                owner_id,
                topic,
                minutes,
                title,
                description,
                hashtags,
                json.dumps(platforms),
                scheduled_at,
                now,
                now,
            ),
        )
        job_id = int(cursor.fetchone()["id"])
        for platform in platforms:
            db.execute(
                """INSERT INTO publications
                (job_id, platform, status, updated_at)
                VALUES (?, ?, 'pending', ?)""",
                (job_id, platform, now),
            )
        return job_id, None


def create_uploaded_media(
    *, owner_id: int, title: str, description: str, hashtags: str, source_path: str
) -> int:
    """Register a finished upload as reusable media without queueing work."""
    now = utc_now()
    with connect() as db:
        cursor = db.execute(
            """INSERT INTO jobs
            (kind, status, topic, minutes, title, description, hashtags,
             platforms, scheduled_at, source_path, video_path, created_at, updated_at)
            VALUES ('manual', 'completed', NULL, NULL, ?, ?, ?, '[]', NULL, ?, ?, ?, ?)
            RETURNING id""",
            (title, description, hashtags, source_path, source_path, now, now),
        )
        media_id = int(cursor.fetchone()["id"])
        db.execute(
            "UPDATE jobs SET owner_id = ? WHERE id = ?", (owner_id, media_id)
        )
        return media_id


def list_jobs(
    limit: int = 100,
    kind: str | None = None,
    status: str | None = None,
    owner_id: int | None = None,
    include_all: bool = False,
):
    filters = []
    parameters: list[object] = []
    if kind:
        filters.append("kind = ?")
        parameters.append(kind)
    if status:
        filters.append("status = ?")
        parameters.append(status)
    if not include_all:
        filters.append("owner_id = ?")
        parameters.append(owner_id)
    where_clause = f" WHERE {' AND '.join(filters)}" if filters else ""
    parameters.append(limit)
    with connect() as db:
        return db.execute(
            f"SELECT * FROM jobs{where_clause} ORDER BY id DESC LIMIT ?",
            parameters,
        ).fetchall()


def list_media_jobs(
    limit: int = 100, owner_id: int | None = None, include_all: bool = False
):
    """Return reusable generated videos and upload-only media records."""
    with connect() as db:
        owner_clause = "" if include_all else "AND owner_id = ?"
        parameters = (limit,) if include_all else (owner_id, limit)
        return db.execute(
            f"""SELECT * FROM jobs
            WHERE video_path IS NOT NULL AND video_path != ''
            AND (kind = 'automatic' OR (kind = 'manual' AND platforms = '[]'))
            {owner_clause}
            ORDER BY id DESC LIMIT ?""",
            parameters,
        ).fetchall()


def list_local_media_jobs(limit: int = 20):
    with connect() as db:
        return db.execute(
            """SELECT * FROM jobs WHERE video_path IS NOT NULL AND video_path != ''
            AND video_path NOT LIKE 'r2://%%'
            AND status IN ('completed', 'published', 'waiting_for_connections')
            ORDER BY id LIMIT ?""",
            (limit,),
        ).fetchall()


def job_status_counts(owner_id: int | None = None, include_all: bool = False) -> dict[str, int]:
    with connect() as db:
        where = "" if include_all else " WHERE owner_id = ?"
        parameters = () if include_all else (owner_id,)
        rows = db.execute(
            f"SELECT status, COUNT(*) AS total FROM jobs{where} GROUP BY status",
            parameters,
        ).fetchall()
    return {str(row["status"]): int(row["total"]) for row in rows}


def list_job_statuses(
    limit: int = 100, owner_id: int | None = None, include_all: bool = False
) -> list[dict]:
    with connect() as db:
        where = "" if include_all else " WHERE owner_id = ?"
        parameters = (limit,) if include_all else (owner_id, limit)
        rows = db.execute(
            f"""SELECT id, kind, status, title, platforms, video_path, updated_at
            FROM jobs{where} ORDER BY id DESC LIMIT ?""",
            parameters,
        ).fetchall()
        return [dict(row) for row in rows]


def get_job(
    job_id: int, owner_id: int | None = None, include_all: bool = False
):
    with connect() as db:
        owner_clause = "" if include_all else " AND owner_id = ?"
        parameters = (job_id,) if include_all else (job_id, owner_id)
        job = db.execute(
            f"SELECT * FROM jobs WHERE id = ?{owner_clause}", parameters
        ).fetchone()
        if not job:
            return None, []
        publications = db.execute(
            "SELECT * FROM publications WHERE job_id = ? ORDER BY platform", (job_id,)
        ).fetchall()
        return job, publications


def get_job_by_share_token(token: str):
    with connect() as db:
        return db.execute(
            "SELECT * FROM jobs WHERE share_token = ?", (token,)
        ).fetchone()


def set_job_share_token(job_id: int, token: str | None) -> None:
    with connect() as db:
        db.execute(
            "UPDATE jobs SET share_token = ?, updated_at = ? WHERE id = ?",
            (token, utc_now(), job_id),
        )


def get_or_create_user(
    *, email: str, name: str = "", avatar_url: str = "", admin_email: str = ""
):
    now = utc_now()
    normalized_email = email.strip().lower()
    with connect() as db:
        user = db.execute(
            "SELECT * FROM users WHERE LOWER(email) = LOWER(?)", (normalized_email,)
        ).fetchone()
        if user:
            next_role = (
                "admin"
                if admin_email and normalized_email == admin_email.lower()
                else user["role"]
            )
            db.execute(
                """UPDATE users SET name=CASE WHEN ? != '' THEN ? ELSE name END,
                avatar_url=CASE WHEN ? != '' THEN ? ELSE avatar_url END,
                role=?, last_login_at=?, updated_at=? WHERE id=?""",
                (name, name, avatar_url, avatar_url, next_role, now, now, user["id"]),
            )
        else:
            role = "admin" if admin_email and normalized_email == admin_email.lower() else "creator"
            cursor = db.execute(
                """INSERT INTO users
                (email, name, avatar_url, role, created_at, updated_at, last_login_at)
                VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id""",
                (normalized_email, name, avatar_url, role, now, now, now),
            )
            user_id = cursor.fetchone()["id"]
            user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            return user
        return db.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()


def link_identity(user_id: int, provider: str, subject: str) -> None:
    with connect() as db:
        db.execute(
            """INSERT INTO auth_identities
            (user_id, provider, provider_subject, created_at) VALUES (?, ?, ?, ?)
            ON CONFLICT (provider, provider_subject) DO NOTHING""",
            (user_id, provider, subject, utc_now()),
        )


def get_user(user_id: int):
    with connect() as db:
        return db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def create_magic_link(email: str, token_hash: str, ip_hash: str, ttl_minutes: int = 15) -> bool:
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=1)).isoformat(timespec="seconds")
    with connect() as db:
        recent = db.execute(
            "SELECT COUNT(*) AS total FROM magic_links WHERE email=? AND created_at>=?",
            (email.lower(), cutoff),
        ).fetchone()["total"]
        if recent >= 5:
            return False
        db.execute(
            """INSERT INTO magic_links
            (email, token_hash, requested_ip_hash, expires_at, created_at)
            VALUES (?, ?, ?, ?, ?)""",
            (
                email.lower(), token_hash, ip_hash,
                (now + timedelta(minutes=ttl_minutes)).isoformat(timespec="seconds"),
                now.isoformat(timespec="seconds"),
            ),
        )
        return True


def discard_magic_link(token_hash: str) -> None:
    """Remove an undelivered link so provider failures do not consume the rate limit."""
    with connect() as db:
        db.execute(
            "DELETE FROM magic_links WHERE token_hash = ? AND used_at IS NULL",
            (token_hash,),
        )


def consume_magic_link(token_hash: str):
    now = utc_now()
    with connect() as db:
        link = db.execute(
            """SELECT * FROM magic_links WHERE token_hash=? AND used_at IS NULL
            AND expires_at>=? FOR UPDATE""",
            (token_hash, now),
        ).fetchone()
        if not link:
            return None
        db.execute("UPDATE magic_links SET used_at=? WHERE id=?", (now, link["id"]))
        return link


def monthly_job_usage(user_id: int) -> int:
    month_start = datetime.now(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    ).isoformat(timespec="seconds")
    with connect() as db:
        return int(db.execute(
            """SELECT COUNT(*) AS total FROM jobs
            WHERE owner_id=? AND kind='automatic' AND created_at>=?""",
            (user_id, month_start),
        ).fetchone()["total"])


def active_job_count(user_id: int) -> int:
    with connect() as db:
        return int(db.execute(
            """SELECT COUNT(*) AS total FROM jobs WHERE owner_id=?
            AND status IN ('queued','processing','publishing')""",
            (user_id,),
        ).fetchone()["total"])


def list_users():
    month_start = datetime.now(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    ).isoformat(timespec="seconds")
    with connect() as db:
        return db.execute(
            """SELECT u.*,
            COUNT(j.id) AS total_jobs,
            SUM(CASE WHEN j.kind='automatic' AND j.created_at>=? THEN 1 ELSE 0 END)
                AS monthly_jobs,
            SUM(CASE WHEN j.status IN ('queued','processing','publishing') THEN 1 ELSE 0 END) AS active_jobs
            FROM users u LEFT JOIN jobs j ON j.owner_id=u.id
            GROUP BY u.id ORDER BY u.created_at DESC""",
            (month_start,),
        ).fetchall()


def update_user_limits(
    user_id: int, *, monthly_job_limit: int, max_minutes_per_job: float, status: str
) -> None:
    with connect() as db:
        db.execute(
            """UPDATE users SET monthly_job_limit=?, max_minutes_per_job=?,
            status=?, updated_at=? WHERE id=? AND role='creator'""",
            (monthly_job_limit, max_minutes_per_job, status, utc_now(), user_id),
        )


def get_social_connection(user_id: int, platform: str):
    with connect() as db:
        return db.execute(
            "SELECT * FROM social_connections WHERE user_id=? AND platform=?",
            (user_id, platform),
        ).fetchone()


def save_social_connection(
    user_id: int,
    platform: str,
    *,
    external_account_id: str,
    account_name: str,
    access_token_encrypted: str,
    refresh_token_encrypted: str,
    token_expires_at: str | None,
    scopes: str,
) -> None:
    now = utc_now()
    with connect() as db:
        db.execute(
            """INSERT INTO social_connections
            (user_id, platform, external_account_id, account_name,
             access_token_encrypted, refresh_token_encrypted, token_expires_at,
             scopes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (user_id, platform) DO UPDATE SET
              external_account_id=EXCLUDED.external_account_id,
              account_name=EXCLUDED.account_name,
              access_token_encrypted=EXCLUDED.access_token_encrypted,
              refresh_token_encrypted=EXCLUDED.refresh_token_encrypted,
              token_expires_at=EXCLUDED.token_expires_at,
              scopes=EXCLUDED.scopes,
              updated_at=EXCLUDED.updated_at""",
            (
                user_id, platform, external_account_id, account_name,
                access_token_encrypted, refresh_token_encrypted, token_expires_at,
                scopes, now, now,
            ),
        )


def delete_social_connection(user_id: int, platform: str) -> None:
    with connect() as db:
        db.execute(
            "DELETE FROM social_connections WHERE user_id=? AND platform=?",
            (user_id, platform),
        )


def update_social_access_token(
    user_id: int, platform: str, access_token_encrypted: str, token_expires_at: str | None
) -> None:
    with connect() as db:
        db.execute(
            """UPDATE social_connections SET access_token_encrypted=?,
            token_expires_at=?, updated_at=? WHERE user_id=? AND platform=?""",
            (access_token_encrypted, token_expires_at, utc_now(), user_id, platform),
        )


def media_reference_count(reference: str) -> int:
    with connect() as db:
        row = db.execute(
            """SELECT COUNT(*) AS total FROM jobs
            WHERE video_path=? OR source_path=?""",
            (reference, reference),
        ).fetchone()
        return int(row["total"])


def claim_job():
    now = utc_now()
    with connect() as db:
        job = db.execute(
            """SELECT * FROM jobs WHERE status = 'queued'
            AND (scheduled_at IS NULL OR scheduled_at = '' OR scheduled_at <= ?)
            ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED""", (now,)
        ).fetchone()
        if not job:
            return None
        db.execute(
            "UPDATE jobs SET status = 'processing', updated_at = ? WHERE id = ?",
            (now, job["id"]),
        )
        return job


def update_job(job_id: int, status: str, **fields) -> None:
    allowed = {
        "topic", "title", "description", "hashtags",
        "video_path", "script_path", "log_path", "error",
    }
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


def request_job_deletion(job_id: int) -> None:
    with connect() as db:
        job = db.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not job:
            return
        if job["status"] in {"processing", "publishing", "cancel_requested"}:
            db.execute(
                "UPDATE jobs SET status='cancel_requested', updated_at=? WHERE id=?",
                (utc_now(), job_id),
            )
        else:
            db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))


def request_jobs_deletion(job_ids: list[int]) -> None:
    """Delete idle jobs and request cancellation for running jobs."""
    unique_ids = sorted(set(job_ids))
    if not unique_ids:
        return
    now = utc_now()
    placeholders = ",".join("?" for _job_id in unique_ids)
    with connect() as db:
        db.execute(
            f"""UPDATE jobs SET status='cancel_requested', updated_at=?
            WHERE id IN ({placeholders})
            AND status IN ('processing', 'publishing', 'cancel_requested')""",
            (now, *unique_ids),
        )
        db.execute(
            f"""DELETE FROM jobs WHERE id IN ({placeholders})
            AND status NOT IN ('processing', 'publishing', 'cancel_requested')""",
            unique_ids,
        )


def cancellation_requested(job_id: int) -> bool:
    with connect() as db:
        job = db.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return not job or job["status"] == "cancel_requested"


def delete_job(job_id: int) -> None:
    with connect() as db:
        db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))


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
