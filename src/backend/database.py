import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row

from project_paths import DATA_DIR
from .plans import plan_for


LEGACY_DATABASE_PATH = DATA_DIR / "admin.sqlite3"
BYTES_PER_GB = 1024 ** 3


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
                max_minutes_per_job REAL NOT NULL DEFAULT 5,
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
            CREATE TABLE IF NOT EXISTS subscriptions (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
                paystack_customer_code TEXT NOT NULL DEFAULT '',
                payment_reference TEXT NOT NULL UNIQUE,
                plan TEXT NOT NULL,
                status TEXT NOT NULL,
                current_period_end TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS payment_events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                processed_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS payment_attempts (
                reference TEXT PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                plan TEXT NOT NULL,
                amount_ngn INTEGER NOT NULL,
                status TEXT NOT NULL,
                provider_transaction_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS story_usage (
                job_id BIGINT PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                outcome TEXT NOT NULL CHECK(outcome IN ('pending', 'completed', 'released')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS story_usage_user_created
            ON story_usage(user_id, created_at);
            CREATE TABLE IF NOT EXISTS notifications (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                message TEXT NOT NULL,
                read_at TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit_events (
                id BIGSERIAL PRIMARY KEY,
                actor_user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
                action TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target_id TEXT NOT NULL DEFAULT '',
                detail TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS media_assets (
                reference TEXT PRIMARY KEY,
                owner_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                size_bytes BIGINT NOT NULL CHECK(size_bytes >= 0),
                kind TEXT NOT NULL CHECK(kind IN ('video', 'thumbnail', 'upload')),
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS media_assets_owner ON media_assets(owner_id);
            CREATE TABLE IF NOT EXISTS job_feedback (
                id BIGSERIAL PRIMARY KEY,
                job_id BIGINT NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
                user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
                comment TEXT NOT NULL DEFAULT '',
                public_display_name TEXT NOT NULL DEFAULT '',
                public_consent BOOLEAN NOT NULL DEFAULT FALSE,
                approved BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS job_feedback_public
            ON job_feedback(approved, public_consent, updated_at);
            """
        )
        db.executescript(
            """
            ALTER TABLE users ADD COLUMN IF NOT EXISTS max_images_per_job INTEGER NOT NULL DEFAULT 8;
            ALTER TABLE users ADD COLUMN IF NOT EXISTS narration_voice TEXT NOT NULL DEFAULT 'Kore';
            ALTER TABLE users ADD COLUMN IF NOT EXISTS default_story_minutes REAL NOT NULL DEFAULT 2;
            ALTER TABLE users ADD COLUMN IF NOT EXISTS paystack_customer_code TEXT NOT NULL DEFAULT '';
            ALTER TABLE users ADD COLUMN IF NOT EXISTS channel_name TEXT NOT NULL DEFAULT '';
            ALTER TABLE users ADD COLUMN IF NOT EXISTS creator_niche TEXT NOT NULL DEFAULT '';
            ALTER TABLE users ADD COLUMN IF NOT EXISTS target_audience TEXT NOT NULL DEFAULT '';
            ALTER TABLE users ADD COLUMN IF NOT EXISTS content_style TEXT NOT NULL DEFAULT 'cinematic';
            ALTER TABLE users ADD COLUMN IF NOT EXISTS creator_goal TEXT NOT NULL DEFAULT '';
            ALTER TABLE users ADD COLUMN IF NOT EXISTS next_job_number INTEGER NOT NULL DEFAULT 1;
            ALTER TABLE users ADD COLUMN IF NOT EXISTS storage_limit_bytes BIGINT NOT NULL DEFAULT 1073741824;
            ALTER TABLE users ALTER COLUMN max_minutes_per_job SET DEFAULT 5;
            ALTER TABLE jobs ADD COLUMN IF NOT EXISTS narration_voice TEXT NOT NULL DEFAULT 'Kore';
            ALTER TABLE jobs ADD COLUMN IF NOT EXISTS max_images INTEGER NOT NULL DEFAULT 8;
            ALTER TABLE jobs ADD COLUMN IF NOT EXISTS owner_job_number INTEGER;
            ALTER TABLE jobs ADD COLUMN IF NOT EXISTS creator_niche TEXT NOT NULL DEFAULT '';
            ALTER TABLE jobs ADD COLUMN IF NOT EXISTS target_audience TEXT NOT NULL DEFAULT '';
            ALTER TABLE jobs ADD COLUMN IF NOT EXISTS content_style TEXT NOT NULL DEFAULT 'cinematic';
            ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS paystack_customer_code TEXT NOT NULL DEFAULT '';
            ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS payment_reference TEXT NOT NULL DEFAULT '';
            """
        )
        db.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS users_paystack_customer_code
            ON users(paystack_customer_code) WHERE paystack_customer_code != ''"""
        )
        subscription_columns = {
            row["column_name"]
            for row in db.execute(
                """SELECT column_name FROM information_schema.columns
                WHERE table_schema='public' AND table_name='subscriptions'"""
            ).fetchall()
        }
        if "stripe_subscription_id" in subscription_columns:
            db.execute(
                "ALTER TABLE subscriptions ALTER COLUMN stripe_subscription_id DROP NOT NULL"
            )
        db.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS subscriptions_payment_reference
            ON subscriptions(payment_reference) WHERE payment_reference != ''"""
        )
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS jobs_share_token ON jobs(share_token)"
        )
        db.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS jobs_owner_job_number
            ON jobs(owner_id, owner_job_number) WHERE owner_id IS NOT NULL"""
        )
    import_legacy_sqlite()
    initialize_creator_job_numbers()
    initialize_plan_limits()
    initialize_storage_limits()
    initialize_story_usage()
    ensure_configured_accounts()


def initialize_creator_job_numbers() -> None:
    """Give existing jobs stable creator-local numbers beginning at one."""
    with connect() as db:
        applied = db.execute(
            "SELECT 1 AS present FROM app_migrations WHERE name=?",
            ("creator_job_numbers_v1",),
        ).fetchone()
        if applied:
            return
        db.execute(
            """WITH numbered AS (
                SELECT id, ROW_NUMBER() OVER (PARTITION BY owner_id ORDER BY id) AS number
                FROM jobs WHERE owner_id IS NOT NULL
            )
            UPDATE jobs SET owner_job_number=numbered.number
            FROM numbered WHERE jobs.id=numbered.id"""
        )
        db.execute(
            """UPDATE users SET next_job_number=COALESCE(
                (SELECT MAX(owner_job_number) + 1 FROM jobs WHERE owner_id=users.id), 1
            )"""
        )
        db.execute(
            "INSERT INTO app_migrations (name, applied_at) VALUES (?, ?)",
            ("creator_job_numbers_v1", utc_now()),
        )


def _take_job_number(db: ConnectionAdapter, owner_id: int) -> int:
    row = db.execute(
        """UPDATE users SET next_job_number=next_job_number + 1
        WHERE id=? RETURNING next_job_number - 1 AS job_number""",
        (owner_id,),
    ).fetchone()
    if not row:
        raise ValueError("Job owner does not exist")
    return int(row["job_number"])


def initialize_plan_limits() -> None:
    """Apply the commercial plan matrix once to pre-billing creator accounts."""
    with connect() as db:
        applied = db.execute(
            "SELECT 1 AS present FROM app_migrations WHERE name=?",
            ("commercial_plan_limits_v1",),
        ).fetchone()
        if applied:
            return
        for key in ("free", "basic", "pro", "studio"):
            plan = plan_for(key)
            db.execute(
                """UPDATE users SET monthly_job_limit=?, max_minutes_per_job=?,
                max_images_per_job=?, updated_at=? WHERE role='creator' AND plan=?""",
                (plan.monthly_jobs, plan.max_minutes, plan.max_images, utc_now(), key),
            )
        db.execute(
            "INSERT INTO app_migrations (name, applied_at) VALUES (?, ?)",
            ("commercial_plan_limits_v1", utc_now()),
        )


def initialize_storage_limits() -> None:
    """Apply storage allowances without changing administrator custom limits later."""
    with connect() as db:
        applied = db.execute(
            "SELECT 1 AS present FROM app_migrations WHERE name=?",
            ("commercial_storage_limits_v1",),
        ).fetchone()
        if applied:
            return
        for key in ("free", "basic", "pro", "studio"):
            plan = plan_for(key)
            db.execute(
                """UPDATE users SET storage_limit_bytes=?, updated_at=?
                WHERE role='creator' AND plan=?""",
                (plan.storage_gb * BYTES_PER_GB, utc_now(), key),
            )
        db.execute(
            "INSERT INTO app_migrations (name, applied_at) VALUES (?, ?)",
            ("commercial_storage_limits_v1", utc_now()),
        )


def initialize_story_usage() -> None:
    """Backfill durable usage for existing automatic stories once."""
    now = utc_now()
    with connect() as db:
        applied = db.execute(
            "SELECT 1 AS present FROM app_migrations WHERE name=?",
            ("durable_story_usage_v1",),
        ).fetchone()
        if applied:
            return
        db.execute(
            """INSERT INTO story_usage (job_id, user_id, outcome, created_at, updated_at)
            SELECT id, owner_id,
              CASE WHEN status IN ('failed', 'cancel_requested') THEN 'released'
                   WHEN status IN ('completed', 'published', 'waiting_for_connections') THEN 'completed'
                   ELSE 'pending' END,
              created_at, ?
            FROM jobs WHERE kind='automatic' AND owner_id IS NOT NULL
            ON CONFLICT (job_id) DO NOTHING""",
            (now,),
        )
        db.execute(
            "INSERT INTO app_migrations (name, applied_at) VALUES (?, ?)",
            ("durable_story_usage_v1", now),
        )


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
        owner_job_number = _take_job_number(db, owner_id)
        cursor = db.execute(
            """INSERT INTO jobs
            (owner_id, owner_job_number, kind, status, topic, minutes, title, description, hashtags,
             platforms, scheduled_at, source_path, created_at, updated_at)
            VALUES (?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id""",
            (
                owner_id, owner_job_number, values["kind"],
                values.get("topic"), values.get("minutes"),
                values["title"], values.get("description", ""),
                values.get("hashtags", ""), json.dumps(values["platforms"]),
                values.get("scheduled_at"), values.get("source_path"), now, now,
            ),
        )
        job_id = cursor.fetchone()["id"]
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
    period_start = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(
        timespec="seconds"
    )
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
                """SELECT COUNT(*) AS total FROM story_usage WHERE user_id = ?
                AND outcome != 'released' AND created_at >= ?""",
                (owner_id, period_start),
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

        owner_job_number = _take_job_number(db, owner_id)
        cursor = db.execute(
            """INSERT INTO jobs
            (owner_id, owner_job_number, kind, status, topic, minutes, title, description, hashtags,
             platforms, scheduled_at, source_path, narration_voice, max_images,
             creator_niche, target_audience, content_style,
             created_at, updated_at)
            VALUES (?, ?, 'automatic', 'queued', ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id""",
            (
                owner_id,
                owner_job_number,
                topic,
                minutes,
                title,
                description,
                hashtags,
                json.dumps(platforms),
                scheduled_at,
                user["narration_voice"],
                user["max_images_per_job"],
                user["creator_niche"],
                user["target_audience"],
                user["content_style"],
                now,
                now,
            ),
        )
        job_id = int(cursor.fetchone()["id"])
        db.execute(
            """INSERT INTO story_usage
            (job_id, user_id, outcome, created_at, updated_at)
            VALUES (?, ?, 'pending', ?, ?)""",
            (job_id, owner_id, now, now),
        )
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
        owner_job_number = _take_job_number(db, owner_id)
        cursor = db.execute(
            """INSERT INTO jobs
            (owner_id, owner_job_number, kind, status, topic, minutes, title, description, hashtags,
             platforms, scheduled_at, source_path, video_path, created_at, updated_at)
            VALUES (?, ?, 'manual', 'completed', NULL, NULL, ?, ?, ?, '[]', NULL, ?, ?, ?, ?)
            RETURNING id""",
            (owner_id, owner_job_number, title, description, hashtags,
             source_path, source_path, now, now),
        )
        media_id = int(cursor.fetchone()["id"])
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


def list_showcase_jobs(limit: int = 60):
    """Return finished generated stories for the signed-in creator gallery."""
    with connect() as db:
        return db.execute(
            """SELECT j.*, COALESCE(NULLIF(u.channel_name, ''), NULLIF(u.name, ''),
            'Sleep Studio creator') AS creator_name
            FROM jobs j
            JOIN users u ON u.id=j.owner_id
            WHERE j.kind='automatic'
            AND j.status IN ('completed', 'published', 'waiting_for_connections')
            AND j.video_path IS NOT NULL AND j.video_path != ''
            AND u.status='active'
            ORDER BY j.updated_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()


def get_showcase_job(job_id: int):
    with connect() as db:
        return db.execute(
            """SELECT j.*, COALESCE(NULLIF(u.channel_name, ''), NULLIF(u.name, ''),
            'Sleep Studio creator') AS creator_name
            FROM jobs j
            JOIN users u ON u.id=j.owner_id
            WHERE j.id=? AND j.kind='automatic'
            AND j.status IN ('completed', 'published', 'waiting_for_connections')
            AND j.video_path IS NOT NULL AND j.video_path != ''
            AND u.status='active'""",
            (job_id,),
        ).fetchone()


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


def get_job_feedback(job_id: int):
    with connect() as db:
        return db.execute(
            "SELECT * FROM job_feedback WHERE job_id = ?", (job_id,)
        ).fetchone()


def save_job_feedback(
    *, job_id: int, user_id: int, rating: int, comment: str,
    public_display_name: str, public_consent: bool,
) -> None:
    now = utc_now()
    with connect() as db:
        db.execute(
            """INSERT INTO job_feedback
            (job_id, user_id, rating, comment, public_display_name,
             public_consent, approved, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, FALSE, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                rating=EXCLUDED.rating,
                comment=EXCLUDED.comment,
                public_display_name=EXCLUDED.public_display_name,
                public_consent=EXCLUDED.public_consent,
                approved=FALSE,
                updated_at=EXCLUDED.updated_at
            WHERE job_feedback.user_id=EXCLUDED.user_id""",
            (job_id, user_id, rating, comment, public_display_name,
             public_consent, now, now),
        )


def set_job_feedback_approved(job_id: int, approved: bool) -> None:
    with connect() as db:
        db.execute(
            "UPDATE job_feedback SET approved=?, updated_at=? WHERE job_id=?",
            (approved, utc_now(), job_id),
        )


def list_public_testimonials(limit: int = 6):
    with connect() as db:
        return db.execute(
            """SELECT rating, comment, public_display_name
            FROM job_feedback
            WHERE approved=TRUE AND public_consent=TRUE AND comment != ''
            ORDER BY updated_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()


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
        subscription = db.execute(
            """SELECT * FROM subscriptions WHERE user_id=? AND status='active'
            AND current_period_end IS NOT NULL AND current_period_end<=? FOR UPDATE""",
            (user_id, utc_now()),
        ).fetchone()
        if subscription:
            free = plan_for("free")
            db.execute(
                "UPDATE subscriptions SET status='expired', updated_at=? WHERE user_id=?",
                (utc_now(), user_id),
            )
            db.execute(
                """UPDATE users SET plan=?, monthly_job_limit=?, max_minutes_per_job=?,
                max_images_per_job=?, storage_limit_bytes=?, updated_at=?
                WHERE id=? AND role='creator'""",
                (free.key, free.monthly_jobs, free.max_minutes, free.max_images,
                 free.storage_gb * BYTES_PER_GB, utc_now(), user_id),
            )
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
    period_start = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(
        timespec="seconds"
    )
    with connect() as db:
        return int(db.execute(
            """SELECT COUNT(*) AS total FROM story_usage
            WHERE user_id=? AND outcome!='released' AND created_at>=?""",
            (user_id, period_start),
        ).fetchone()["total"])


def active_job_count(user_id: int) -> int:
    with connect() as db:
        return int(db.execute(
            """SELECT COUNT(*) AS total FROM jobs WHERE owner_id=?
            AND status IN ('queued','processing','publishing')""",
            (user_id,),
        ).fetchone()["total"])


def list_users():
    period_start = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(
        timespec="seconds"
    )
    with connect() as db:
        return db.execute(
            """SELECT u.*, s.status AS subscription_status,
            s.current_period_end, s.payment_reference,
            COUNT(j.id) AS total_jobs,
            (SELECT COUNT(*) FROM story_usage su WHERE su.user_id=u.id
             AND su.outcome!='released' AND su.created_at>=?) AS monthly_jobs,
            (SELECT COALESCE(SUM(ma.size_bytes), 0) FROM media_assets ma
             WHERE ma.owner_id=u.id) AS storage_used_bytes,
            SUM(CASE WHEN j.status IN ('queued','processing','publishing') THEN 1 ELSE 0 END) AS active_jobs
            FROM users u
            LEFT JOIN subscriptions s ON s.user_id=u.id
            LEFT JOIN jobs j ON j.owner_id=u.id
            GROUP BY u.id, s.id ORDER BY u.created_at DESC""",
            (period_start,),
        ).fetchall()


def update_user_limits(
    user_id: int, *, plan: str, monthly_job_limit: int,
    max_minutes_per_job: float, max_images_per_job: int,
    storage_limit_bytes: int, status: str
) -> None:
    with connect() as db:
        db.execute(
            """UPDATE users SET plan=?, monthly_job_limit=?, max_minutes_per_job=?,
            max_images_per_job=?, storage_limit_bytes=?, status=?, updated_at=?
            WHERE id=? AND role='creator'""",
            (plan, monthly_job_limit, max_minutes_per_job, max_images_per_job,
             storage_limit_bytes, status, utc_now(), user_id),
        )


def update_creator_settings(
    user_id: int, *, name: str, channel_name: str, creator_niche: str,
    target_audience: str, content_style: str, creator_goal: str,
    narration_voice: str, default_story_minutes: float
) -> None:
    with connect() as db:
        db.execute(
            """UPDATE users SET name=?, channel_name=?, creator_niche=?,
            target_audience=?, content_style=?, creator_goal=?, narration_voice=?,
            default_story_minutes=?, updated_at=? WHERE id=? AND role='creator'""",
            (name, channel_name, creator_niche, target_audience, content_style,
             creator_goal, narration_voice, default_story_minutes, utc_now(), user_id),
        )


def subscription_for_user(user_id: int):
    with connect() as db:
        return db.execute(
            "SELECT * FROM subscriptions WHERE user_id=?", (user_id,)
        ).fetchone()


def create_payment_attempt(
    *, reference: str, user_id: int, plan: str, amount_ngn: int
) -> None:
    now = utc_now()
    with connect() as db:
        db.execute(
            """INSERT INTO payment_attempts
            (reference, user_id, plan, amount_ngn, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'pending', ?, ?)""",
            (reference, user_id, plan, amount_ngn, now, now),
        )


def fail_payment_attempt(reference: str) -> None:
    with connect() as db:
        db.execute(
            """UPDATE payment_attempts SET status='failed', updated_at=?
            WHERE reference=? AND status='pending'""",
            (utc_now(), reference),
        )


def activate_paystack_payment(
    *, reference: str, event_id: str, event_type: str, amount_kobo: int,
    currency: str, provider_transaction_id: str, customer_code: str,
) -> bool:
    """Activate one verified Paystack payment exactly once."""
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat(timespec="seconds")
    with connect() as db:
        attempt = db.execute(
            "SELECT * FROM payment_attempts WHERE reference=? FOR UPDATE", (reference,)
        ).fetchone()
        if not attempt:
            raise ValueError("Unknown Paystack payment reference")
        if currency != "NGN" or amount_kobo != int(attempt["amount_ngn"]) * 100:
            raise ValueError("Paystack payment amount or currency does not match checkout")
        if attempt["status"] == "paid":
            return False
        event = db.execute(
            """INSERT INTO payment_events (event_id, event_type, processed_at)
            VALUES (?, ?, ?) ON CONFLICT DO NOTHING RETURNING event_id""",
            (event_id, event_type, now),
        ).fetchone()
        if not event:
            return False
        plan = plan_for(str(attempt["plan"]))
        if plan.key == "free":
            raise ValueError("Free plan cannot be activated by payment")
        existing_subscription = db.execute(
            "SELECT * FROM subscriptions WHERE user_id=? FOR UPDATE",
            (attempt["user_id"],),
        ).fetchone()
        period_start = now_dt
        if existing_subscription and existing_subscription["current_period_end"]:
            try:
                existing_end = datetime.fromisoformat(
                    str(existing_subscription["current_period_end"])
                )
                if existing_end > now_dt:
                    period_start = existing_end
            except (ValueError, TypeError):
                pass
        period_end = (period_start + timedelta(days=30)).isoformat(timespec="seconds")
        db.execute(
            """UPDATE payment_attempts SET status='paid', provider_transaction_id=?,
            updated_at=? WHERE reference=?""",
            (provider_transaction_id, now, reference),
        )
        db.execute(
            """INSERT INTO subscriptions
            (user_id, paystack_customer_code, payment_reference, plan, status,
             current_period_end, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'active', ?, ?, ?)
            ON CONFLICT (user_id) DO UPDATE SET
              paystack_customer_code=EXCLUDED.paystack_customer_code,
              payment_reference=EXCLUDED.payment_reference,
              plan=EXCLUDED.plan, status='active',
              current_period_end=EXCLUDED.current_period_end,
              updated_at=EXCLUDED.updated_at""",
            (attempt["user_id"], customer_code, reference, plan.key,
             period_end, now, now),
        )
        db.execute(
            """UPDATE users SET paystack_customer_code=?, plan=?, monthly_job_limit=?,
            max_minutes_per_job=?, max_images_per_job=?, storage_limit_bytes=?,
            updated_at=? WHERE id=?""",
            (customer_code, plan.key, plan.monthly_jobs, plan.max_minutes,
             plan.max_images, plan.storage_gb * BYTES_PER_GB, now,
             attempt["user_id"]),
        )
        return True


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


def record_media_asset(
    *, owner_id: int, reference: str, size_bytes: int, kind: str
) -> None:
    with connect() as db:
        db.execute(
            """INSERT INTO media_assets
            (reference, owner_id, size_bytes, kind, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(reference) DO UPDATE SET size_bytes=EXCLUDED.size_bytes""",
            (reference, owner_id, max(0, size_bytes), kind, utc_now()),
        )


def reserve_media_assets(
    owner_id: int, assets: list[tuple[str, int, str]]
) -> bool:
    """Atomically reserve storage so concurrent uploads cannot exceed the limit."""
    with connect() as db:
        user = db.execute(
            "SELECT storage_limit_bytes FROM users WHERE id=? FOR UPDATE", (owner_id,)
        ).fetchone()
        if not user:
            return False
        usage = int(db.execute(
            """SELECT COALESCE(SUM(size_bytes), 0) AS total
            FROM media_assets WHERE owner_id=?""",
            (owner_id,),
        ).fetchone()["total"])
        new_assets = []
        for reference, size_bytes, kind in assets:
            exists = db.execute(
                "SELECT 1 AS present FROM media_assets WHERE reference=?", (reference,)
            ).fetchone()
            if not exists:
                new_assets.append((reference, max(0, size_bytes), kind))
        if usage + sum(item[1] for item in new_assets) > int(user["storage_limit_bytes"]):
            return False
        now = utc_now()
        for reference, size_bytes, kind in new_assets:
            db.execute(
                """INSERT INTO media_assets
                (reference, owner_id, size_bytes, kind, created_at)
                VALUES (?, ?, ?, ?, ?) ON CONFLICT(reference) DO NOTHING""",
                (reference, owner_id, size_bytes, kind, now),
            )
        return True


def remove_media_asset(reference: str | None) -> None:
    if not reference:
        return
    with connect() as db:
        db.execute("DELETE FROM media_assets WHERE reference=?", (reference,))


def media_asset_recorded(reference: str) -> bool:
    with connect() as db:
        return bool(db.execute(
            "SELECT 1 AS present FROM media_assets WHERE reference=?", (reference,)
        ).fetchone())


def storage_usage_bytes(user_id: int) -> int:
    with connect() as db:
        row = db.execute(
            """SELECT COALESCE(SUM(size_bytes), 0) AS total
            FROM media_assets WHERE owner_id=?""",
            (user_id,),
        ).fetchone()
        return int(row["total"])


def can_add_storage(user_id: int, additional_bytes: int) -> bool:
    with connect() as db:
        user = db.execute(
            "SELECT storage_limit_bytes FROM users WHERE id=?", (user_id,)
        ).fetchone()
        if not user:
            return False
        usage = db.execute(
            """SELECT COALESCE(SUM(size_bytes), 0) AS total
            FROM media_assets WHERE owner_id=?""",
            (user_id,),
        ).fetchone()
        return int(usage["total"]) + max(0, additional_bytes) <= int(
            user["storage_limit_bytes"]
        )


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
        if status in {"completed", "published", "waiting_for_connections"}:
            db.execute(
                "UPDATE story_usage SET outcome='completed', updated_at=? WHERE job_id=?",
                (utc_now(), job_id),
            )
        elif status == "failed":
            db.execute(
                "UPDATE story_usage SET outcome='released', updated_at=? WHERE job_id=?",
                (utc_now(), job_id),
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
        db.execute(
            "UPDATE story_usage SET outcome='pending', updated_at=? WHERE job_id=?",
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
            db.execute(
                """UPDATE story_usage SET outcome='released', updated_at=?
                WHERE job_id=? AND outcome='pending'""",
                (utc_now(), job_id),
            )
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
            f"""UPDATE story_usage SET outcome='released', updated_at=?
            WHERE job_id IN ({placeholders}) AND outcome='pending'""",
            (now, *unique_ids),
        )
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
        db.execute(
            """UPDATE story_usage SET outcome='released', updated_at=?
            WHERE job_id=? AND outcome='pending'""",
            (utc_now(), job_id),
        )
        db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))


def add_notification(user_id: int, kind: str, message: str) -> None:
    with connect() as db:
        db.execute(
            """INSERT INTO notifications (user_id, kind, message, created_at)
            VALUES (?, ?, ?, ?)""",
            (user_id, kind[:40], message[:500], utc_now()),
        )


def list_notifications(user_id: int, limit: int = 20):
    with connect() as db:
        return db.execute(
            "SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()


def add_notification_once(user_id: int, kind: str, message: str) -> None:
    with connect() as db:
        existing = db.execute(
            "SELECT 1 AS present FROM notifications WHERE user_id=? AND kind=? AND message=?",
            (user_id, kind[:40], message[:500]),
        ).fetchone()
        if not existing:
            db.execute(
                """INSERT INTO notifications (user_id, kind, message, created_at)
                VALUES (?, ?, ?, ?)""",
                (user_id, kind[:40], message[:500], utc_now()),
            )


def mark_notifications_read(user_id: int) -> None:
    with connect() as db:
        db.execute(
            "UPDATE notifications SET read_at=? WHERE user_id=? AND read_at IS NULL",
            (utc_now(), user_id),
        )


def create_expiry_notifications() -> None:
    now_dt = datetime.now(timezone.utc)
    cutoff = (now_dt + timedelta(days=7)).isoformat(timespec="seconds")
    now = now_dt.isoformat(timespec="seconds")
    with connect() as db:
        subscriptions = db.execute(
            """SELECT user_id, plan, current_period_end FROM subscriptions
            WHERE status='active' AND current_period_end>? AND current_period_end<=?""",
            (now, cutoff),
        ).fetchall()
        for subscription in subscriptions:
            message = (
                f"Your {str(subscription['plan']).title()} access ends on "
                f"{str(subscription['current_period_end'])[:10]}. Renew from Pricing "
                "if you want uninterrupted access."
            )
            existing = db.execute(
                """SELECT 1 AS present FROM notifications
                WHERE user_id=? AND kind='billing' AND message=?""",
                (subscription["user_id"], message),
            ).fetchone()
            if not existing:
                db.execute(
                    """INSERT INTO notifications (user_id, kind, message, created_at)
                    VALUES (?, 'billing', ?, ?)""",
                    (subscription["user_id"], message, now),
                )


def record_audit(actor_user_id: int | None, action: str, target_type: str,
                 target_id: str = "", detail: str = "") -> None:
    with connect() as db:
        db.execute(
            """INSERT INTO audit_events
            (actor_user_id, action, target_type, target_id, detail, created_at)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (actor_user_id, action[:80], target_type[:40], target_id[:100],
             detail[:1000], utc_now()),
        )


def list_audit_events(limit: int = 100):
    with connect() as db:
        return db.execute(
            """SELECT a.*, u.email AS actor_email FROM audit_events a
            LEFT JOIN users u ON u.id=a.actor_user_id ORDER BY a.id DESC LIMIT ?""",
            (limit,),
        ).fetchall()


def user_media_references(user_id: int) -> list[str]:
    with connect() as db:
        rows = db.execute(
            "SELECT video_path, source_path FROM jobs WHERE owner_id=?", (user_id,)
        ).fetchall()
    return [str(value) for row in rows for value in (row["video_path"], row["source_path"])
            if value]


def payment_history(user_id: int | None = None, limit: int = 100):
    with connect() as db:
        where = " WHERE p.user_id=?" if user_id is not None else ""
        params = (user_id, limit) if user_id is not None else (limit,)
        return db.execute(
            f"""SELECT p.*, u.email FROM payment_attempts p
            JOIN users u ON u.id=p.user_id{where}
            ORDER BY p.created_at DESC LIMIT ?""",
            params,
        ).fetchall()


def payment_attempt(reference: str):
    with connect() as db:
        return db.execute(
            "SELECT * FROM payment_attempts WHERE reference=?", (reference,)
        ).fetchone()


def confirmed_revenue_ngn() -> int:
    with connect() as db:
        row = db.execute(
            "SELECT COALESCE(SUM(amount_ngn), 0) AS total FROM payment_attempts WHERE status='paid'"
        ).fetchone()
        return int(row["total"])


def revoke_disputed_payment(reference: str) -> int | None:
    """Remove access only when the disputed payment is the active entitlement."""
    now = utc_now()
    free = plan_for("free")
    with connect() as db:
        subscription = db.execute(
            "SELECT * FROM subscriptions WHERE payment_reference=? FOR UPDATE",
            (reference,),
        ).fetchone()
        if not subscription or subscription["status"] != "active":
            return None
        db.execute(
            "UPDATE subscriptions SET status='disputed', updated_at=? WHERE id=?",
            (now, subscription["id"]),
        )
        db.execute(
            """UPDATE users SET plan=?, monthly_job_limit=?, max_minutes_per_job=?,
            max_images_per_job=?, storage_limit_bytes=?, updated_at=? WHERE id=?""",
            (free.key, free.monthly_jobs, free.max_minutes, free.max_images,
             free.storage_gb * BYTES_PER_GB, now, subscription["user_id"]),
        )
        return int(subscription["user_id"])


def user_export(user_id: int) -> dict:
    with connect() as db:
        user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        jobs = db.execute(
            """SELECT owner_job_number, kind, status, topic, minutes, title,
            description, hashtags, created_at, updated_at FROM jobs
            WHERE owner_id=? ORDER BY id""",
            (user_id,),
        ).fetchall()
        payments = db.execute(
            """SELECT reference, plan, amount_ngn, status, created_at, updated_at
            FROM payment_attempts WHERE user_id=? ORDER BY created_at""",
            (user_id,),
        ).fetchall()
        feedback = db.execute(
            """SELECT job_id, rating, comment, public_display_name, public_consent,
            approved, created_at, updated_at FROM job_feedback
            WHERE user_id=? ORDER BY created_at""",
            (user_id,),
        ).fetchall()
        stored_media = db.execute(
            """SELECT reference, size_bytes, kind, created_at FROM media_assets
            WHERE owner_id=? ORDER BY created_at""",
            (user_id,),
        ).fetchall()
        return {"account": dict(user) if user else {},
                "jobs": [dict(row) for row in jobs],
                "payments": [dict(row) for row in payments],
                "feedback": [dict(row) for row in feedback],
                "stored_media": [dict(row) for row in stored_media]}


def delete_creator(user_id: int) -> None:
    with connect() as db:
        db.execute("DELETE FROM jobs WHERE owner_id=?", (user_id,))
        db.execute("DELETE FROM users WHERE id=? AND role='creator'", (user_id,))


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
