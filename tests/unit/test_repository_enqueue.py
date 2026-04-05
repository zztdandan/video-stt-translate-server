"""入队规则单元测试：幂等返回与拒绝策略。"""

import re
from pathlib import Path

from whisper_stt_service.db import Database
from whisper_stt_service.repository import JobRepository


def test_enqueue_idempotent_when_all_three_tasks_queued(tmp_path: Path) -> None:
    """同路径且三阶段均 queued 时，应返回已有 job_id（幂等）。"""

    # 初始化独立测试数据库与仓储对象。
    db = Database(tmp_path / "q.db")
    db.init_schema()
    repo = JobRepository(db)

    first = repo.enqueue(video_path="/tmp/a.mp4", language="ja")
    second = repo.enqueue(video_path="/tmp/a.mp4", language="ja")

    # 第二次请求不新建任务，直接返回第一次的 job 标识。
    assert first.job_id == second.job_id
    assert second.message == "idempotent_returned"


def test_enqueue_rejected_when_started(tmp_path: Path) -> None:
    """同路径任务任一阶段已开始后，应拒绝再次入队。"""

    db = Database(tmp_path / "q.db")
    db.init_schema()
    repo = JobRepository(db)
    created = repo.enqueue(video_path="/tmp/b.mp4", language="ja")
    # 人工把阶段置为 claimed，模拟“任务已经开始执行”的场景。
    repo.force_mark_any_stage_started(created.job_id)

    # 预期：返回 rejected_started 且 accepted=False。
    rejected = repo.enqueue(video_path="/tmp/b.mp4", language="ja")
    assert rejected.accepted is False
    assert rejected.message == "rejected_started"


def test_enqueue_generates_readable_job_id(tmp_path: Path) -> None:
    """job_id 应包含可读 task_name + 时间戳 + 短后缀。"""

    db = Database(tmp_path / "q.db")
    db.init_schema()
    repo = JobRepository(db)

    created = repo.enqueue(
        video_path="/tmp/[Final] 2026-04-05 demo_movie_v12 (1080p).mp4",
        language="ja",
    )
    assert re.fullmatch(
        r"job-Final20260405demomoviev121080p-job-\d{14}-[0-9a-f]{4}",
        created.job_id,
    )


def test_enqueue_job_id_keeps_last_64_alnum_chars(tmp_path: Path) -> None:
    """超长文件名清洗后只保留右侧 64 个字母数字字符。"""

    db = Database(tmp_path / "q.db")
    db.init_schema()
    repo = JobRepository(db)

    source_stem = "L!" * 20 + "KeepThisReadableSuffix0123456789" * 3
    video_path = f"/tmp/{source_stem}.mp4"
    cleaned = "".join(ch for ch in source_stem if ch.isalnum())
    expected_task_name = cleaned[-64:]

    created = repo.enqueue(video_path=video_path, language="ja")
    assert f"job-{expected_task_name}-job-" in created.job_id


def test_schema_accepts_ids_longer_than_100_chars(tmp_path: Path) -> None:
    """底层表结构应继续支持超长 job_id/task_id。"""

    db = Database(tmp_path / "q.db")
    db.init_schema()
    repo = JobRepository(db)
    long_job_id = "job-" + "a" * 120
    long_task_id = "task-" + "b" * 120
    now = "2026-04-05T12:00:00+00:00"

    with db.tx() as conn:
        conn.execute(
            "INSERT INTO jobs(job_id,video_path,source_language,status,output_ja_path,output_zh_path,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (
                long_job_id,
                "/tmp/long-id.mp4",
                "ja",
                "queued",
                "/tmp/a.ja.srt",
                "/tmp/a.zh.srt",
                now,
                now,
            ),
        )
        conn.execute(
            "INSERT INTO tasks(task_id,job_id,stage,status,depends_on_task_id,max_retries,timeout_sec,log_dir,log_file,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                long_task_id,
                long_job_id,
                "extract",
                "queued",
                None,
                2,
                300,
                "/tmp/log",
                "/tmp/log/task.log",
                now,
                now,
            ),
        )

    detail = repo.get_job_detail(long_job_id)
    assert detail is not None
    assert detail["job_id"] == long_job_id
    assert detail["tasks"][0]["task_id"] == long_task_id
