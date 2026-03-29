from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
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
