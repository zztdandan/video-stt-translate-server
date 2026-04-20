"""任务队列仓储层：入队、领取、状态流转与查询。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import re
import sqlite3
import uuid

from whisper_stt_service.core.dag import (
    build_default_dag,
    normalize_and_validate_dag,
    normalize_and_validate_job_config,
)
from whisper_stt_service.repo.database import Database
from whisper_stt_service.core.stages import SUPPORTED_STAGES


STAGES = SUPPORTED_STAGES
_MAX_TASK_NAME_LEN = 64
_ID_RETRY_LIMIT = 32
_UUID_SUFFIX_LEN = 4
_NON_ALNUM_PATTERN = re.compile(r"[^0-9A-Za-z]+")


def _derive_task_name(video_path: str) -> str:
    """从视频文件名提取可读 task_name（仅字母数字，保留末尾 64 位）。"""

    stem = Path(video_path).stem
    cleaned = _NON_ALNUM_PATTERN.sub("", stem)
    if len(cleaned) > _MAX_TASK_NAME_LEN:
        cleaned = cleaned[-_MAX_TASK_NAME_LEN:]
    return cleaned or "unnamed"


def _readable_timestamp() -> str:
    """生成 `YYYYMMDDHHMMSS` 格式时间戳。"""

    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def _short_uuid_suffix() -> str:
    """生成 4 位十六进制短后缀。"""

    return uuid.uuid4().hex[:_UUID_SUFFIX_LEN]


def _build_readable_job_id(task_name: str, timestamp: str) -> str:
    """构造可读 job_id。"""

    return f"job-{task_name}-job-{timestamp}-{_short_uuid_suffix()}"


def _build_readable_task_id(task_name: str, stage: str, timestamp: str) -> str:
    """构造可读 task_id。"""

    return f"task-{task_name}-{stage}-{timestamp}-{_short_uuid_suffix()}"


def _is_id_collision_error(exc: sqlite3.IntegrityError) -> bool:
    """仅识别 job/task 主键冲突，用于触发可恢复重试。"""

    message = str(exc)
    return (
        "UNIQUE constraint failed: jobs.job_id" in message
        or "UNIQUE constraint failed: tasks.task_id" in message
    )


def _now() -> str:
    """返回当前 UTC 时间的 ISO8601 字符串。"""

    return datetime.now(timezone.utc).isoformat()


def _lease_expire(sec: int) -> str:
    """根据租约秒数计算过期时间戳。"""

    return (datetime.now(timezone.utc) + timedelta(seconds=sec)).isoformat()


from whisper_stt_service.repo.dependency_payload import (
    decode_dependency_payload,
    encode_dependency_payload,
)
from whisper_stt_service.repo.models import (
    ClaimedTask,
    EnqueueResult,
    TaskExecutionContext,
)


class JobRepository:
    """基于 SQLite 的 job/task 仓储实现。"""

    def __init__(
        self,
        db: Database,
        *,
        stage_max_retries: dict[str, int] | None = None,
        stage_timeouts: dict[str, int] | None = None,
        stage_effective_defaults: dict[str, dict] | None = None,
        log_root: Path | None = None,
        artifact_root: Path | None = None,
    ) -> None:
        """注入数据库访问对象与可选阶段默认配置。"""

        self.db = db
        self.stage_max_retries = stage_max_retries or {
            "extract": 2,
            "stt": 2,
            "stt_whisperx": 2,
            "translate": 2,
        }
        self.stage_timeouts = stage_timeouts or {
            "extract": 1200,
            "stt": 7200,
            "stt_whisperx": 7200,
            "translate": 7200,
        }
        self.stage_effective_defaults = stage_effective_defaults or {
            "extract": {},
            "stt": {
                "device": "auto",
                "compute_type": "auto",
                "batch_size": 8,
                "beam_size": 5,
                "best_of": 5,
                "patience": 1.0,
                "condition_on_previous_text": False,
                "vad_filter": True,
                "vad_threshold": 0.45,
                "vad_min_speech_duration_ms": 200,
                "vad_max_speech_duration_s": 18.0,
                "vad_min_silence_duration_ms": 700,
                "vad_speech_pad_ms": 300,
                "no_speech_threshold": 0.6,
                "compression_ratio_threshold": 2.2,
                "log_prob_threshold": -1.0,
                "hallucination_silence_threshold": 1.5,
                "initial_prompt": "",
                "hotwords": "",
            },
            "stt_whisperx": {
                "model": "models/faster-whisper-small",
                "device": "auto",
                "compute_type": "auto",
                "batch_size": 8,
                "vad_config_path": "models/whisperx/vad/pyannote/config.yaml",
                "align_model_root": "models/whisperx/align",
                "align_enabled": True,
                "vad_backend": "pyannote",
                "vad_onset": 0.35,
                "vad_offset": 0.2,
                "local_files_only": True,
            },
            "translate": {
                "chunk_minutes": 30,
                "retry": 4,
                "copy_back": "__video_dir__",
            },
        }
        self.log_root = log_root or Path("./tmp/logs")
        self.artifact_root = artifact_root or (self.log_root.parent / "artifacts")

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

    def _build_output_paths(self, job_id: str, video_path: str) -> tuple[str, str]:
        """把阶段产物放到可写 artifact 目录，避免污染源视频目录。"""

        artifact_dir = self.artifact_root / job_id
        stem = Path(video_path).stem
        return (
            str(artifact_dir / f"{stem}.ja.srt"),
            str(artifact_dir / f"{stem}.zh.srt"),
        )

    def list_job_ids_by_status(
        self, *, job_ids: list[str], statuses: tuple[str, ...]
    ) -> set[str]:
        """返回给定 job_id 中状态命中的集合，用于批量清理判定。"""

        if not job_ids or not statuses:
            return set()
        with self.db.connect() as conn:
            id_placeholders = ",".join(["?"] * len(job_ids))
            status_placeholders = ",".join(["?"] * len(statuses))
            rows = conn.execute(
                f"SELECT job_id FROM jobs WHERE job_id IN ({id_placeholders}) AND status IN ({status_placeholders})",
                tuple(job_ids) + tuple(statuses),
            ).fetchall()
            return {str(row["job_id"]) for row in rows}

    def is_job_completed_for_cleanup(
        self, *, job_id: str, statuses: tuple[str, ...]
    ) -> bool:
        """按给定状态判定单个 job 是否可清理。"""

        if not job_id or not statuses:
            return False
        with self.db.connect() as conn:
            placeholders = ",".join(["?"] * len(statuses))
            row = conn.execute(
                f"SELECT 1 AS ok FROM jobs WHERE job_id=? AND status IN ({placeholders}) LIMIT 1",
                (job_id, *statuses),
            ).fetchone()
            return row is not None

    def _build_effective_config(
        self, stage: str, overrides: dict | None = None
    ) -> dict:
        """按 stage 生成 task 的最终生效配置。"""

        base = {
            "timeout_sec": int(self.stage_timeouts.get(stage, 3600)),
            "max_retries": int(self.stage_max_retries.get(stage, 2)),
        }
        base.update(dict(self.stage_effective_defaults.get(stage, {})))
        if overrides:
            base.update(dict(overrides))
        return base

    def _loads_or_empty(self, raw: str | None) -> dict:
        """解析 JSON 文本，异常时返回空对象。"""

        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except Exception:
            return {}
        if isinstance(parsed, dict):
            return parsed
        return {}

    def enqueue(
        self,
        video_path: str,
        language: str,
        dag: dict | None = None,
        job_config: dict | None = None,
    ) -> EnqueueResult:
        """执行同路径幂等/拒绝判定，并按 DAG 创建 job/tasks。"""

        normalized_dag = normalize_and_validate_dag(dag)
        normalized_job_config = normalize_and_validate_job_config(
            job_config, normalized_dag
        )

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
                if statuses and all(s == "queued" for s in statuses):
                    return EnqueueResult(
                        latest_job_id,
                        False,
                        "idempotent_returned",
                        0,
                        "default" if dag is None else "custom",
                        [str(x["stage"]) for x in normalized_dag["stages"]],
                    )
                if any(s in {"claimed", "succeeded", "failed"} for s in statuses):
                    return EnqueueResult(
                        latest_job_id,
                        False,
                        "rejected_started",
                        0,
                        "default" if dag is None else "custom",
                        [str(x["stage"]) for x in normalized_dag["stages"]],
                    )

            queue_ahead = self._count_queue_ahead(conn, now)
            task_name = _derive_task_name(video_path)
            normalized_stages = normalized_dag["stages"]

            for _ in range(_ID_RETRY_LIMIT):
                readable_ts = _readable_timestamp()
                job_id = _build_readable_job_id(task_name, readable_ts)
                ja, zh = self._build_output_paths(job_id, video_path)
                stage_to_task_id = {
                    str(item["stage"]): _build_readable_task_id(
                        task_name,
                        str(item["stage"]),
                        readable_ts,
                    )
                    for item in normalized_stages
                }

                conn.execute("SAVEPOINT enqueue_id_retry")
                try:
                    conn.execute(
                        "INSERT INTO jobs(job_id,video_path,source_language,status,output_ja_path,output_zh_path,dag_json,job_config_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (
                            job_id,
                            video_path,
                            language,
                            "queued",
                            ja,
                            zh,
                            json.dumps(normalized_dag, ensure_ascii=False),
                            json.dumps(normalized_job_config, ensure_ascii=False)
                            if normalized_job_config
                            else None,
                            now,
                            now,
                        ),
                    )

                    for item in normalized_stages:
                        stage = str(item["stage"])
                        task_id = stage_to_task_id[stage]
                        stage_overrides = normalized_job_config.get(stage, {})
                        effective_config = self._build_effective_config(
                            stage, stage_overrides
                        )
                        task_config = {
                            "stage": stage,
                            "effective_config": effective_config,
                        }
                        log_dir, log_file = self._build_log_paths(
                            job_id, stage, task_id
                        )
                        conn.execute(
                            "INSERT INTO tasks(task_id,job_id,stage,status,depends_on_task_id,task_config_json,max_retries,timeout_sec,log_dir,log_file,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                            (
                                task_id,
                                job_id,
                                stage,
                                "queued",
                                None,
                                json.dumps(task_config, ensure_ascii=False),
                                int(effective_config.get("max_retries", 2)),
                                int(effective_config.get("timeout_sec", 3600)),
                                log_dir,
                                log_file,
                                now,
                                now,
                            ),
                        )

                    for item in normalized_stages:
                        stage = str(item["stage"])
                        dep_stage_names = [str(x) for x in item["depends_on"]]
                        dep_task_ids = [
                            stage_to_task_id[name] for name in dep_stage_names
                        ]
                        payload = encode_dependency_payload(dep_task_ids)
                        conn.execute(
                            "UPDATE tasks SET depends_on_task_id=?, updated_at=? WHERE task_id=?",
                            (payload, now, stage_to_task_id[stage]),
                        )
                except sqlite3.IntegrityError as exc:
                    conn.execute("ROLLBACK TO SAVEPOINT enqueue_id_retry")
                    conn.execute("RELEASE SAVEPOINT enqueue_id_retry")
                    if _is_id_collision_error(exc):
                        continue
                    raise
                else:
                    conn.execute("RELEASE SAVEPOINT enqueue_id_retry")
                    return EnqueueResult(
                        job_id,
                        True,
                        "created",
                        queue_ahead,
                        "default" if dag is None else "custom",
                        [str(x["stage"]) for x in normalized_stages],
                    )

            raise RuntimeError("enqueue_id_generation_exhausted")

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
            rows = conn.execute(
                """
                SELECT task_id, job_id, stage, depends_on_task_id
                FROM tasks
                WHERE stage=? AND status='queued'
                ORDER BY created_at ASC, task_id ASC
                """,
                (stage,),
            ).fetchall()

            for row in rows:
                try:
                    dep_ids = decode_dependency_payload(row["depends_on_task_id"])
                except ValueError:
                    conn.execute(
                        "UPDATE tasks SET status='failed', finished_at=?, last_error=?, updated_at=? WHERE task_id=?",
                        (
                            _now(),
                            "invalid_dependency_payload",
                            _now(),
                            row["task_id"],
                        ),
                    )
                    self._refresh_job_status(conn, row["job_id"])
                    continue

                if dep_ids:
                    holders = ",".join(["?"] * len(dep_ids))
                    dep_rows = conn.execute(
                        f"SELECT task_id, status FROM tasks WHERE task_id IN ({holders})",
                        tuple(dep_ids),
                    ).fetchall()
                    dep_status = {r["task_id"]: r["status"] for r in dep_rows}
                    if any(dep_status.get(dep) != "succeeded" for dep in dep_ids):
                        continue

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
                    continue
                self._refresh_job_status(conn, row["job_id"])
                return ClaimedTask(
                    task_id=row["task_id"],
                    job_id=row["job_id"],
                    stage=row["stage"],
                )
            return None

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
                    t.task_config_json,
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
            task_config = self._loads_or_empty(row["task_config_json"])
            effective = task_config.get("effective_config")
            if not isinstance(effective, dict):
                task_config = {
                    "stage": row["stage"],
                    "effective_config": self._build_effective_config(row["stage"]),
                }
                task_config["effective_config"]["timeout_sec"] = int(row["timeout_sec"])
                task_config["effective_config"]["max_retries"] = int(row["max_retries"])
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
                task_config=task_config,
            )

    def _refresh_job_status(self, conn, job_id: str) -> str:
        """按该 job 全部任务聚合刷新状态。"""

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
        """查询 job 元信息与任务明细。"""

        with self.db.connect() as conn:
            job = conn.execute(
                "SELECT * FROM jobs WHERE job_id=?",
                (job_id,),
            ).fetchone()
            if job is None:
                return None
            tasks = conn.execute(
                "SELECT task_id, stage, status, depends_on_task_id, task_config_json, attempt, max_retries, claimed_at, started_at, finished_at, last_error, lease_owner, lease_expires_at, updated_at FROM tasks WHERE job_id=? ORDER BY created_at ASC, task_id ASC",
                (job_id,),
            ).fetchall()
            normalized_tasks: list[dict] = []
            for task in tasks:
                item = dict(task)
                item["task_config"] = self._loads_or_empty(item.get("task_config_json"))
                if not isinstance(item["task_config"].get("effective_config"), dict):
                    item["task_config"] = {
                        "stage": item["stage"],
                        "effective_config": self._build_effective_config(item["stage"]),
                    }
                normalized_tasks.append(item)
            dag_payload = self._loads_or_empty(job["dag_json"])
            if not dag_payload:
                dag_payload = build_default_dag()
            return {
                "job_id": job["job_id"],
                "video_path": job["video_path"],
                "source_language": job["source_language"],
                "status": job["status"],
                "output_ja_path": job["output_ja_path"],
                "output_zh_path": job["output_zh_path"],
                "dag": dag_payload,
                "job_config": self._loads_or_empty(job["job_config_json"]),
                "created_at": job["created_at"],
                "updated_at": job["updated_at"],
                "finished_at": job["finished_at"],
                "tasks": normalized_tasks,
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

    def archive_job(self, job_id: str, reason: str) -> dict:
        """把 job 与其 tasks 迁移到归档表。"""

        archive_reason = reason.strip() if reason.strip() else "manual_archive"
        archived_at = _now()

        with self.db.tx() as conn:
            job = conn.execute(
                "SELECT * FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if job is None:
                raise ValueError("job_not_found")

            statuses = [
                r["status"]
                for r in conn.execute(
                    "SELECT status FROM tasks WHERE job_id=?", (job_id,)
                ).fetchall()
            ]
            all_queued = bool(statuses) and all(s == "queued" for s in statuses)
            is_terminal = job["status"] in {"succeeded", "failed"}
            if not (all_queued or is_terminal):
                raise ValueError("archive_not_allowed")

            conn.execute(
                """
                INSERT INTO jobs_archive(
                    job_id, video_path, source_language, status,
                    output_ja_path, output_zh_path,
                    dag_json, job_config_json,
                    created_at, updated_at, finished_at,
                    archived_at, archive_reason
                )
                SELECT
                    job_id, video_path, source_language, status,
                    output_ja_path, output_zh_path,
                    dag_json, job_config_json,
                    created_at, updated_at, finished_at,
                    ?, ?
                FROM jobs
                WHERE job_id=?
                """,
                (archived_at, archive_reason, job_id),
            )
            conn.execute(
                """
                INSERT INTO tasks_archive(
                    task_id, job_id, stage, status,
                    depends_on_task_id, task_config_json,
                    attempt, max_retries, timeout_sec,
                    lease_owner, lease_expires_at,
                    claimed_at, started_at, finished_at,
                    last_error,
                    log_dir, log_file,
                    created_at, updated_at,
                    archived_at, archive_reason
                )
                SELECT
                    task_id, job_id, stage, status,
                    depends_on_task_id, task_config_json,
                    attempt, max_retries, timeout_sec,
                    lease_owner, lease_expires_at,
                    claimed_at, started_at, finished_at,
                    last_error,
                    log_dir, log_file,
                    created_at, updated_at,
                    ?, ?
                FROM tasks
                WHERE job_id=?
                """,
                (archived_at, archive_reason, job_id),
            )

            conn.execute("DELETE FROM tasks WHERE job_id=?", (job_id,))
            conn.execute("DELETE FROM jobs WHERE job_id=?", (job_id,))

            return {
                "job_id": job_id,
                "archived": True,
                "archived_at": archived_at,
                "reason": archive_reason,
            }

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

    def count_claimed_tasks(self) -> int:
        """返回全局处于 claimed 状态的任务数量。"""

        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(1) AS c FROM tasks WHERE status='claimed'"
            ).fetchone()
            return int(row["c"]) if row is not None else 0
