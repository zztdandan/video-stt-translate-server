from pathlib import Path

from whisper_stt_service.db import Database
from whisper_stt_service.repository import JobRepository


def test_enqueue_idempotent_when_all_three_tasks_queued(tmp_path: Path) -> None:
    db = Database(tmp_path / "q.db")
    db.init_schema()
    repo = JobRepository(db)

    first = repo.enqueue(video_path="/tmp/a.mp4", language="ja")
    second = repo.enqueue(video_path="/tmp/a.mp4", language="ja")

    assert first.job_id == second.job_id
    assert second.message == "idempotent_returned"


def test_enqueue_rejected_when_started(tmp_path: Path) -> None:
    db = Database(tmp_path / "q.db")
    db.init_schema()
    repo = JobRepository(db)
    created = repo.enqueue(video_path="/tmp/b.mp4", language="ja")
    repo.force_mark_any_stage_started(created.job_id)

    rejected = repo.enqueue(video_path="/tmp/b.mp4", language="ja")
    assert rejected.accepted is False
    assert rejected.message == "rejected_started"
