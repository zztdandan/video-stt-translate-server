"""任务队列仓储层：入队、领取、状态流转与查询。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import uuid

from whisper_stt_service.db import Database


STAGES = ("extract", "stt", "translate")


def _now() -> str:
    """返回当前 UTC 时间的 ISO8601 字符串。"""

    return datetime.now(timezone.utc).isoformat()


def _lease_expire(sec: int) -> str:
    """根据租约秒数计算过期时间戳。"""

    return (datetime.now(timezone.utc) + timedelta(seconds=sec)).isoformat()


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


@dataclass
class TaskExecutionContext:
    """worker 执行任务时需要的上下文。"""

    task_id: str
    job_id: str
    stage: str
    video_path: str
    source_language: str
    output_ja_path: str
    output_zh_path: str
    timeout_sec: int
    attempt: int
    max_retries: int
    log_dir: str
    log_file: str


class JobRepository:
    """基于 SQLite 的 job/task 仓储实现。"""

    def __init__(
        self,
        db: Database,
        *,
        stage_max_retries: dict[str, int] | None = None,
        stage_timeouts: dict[str, int] | None = None,
        log_root: Path | None = None,
    ) -> None:
        """注入数据库访问对象与可选阶段默认配置。"""

        self.db = db
        self.stage_max_retries = stage_max_retries or {
            "extract": 2,
            "stt": 2,
            "translate": 2,
        }
        self.stage_timeouts = stage_timeouts or {
            "extract": 1200,
            "stt": 7200,
            "translate": 7200,
        }
        self.log_root = log_root or Path("./tmp/logs")

    def _count_queue_ahead(self, conn, now: str) -> int:
        """统计当前排在新任务前方的 job 数量。"""

        row = conn.execute(
            """
            SELECT COUNT(1) AS c
            FROM jobs
            WHERE status IN ('queued', 'running') AND created_at < ?
            """,
            (now,),
        ).fetchone()
        return int(row["c"]) if row is not None else 0

    def _build_log_paths(
        self, job_id: str, stage: str, task_id: str
    ) -> tuple[str, str]:
        """按规范生成任务日志目录与日志文件路径。"""

        log_dir = self.log_root / job_id / stage / task_id
        return str(log_dir), str(log_dir / "task.log")

    def enqueue(self, video_path: str, language: str) -> EnqueueResult:
        """执行同路径幂等/拒绝判定，并在需要时创建 1 job + 3 tasks。"""

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

            queue_ahead = self._count_queue_ahead(conn, now)
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
            task_chain = [
                (extract_id, "extract", None),
                (stt_id, "stt", extract_id),
                (tr_id, "translate", stt_id),
            ]
            for task_id, stage, dep in task_chain:
                log_dir, log_file = self._build_log_paths(job_id, stage, task_id)
                conn.execute(
                    "INSERT INTO tasks(task_id,job_id,stage,status,depends_on_task_id,max_retries,timeout_sec,log_dir,log_file,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        task_id,
                        job_id,
                        stage,
                        "queued",
                        dep,
                        int(self.stage_max_retries.get(stage, 2)),
                        int(self.stage_timeouts.get(stage, 3600)),
                        log_dir,
                        log_file,
                        now,
                        now,
                    ),
                )
            return EnqueueResult(job_id, True, "created", queue_ahead)

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
            self._refresh_job_status(conn, row["job_id"])
            return ClaimedTask(
                task_id=row["task_id"],
                job_id=row["job_id"],
                stage=row["stage"],
            )

    def get_task_execution_context(self, task_id: str) -> TaskExecutionContext | None:
        """查询 worker 执行某任务所需的上下文。"""

        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    t.task_id,
                    t.job_id,
                    t.stage,
                    t.timeout_sec,
                    t.attempt,
                    t.max_retries,
                    t.log_dir,
                    t.log_file,
                    j.video_path,
                    j.source_language,
                    j.output_ja_path,
                    j.output_zh_path
                FROM tasks t
                JOIN jobs j ON j.job_id=t.job_id
                WHERE t.task_id=?
                """,
                (task_id,),
            ).fetchone()
            if row is None:
                return None
            return TaskExecutionContext(
                task_id=row["task_id"],
                job_id=row["job_id"],
                stage=row["stage"],
                video_path=row["video_path"],
                source_language=row["source_language"],
                output_ja_path=row["output_ja_path"],
                output_zh_path=row["output_zh_path"],
                timeout_sec=int(row["timeout_sec"]),
                attempt=int(row["attempt"]),
                max_retries=int(row["max_retries"]),
                log_dir=row["log_dir"],
                log_file=row["log_file"],
            )

    def _refresh_job_status(self, conn, job_id: str) -> str:
        """按三阶段任务聚合刷新 job 状态。"""

        rows = conn.execute(
            "SELECT status FROM tasks WHERE job_id=?",
            (job_id,),
        ).fetchall()
        statuses = [r["status"] for r in rows]
        job_status = "queued"
        finished_at: str | None = None
        if statuses and all(s == "succeeded" for s in statuses):
            job_status = "succeeded"
            finished_at = _now()
        elif any(s == "failed" for s in statuses):
            job_status = "failed"
            finished_at = _now()
        elif any(s == "claimed" for s in statuses):
            job_status = "running"

        conn.execute(
            "UPDATE jobs SET status=?, updated_at=?, finished_at=COALESCE(?, finished_at) WHERE job_id=?",
            (job_status, _now(), finished_at, job_id),
        )
        return job_status

    def mark_task_succeeded(self, task_id: str) -> None:
        """把任务标记为 succeeded 并写入完成时间。"""

        with self.db.tx() as conn:
            row = conn.execute(
                "SELECT job_id FROM tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if row is None:
                return
            conn.execute(
                "UPDATE tasks SET status='succeeded', finished_at=?, lease_owner=NULL, lease_expires_at=NULL, updated_at=? WHERE task_id=?",
                (_now(), _now(), task_id),
            )
            self._refresh_job_status(conn, row["job_id"])

    def mark_task_failed(self, task_id: str, error_message: str) -> str:
        """失败后按重试策略更新任务状态，并返回新状态。"""

        with self.db.tx() as conn:
            row = conn.execute(
                "SELECT job_id, attempt, max_retries FROM tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if row is None:
                return "missing"
            next_attempt = int(row["attempt"]) + 1
            if next_attempt <= int(row["max_retries"]):
                conn.execute(
                    "UPDATE tasks SET status='queued', attempt=?, last_error=?, lease_owner=NULL, lease_expires_at=NULL, updated_at=? WHERE task_id=?",
                    (next_attempt, error_message[:2048], _now(), task_id),
                )
                status = "queued"
            else:
                conn.execute(
                    "UPDATE tasks SET status='failed', attempt=?, last_error=?, finished_at=?, lease_owner=NULL, lease_expires_at=NULL, updated_at=? WHERE task_id=?",
                    (next_attempt, error_message[:2048], _now(), _now(), task_id),
                )
                status = "failed"
            self._refresh_job_status(conn, row["job_id"])
            return status

    def rollback_claimed_task(
        self, task_id: str, reason: str = "shutdown_rollback"
    ) -> None:
        """把单个 claimed 任务回退到 queued。"""

        with self.db.tx() as conn:
            row = conn.execute(
                "SELECT job_id FROM tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if row is None:
                return
            conn.execute(
                "UPDATE tasks SET status='queued', lease_owner=NULL, lease_expires_at=NULL, last_error=?, updated_at=? WHERE task_id=? AND status='claimed'",
                (reason, _now(), task_id),
            )
            self._refresh_job_status(conn, row["job_id"])

    def recover_claimed_to_queued(self) -> int:
        """服务启动时把全部 claimed 回退为 queued。"""

        with self.db.tx() as conn:
            changed = conn.execute(
                "UPDATE tasks SET status='queued', lease_owner=NULL, lease_expires_at=NULL, updated_at=? WHERE status='claimed'",
                (_now(),),
            ).rowcount
            # 对所有可能受影响的 job 重新聚合，避免状态残留为 running。
            job_ids = [
                r["job_id"] for r in conn.execute("SELECT job_id FROM jobs").fetchall()
            ]
            for job_id in job_ids:
                self._refresh_job_status(conn, job_id)
            return int(changed)

    def get_job_detail(self, job_id: str) -> dict | None:
        """查询 job 元信息与三阶段任务明细。"""

        with self.db.connect() as conn:
            job = conn.execute(
                "SELECT * FROM jobs WHERE job_id=?",
                (job_id,),
            ).fetchone()
            if job is None:
                return None
            tasks = conn.execute(
                "SELECT task_id, stage, status, attempt, max_retries, claimed_at, started_at, finished_at, last_error, lease_owner, lease_expires_at, updated_at FROM tasks WHERE job_id=? ORDER BY created_at ASC, task_id ASC",
                (job_id,),
            ).fetchall()
            return {
                "job_id": job["job_id"],
                "video_path": job["video_path"],
                "source_language": job["source_language"],
                "status": job["status"],
                "output_ja_path": job["output_ja_path"],
                "output_zh_path": job["output_zh_path"],
                "created_at": job["created_at"],
                "updated_at": job["updated_at"],
                "finished_at": job["finished_at"],
                "tasks": [dict(t) for t in tasks],
            }

    def get_job_latest_by_path(self, video_path: str) -> dict | None:
        """按视频路径返回最新一条 job。"""

        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT job_id FROM jobs WHERE video_path=? ORDER BY created_at DESC LIMIT 1",
                (video_path,),
            ).fetchone()
            if row is None:
                return None
            return self.get_job_detail(row["job_id"])

    def list_jobs(
        self,
        *,
        page: int,
        page_size: int,
        status: str | None = None,
        video_path_like: str | None = None,
        created_from: str | None = None,
        created_to: str | None = None,
        language: str | None = None,
        has_failed_tasks: bool | None = None,
        sort_by: str = "created_at",
        order: str = "desc",
    ) -> dict:
        """分页查询 jobs，支持设计稿约定筛选字段。"""

        sort_key = sort_by if sort_by in {"created_at", "updated_at"} else "created_at"
        order_key = "ASC" if order.lower() == "asc" else "DESC"
        clauses: list[str] = []
        params: list[object] = []
        if status:
            clauses.append("j.status=?")
            params.append(status)
        if video_path_like:
            clauses.append("j.video_path LIKE ?")
            params.append(f"%{video_path_like}%")
        if created_from:
            clauses.append("j.created_at>=?")
            params.append(created_from)
        if created_to:
            clauses.append("j.created_at<=?")
            params.append(created_to)
        if language:
            clauses.append("j.source_language=?")
            params.append(language)
        if has_failed_tasks is True:
            clauses.append(
                "EXISTS (SELECT 1 FROM tasks tf WHERE tf.job_id=j.job_id AND tf.status='failed')"
            )
        if has_failed_tasks is False:
            clauses.append(
                "NOT EXISTS (SELECT 1 FROM tasks tf WHERE tf.job_id=j.job_id AND tf.status='failed')"
            )

        where_sql = ""
        if clauses:
            where_sql = " WHERE " + " AND ".join(clauses)

        offset = max(page - 1, 0) * page_size
        with self.db.connect() as conn:
            total_row = conn.execute(
                f"SELECT COUNT(1) AS c FROM jobs j{where_sql}",
                tuple(params),
            ).fetchone()
            total = int(total_row["c"]) if total_row is not None else 0
            rows = conn.execute(
                f"SELECT j.* FROM jobs j{where_sql} ORDER BY j.{sort_key} {order_key} LIMIT ? OFFSET ?",
                tuple(params + [page_size, offset]),
            ).fetchall()
            return {
                "items": [dict(r) for r in rows],
                "total": total,
                "page": page,
                "page_size": page_size,
            }

    def list_tasks(
        self,
        *,
        page: int,
        page_size: int,
        stage: str | None = None,
        status: str | None = None,
        job_id: str | None = None,
        lease_owner: str | None = None,
        updated_from: str | None = None,
        updated_to: str | None = None,
    ) -> dict:
        """分页查询 tasks，支持运维筛选。"""

        clauses: list[str] = []
        params: list[object] = []
        if stage:
            clauses.append("stage=?")
            params.append(stage)
        if status:
            clauses.append("status=?")
            params.append(status)
        if job_id:
            clauses.append("job_id=?")
            params.append(job_id)
        if lease_owner:
            clauses.append("lease_owner=?")
            params.append(lease_owner)
        if updated_from:
            clauses.append("updated_at>=?")
            params.append(updated_from)
        if updated_to:
            clauses.append("updated_at<=?")
            params.append(updated_to)
        where_sql = ""
        if clauses:
            where_sql = " WHERE " + " AND ".join(clauses)

        offset = max(page - 1, 0) * page_size
        with self.db.connect() as conn:
            total_row = conn.execute(
                f"SELECT COUNT(1) AS c FROM tasks{where_sql}",
                tuple(params),
            ).fetchone()
            total = int(total_row["c"]) if total_row is not None else 0
            rows = conn.execute(
                f"SELECT * FROM tasks{where_sql} ORDER BY updated_at DESC, task_id ASC LIMIT ? OFFSET ?",
                tuple(params + [page_size, offset]),
            ).fetchall()
            return {
                "items": [dict(r) for r in rows],
                "total": total,
                "page": page,
                "page_size": page_size,
            }

    def queue_summary(self) -> dict:
        """返回各阶段状态计数摘要。"""

        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT stage, status, COUNT(1) AS c FROM tasks GROUP BY stage, status"
            ).fetchall()
            stage_map: dict[str, dict[str, int]] = {
                stage: {"queued": 0, "claimed": 0, "succeeded": 0, "failed": 0}
                for stage in STAGES
            }
            for row in rows:
                stage = row["stage"]
                status = row["status"]
                if stage not in stage_map:
                    stage_map[stage] = {}
                stage_map[stage][status] = int(row["c"])
            return {"stages": stage_map}
