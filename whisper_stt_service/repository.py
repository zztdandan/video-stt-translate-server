from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import uuid

from whisper_stt_service.db import Database


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class EnqueueResult:
    job_id: str
    accepted: bool
    message: str
    queue_ahead: int


@dataclass
class ClaimedTask:
    task_id: str
    job_id: str
    stage: str


def _lease_expire(sec: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=sec)).isoformat()


class JobRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def enqueue(self, video_path: str, language: str) -> EnqueueResult:
        now = _now()
        with self.db.tx() as conn:
            row = conn.execute(
                "SELECT job_id FROM jobs WHERE video_path=? ORDER BY created_at DESC LIMIT 1",
                (video_path,),
            ).fetchone()
            if row is not None:
                latest_job_id = row["job_id"]
                statuses = [
                    r["status"]
                    for r in conn.execute(
                        "SELECT status FROM tasks WHERE job_id=? ORDER BY stage",
                        (latest_job_id,),
                    ).fetchall()
                ]
                if len(statuses) == 3 and all(s == "queued" for s in statuses):
                    return EnqueueResult(latest_job_id, False, "idempotent_returned", 0)
                if any(s in {"claimed", "succeeded", "failed"} for s in statuses):
                    return EnqueueResult(latest_job_id, False, "rejected_started", 0)

            job_id = str(uuid.uuid4())
            p = Path(video_path)
            ja = str(p.with_suffix(".ja.srt"))
            zh = str(p.with_suffix(".zh.srt"))
            conn.execute(
                "INSERT INTO jobs(job_id,video_path,source_language,status,output_ja_path,output_zh_path,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (job_id, video_path, language, "queued", ja, zh, now, now),
            )
            extract_id = str(uuid.uuid4())
            stt_id = str(uuid.uuid4())
            tr_id = str(uuid.uuid4())
            tasks = [
                (extract_id, job_id, "extract", "queued", None),
                (stt_id, job_id, "stt", "queued", extract_id),
                (tr_id, job_id, "translate", "queued", stt_id),
            ]
            for task_id, jid, stage, status, dep in tasks:
                conn.execute(
                    "INSERT INTO tasks(task_id,job_id,stage,status,depends_on_task_id,max_retries,timeout_sec,log_dir,log_file,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (task_id, jid, stage, status, dep, 2, 3600, "", "", now, now),
                )
            return EnqueueResult(job_id, True, "created", 0)

    def force_mark_any_stage_started(self, job_id: str) -> None:
        with self.db.tx() as conn:
            conn.execute(
                "UPDATE tasks SET status='claimed', started_at=?, updated_at=? WHERE job_id=? AND stage='extract'",
                (_now(), _now(), job_id),
            )

    def claim_next(
        self, stage: str, worker_id: str, lease_timeout_sec: int
    ) -> ClaimedTask | None:
        with self.db.tx() as conn:
            row = conn.execute(
                """
                SELECT t.task_id, t.job_id, t.stage
                FROM tasks t
                LEFT JOIN tasks d ON d.task_id = t.depends_on_task_id
                WHERE t.stage=? AND t.status='queued'
                  AND (t.depends_on_task_id IS NULL OR d.status='succeeded')
                ORDER BY t.created_at ASC, t.task_id ASC
                LIMIT 1
                """,
                (stage,),
            ).fetchone()
            if row is None:
                return None

            now = _now()
            changed = conn.execute(
                """
                UPDATE tasks
                SET status='claimed', lease_owner=?, lease_expires_at=?, claimed_at=?, started_at=?, updated_at=?
                WHERE task_id=? AND status='queued'
                """,
                (
                    worker_id,
                    _lease_expire(lease_timeout_sec),
                    now,
                    now,
                    now,
                    row["task_id"],
                ),
            ).rowcount
            if changed == 0:
                return None
            return ClaimedTask(
                task_id=row["task_id"], job_id=row["job_id"], stage=row["stage"]
            )
