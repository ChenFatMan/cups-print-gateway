from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from . import states


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso_now() -> str:
    return utcnow().isoformat()


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def create_task(self, *, created_by: str, source_filename: str, source_mime: str, expires_at: str) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO print_tasks (
                    created_by, status, source_filename, source_mime,
                    requires_preview_confirmation, created_at, updated_at, expires_at
                )
                VALUES (?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (created_by, states.UPLOADED, source_filename, source_mime, iso_now(), iso_now(), expires_at),
            )
            task_id = int(cursor.lastrowid)
            add_event(conn, task_id, "task_created", states.UPLOADED, actor=created_by)
            return task_id

    def get_task(self, task_id: int) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM print_tasks WHERE id = ?", (task_id,)).fetchone()

    def list_tasks(self, *, include_history: bool = False) -> list[sqlite3.Row]:
        with self.connect() as conn:
            if include_history:
                return list(conn.execute("SELECT * FROM print_tasks ORDER BY id DESC"))
            placeholders = ",".join("?" for _ in states.HISTORY_STATES)
            return list(
                conn.execute(
                    f"SELECT * FROM print_tasks WHERE status NOT IN ({placeholders}) ORDER BY id DESC",
                    tuple(states.HISTORY_STATES),
                )
            )

    def set_task_file(
        self,
        *,
        task_id: int,
        kind: str,
        storage_path: str,
        filename: str,
        mime: str,
        size_bytes: int,
        sha256: str,
    ) -> None:
        now = iso_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO task_files (
                    task_id, kind, storage_path, filename, mime, size_bytes,
                    sha256, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id, kind) DO UPDATE SET
                    storage_path = excluded.storage_path,
                    filename = excluded.filename,
                    mime = excluded.mime,
                    size_bytes = excluded.size_bytes,
                    sha256 = excluded.sha256,
                    deleted_at = NULL,
                    updated_at = excluded.updated_at
                """,
                (task_id, kind, storage_path, filename, mime, size_bytes, sha256, now, now),
            )
            add_event(conn, task_id, f"{kind}_file_saved", None, actor="server")

    def get_task_file(self, task_id: int, kind: str) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM task_files WHERE task_id = ? AND kind = ? AND deleted_at IS NULL",
                (task_id, kind),
            ).fetchone()

    def list_task_events(self, task_id: int) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(conn.execute("SELECT * FROM task_events WHERE task_id = ? ORDER BY id ASC", (task_id,)))

    def update_status(self, task_id: int, status: str, *, actor: str, message: str | None = None) -> None:
        now = iso_now()
        with self.connect() as conn:
            conn.execute(
                "UPDATE print_tasks SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, task_id),
            )
            add_event(conn, task_id, "status_changed", status, actor=actor, message=message)

    def mark_for_conversion(self, task_id: int, *, requires_confirmation: bool) -> None:
        now = iso_now()
        status = states.QUEUED_FOR_CONVERSION
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE print_tasks
                SET status = ?, requires_preview_confirmation = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, int(requires_confirmation), now, task_id),
            )
            add_event(conn, task_id, "status_changed", status, actor="server")

    def confirm_preview(
        self,
        task_id: int,
        *,
        printer_id: str,
        print_options: dict[str, Any],
        confirmed_by: str,
    ) -> None:
        now = iso_now()
        with self.connect() as conn:
            task = conn.execute("SELECT * FROM print_tasks WHERE id = ?", (task_id,)).fetchone()
            if task is None:
                raise ValueError("task not found")
            if task["status"] not in {states.PREVIEW_READY, states.WAITING_USER_CONFIRM}:
                raise ValueError(f"task is not ready for print: {task['status']}")
            printer = conn.execute("SELECT id FROM printers WHERE id = ?", (printer_id,)).fetchone()
            if printer is None:
                raise ValueError("selected printer is not available")
            converted = conn.execute(
                "SELECT sha256 FROM task_files WHERE task_id = ? AND kind = 'converted_pdf' AND deleted_at IS NULL",
                (task_id,),
            ).fetchone()
            if converted is None:
                raise ValueError("converted PDF is missing")
            options_json = json.dumps(print_options, ensure_ascii=False, sort_keys=True)
            conn.execute(
                """
                UPDATE print_tasks
                SET status = ?, preview_confirmed_at = ?, printer_id = ?,
                    print_options_json = ?, target_pdf_sha256 = ?,
                    lease_id = NULL, lease_owner_agent_id = NULL,
                    lease_expires_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                (
                    states.QUEUED_FOR_PRINT,
                    now,
                    printer_id,
                    options_json,
                    converted["sha256"],
                    now,
                    task_id,
                ),
            )
            add_event(conn, task_id, "preview_confirmed", states.QUEUED_FOR_PRINT, actor=confirmed_by)

    def lease_task(self, *, agent_id: str, lease_seconds: int) -> sqlite3.Row | None:
        now = utcnow()
        expires_at = (now + timedelta(seconds=lease_seconds)).isoformat()
        lease_id = f"{agent_id}-{int(now.timestamp() * 1000)}"
        with self.connect() as conn:
            task = conn.execute(
                """
                SELECT * FROM print_tasks
                WHERE status IN (?, ?)
                  AND (lease_expires_at IS NULL OR lease_expires_at < ?)
                ORDER BY id ASC
                LIMIT 1
                """,
                (states.QUEUED_FOR_CONVERSION, states.QUEUED_FOR_PRINT, now.isoformat()),
            ).fetchone()
            if task is None:
                return None
            next_status = states.CONVERTING if task["status"] == states.QUEUED_FOR_CONVERSION else states.PRINTING
            conn.execute(
                """
                UPDATE print_tasks
                SET status = ?, lease_id = ?, lease_owner_agent_id = ?,
                    lease_expires_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (next_status, lease_id, agent_id, expires_at, now.isoformat(), task["id"]),
            )
            add_event(conn, task["id"], "task_leased", next_status, actor=agent_id, lease_id=lease_id)
            return conn.execute("SELECT * FROM print_tasks WHERE id = ?", (task["id"],)).fetchone()

    def record_agent_event(
        self,
        *,
        task_id: int,
        agent_id: str,
        lease_id: str,
        event_seq: int,
        event_type: str,
        status: str | None,
        message: str | None,
    ) -> None:
        now = iso_now()
        with self.connect() as conn:
            task = conn.execute("SELECT * FROM print_tasks WHERE id = ?", (task_id,)).fetchone()
            if task is None:
                raise ValueError("task not found")
            require_active_lease(task, agent_id=agent_id, lease_id=lease_id)
            existing = conn.execute(
                """
                SELECT id FROM task_events
                WHERE task_id = ? AND lease_id = ? AND agent_id = ? AND event_seq = ?
                """,
                (task_id, lease_id, agent_id, event_seq),
            ).fetchone()
            if existing is not None:
                return
            if status:
                conn.execute("UPDATE print_tasks SET status = ?, updated_at = ? WHERE id = ?", (status, now, task_id))
            add_event(
                conn,
                task_id,
                event_type,
                status,
                actor=agent_id,
                message=message,
                lease_id=lease_id,
                agent_id=agent_id,
                event_seq=event_seq,
            )

    def record_cups_job(
        self,
        *,
        task_id: int,
        agent_id: str,
        lease_id: str,
        cups_job_id: str,
        status: str,
        message: str | None,
    ) -> None:
        now = iso_now()
        with self.connect() as conn:
            task = conn.execute("SELECT * FROM print_tasks WHERE id = ?", (task_id,)).fetchone()
            if task is None:
                raise ValueError("task not found")
            require_active_lease(task, agent_id=agent_id, lease_id=lease_id)
            if task["cups_job_id"] and task["cups_job_id"] != cups_job_id:
                raise ValueError("task already has a different CUPS job id")
            conn.execute(
                """
                UPDATE print_tasks
                SET cups_job_id = ?, status = ?, updated_at = ?
                WHERE id = ?
                """,
                (cups_job_id, status, now, task_id),
            )
            add_event(conn, task_id, "cups_job_recorded", status, actor=agent_id, message=message, lease_id=lease_id, agent_id=agent_id)

    def mark_files_deleted(self, task_id: int, *, reason: str) -> None:
        now = iso_now()
        with self.connect() as conn:
            conn.execute(
                "UPDATE print_tasks SET files_deleted_at = ?, files_delete_reason = ?, updated_at = ? WHERE id = ?",
                (now, reason, now, task_id),
            )
            conn.execute(
                "UPDATE task_files SET deleted_at = ?, updated_at = ? WHERE task_id = ? AND deleted_at IS NULL",
                (now, now, task_id),
            )
            add_event(conn, task_id, "files_deleted", None, actor="server", message=reason)


def require_active_lease(task: sqlite3.Row, *, agent_id: str, lease_id: str) -> None:
    if task["lease_owner_agent_id"] != agent_id or task["lease_id"] != lease_id:
        raise ValueError("lease does not belong to this agent")
    expires = parse_time(task["lease_expires_at"])
    if expires is None or expires < utcnow():
        raise ValueError("lease has expired")


def add_event(
    conn: sqlite3.Connection,
    task_id: int,
    event_type: str,
    status: str | None,
    *,
    actor: str,
    message: str | None = None,
    lease_id: str | None = None,
    agent_id: str | None = None,
    event_seq: int | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO task_events (
            task_id, event_type, status, actor, message, lease_id,
            agent_id, event_seq, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (task_id, event_type, status, actor, message, lease_id, agent_id, event_seq, iso_now()),
    )


SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    token_label TEXT,
    version TEXT,
    hostname TEXT,
    last_heartbeat_at TEXT,
    status TEXT NOT NULL DEFAULT 'offline',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS printers (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    cups_name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    capabilities_json TEXT NOT NULL DEFAULT '{}',
    capability_version TEXT NOT NULL,
    last_synced_at TEXT NOT NULL,
    FOREIGN KEY(agent_id) REFERENCES agents(id)
);

CREATE TABLE IF NOT EXISTS print_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_by TEXT NOT NULL,
    status TEXT NOT NULL,
    source_filename TEXT NOT NULL,
    source_mime TEXT NOT NULL,
    requires_preview_confirmation INTEGER NOT NULL DEFAULT 0,
    preview_confirmed_at TEXT,
    printer_id TEXT,
    print_options_json TEXT,
    target_pdf_sha256 TEXT,
    lease_id TEXT,
    lease_owner_agent_id TEXT,
    lease_expires_at TEXT,
    cups_job_id TEXT,
    last_error_code TEXT,
    last_error_message TEXT,
    files_deleted_at TEXT,
    files_delete_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    filename TEXT NOT NULL,
    mime TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    deleted_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(task_id, kind),
    FOREIGN KEY(task_id) REFERENCES print_tasks(id)
);

CREATE TABLE IF NOT EXISTS task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    status TEXT,
    actor TEXT NOT NULL,
    message TEXT,
    lease_id TEXT,
    agent_id TEXT,
    event_seq INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY(task_id) REFERENCES print_tasks(id),
    UNIQUE(task_id, lease_id, agent_id, event_seq)
);
"""
