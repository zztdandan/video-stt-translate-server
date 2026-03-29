"""任务队列仓储层：入队、领取与状态流转。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import uuid

from whisper_stt_service.db import Database


def _now() -> str:
    """返回当前 UTC 时间的 ISO8601 字符串。"""

    return datetime.now(timezone.utc).isoformat()


@dataclass
class EnqueueResult:
    """入队结果载体。"""

    job_id: str
    accepted: bool
    message: str
    queue_ahead: int


@dataclass
class ClaimedTask:
    """领取到的任务最小信息。"""

    task_id: str
    job_id: str
    stage: str


def _lease_expire(sec: int) -> str:
    """根据租约秒数计算过期时间戳。"""

    return (datetime.now(timezone.utc) + timedelta(seconds=sec)).isoformat()


class JobRepository:
    """基于 SQLite 的 job/task 仓储实现。"""

    def __init__(self, db: Database) -> None:
        """注入数据库访问对象。"""

        self.db = db

    def enqueue(self, video_path: str, language: str) -> EnqueueResult:
        """执行同路径幂等/拒绝判定，并在需要时创建 1 job + 3 tasks。"""

        now = _now()
        with self.db.tx() as conn:
            # 只看同路径最新 job，用于执行“幂等返回/拒绝入队”规则。
            row = conn.execute(
                "SELECT job_id FROM jobs WHERE video_path=? ORDER BY created_at DESC LIMIT 1",
                (video_path,),
            ).fetchone()
            if row is not None:
                latest_job_id = row["job_id"]
                # 读取该 job 的三阶段状态，判断是否全部排队或已经开始/结束。
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

            # 未命中幂等/拒绝规则时，创建新的 job 及阶段链路任务。
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
                # 最小实现阶段统一写死重试/超时，后续可接入配置。
                conn.execute(
                    "INSERT INTO tasks(task_id,job_id,stage,status,depends_on_task_id,max_retries,timeout_sec,log_dir,log_file,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (task_id, jid, stage, status, dep, 2, 3600, "", "", now, now),
                )
            return EnqueueResult(job_id, True, "created", 0)

    def force_mark_any_stage_started(self, job_id: str) -> None:
        """测试辅助：强制把 extract 置为 claimed，模拟任务已开始。"""

        with self.db.tx() as conn:
            conn.execute(
                "UPDATE tasks SET status='claimed', started_at=?, updated_at=? WHERE job_id=? AND stage='extract'",
                (_now(), _now(), job_id),
            )

    def claim_next(
        self, stage: str, worker_id: str, lease_timeout_sec: int
    ) -> ClaimedTask | None:
        """原子领取指定阶段的下一个可执行任务。"""

        with self.db.tx() as conn:
            # 只选择依赖已完成且仍处于 queued 的任务，按 FIFO 领取。
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
            # 二次条件保护：只有 queued 才能更新为 claimed，防并发抢占。
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

    def mark_task_succeeded(self, task_id: str) -> None:
        """把任务标记为 succeeded 并写入完成时间。"""

        with self.db.tx() as conn:
            conn.execute(
                "UPDATE tasks SET status='succeeded', finished_at=?, updated_at=? WHERE task_id=?",
                (_now(), _now(), task_id),
            )
