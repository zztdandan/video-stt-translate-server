"""SQLite 访问与事务封装模块。"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path


class Database:
    """提供 SQLite 连接、建表和事务上下文能力。"""

    def __init__(self, db_path: Path) -> None:
        """记录数据库文件路径。"""
        self.db_path = db_path

    def connect(self) -> sqlite3.Connection:
        """创建具备 Row 访问能力的连接对象。"""

        conn = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        return conn

    def init_schema(self) -> None:
        """初始化 jobs 与 tasks 两张核心表。"""

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
                CREATE INDEX IF NOT EXISTS idx_jobs_video_created ON jobs(video_path, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs(status, created_at DESC);
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
                CREATE INDEX IF NOT EXISTS idx_tasks_stage_status_created ON tasks(stage, status, created_at);
                CREATE INDEX IF NOT EXISTS idx_tasks_job_stage ON tasks(job_id, stage);
                CREATE INDEX IF NOT EXISTS idx_tasks_status_lease_exp ON tasks(status, lease_expires_at);
                """
            )

    @contextmanager
    def tx(self):
        """开启 `BEGIN IMMEDIATE` 事务并自动提交/回滚。"""

        conn = self.connect()
        try:
            # 立即获取写锁，避免并发领取时出现竞争写入冲突。
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            # 任何异常均回滚，保证调用方看到一致状态。
            conn.rollback()
            raise
        finally:
            # 无论成功或失败，最终都释放连接资源。
            conn.close()
