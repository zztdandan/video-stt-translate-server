"""Job 归档仓储行为测试。"""

from pathlib import Path

from whisper_stt_service.db import Database
from whisper_stt_service.repository import JobRepository


def test_archive_moves_job_and_tasks_and_releases_video_path(tmp_path: Path) -> None:
    """归档后主表删除，且同路径允许再次提交。"""

    db = Database(tmp_path / "q.db")
    db.init_schema()
    repo = JobRepository(db)

    created = repo.enqueue(video_path="/tmp/archive.mp4", language="ja")
    repo.archive_job(created.job_id, "manual_archive")

    with db.connect() as conn:
        jobs_main = conn.execute("SELECT COUNT(1) AS c FROM jobs").fetchone()["c"]
        tasks_main = conn.execute("SELECT COUNT(1) AS c FROM tasks").fetchone()["c"]
        jobs_arc = conn.execute("SELECT COUNT(1) AS c FROM jobs_archive").fetchone()[
            "c"
        ]
        tasks_arc = conn.execute("SELECT COUNT(1) AS c FROM tasks_archive").fetchone()[
            "c"
        ]

    assert int(jobs_main) == 0
    assert int(tasks_main) == 0
    assert int(jobs_arc) == 1
    assert int(tasks_arc) == 3

    recreated = repo.enqueue(video_path="/tmp/archive.mp4", language="ja")
    assert recreated.accepted is True


def test_archive_rejects_mixed_running_state(tmp_path: Path) -> None:
    """部分已开始且未终态的 job 不允许归档。"""

    db = Database(tmp_path / "q.db")
    db.init_schema()
    repo = JobRepository(db)
    created = repo.enqueue(video_path="/tmp/archive2.mp4", language="ja")
    repo.force_mark_any_stage_started(created.job_id)

    try:
        repo.archive_job(created.job_id, "manual_archive")
        assert False, "should reject"
    except ValueError as exc:
        assert str(exc) == "archive_not_allowed"
