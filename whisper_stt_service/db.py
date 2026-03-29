from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path


class Database:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    video_path TEXT NOT NULL,
                    source_language TEXT NOT NULL,
                    status TEXT NOT NULL,
                    output_ja_path TEXT NOT NULL,
                    output_zh_path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    finished_at TEXT
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    status TEXT NOT NULL,
                    depends_on_task_id TEXT,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    max_retries INTEGER NOT NULL,
                    timeout_sec INTEGER NOT NULL,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    claimed_at TEXT,
                    started_at TEXT,
                    finished_at TEXT,
                    last_error TEXT,
                    log_dir TEXT NOT NULL,
                    log_file TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    @contextmanager
    def tx(self):
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
