from pathlib import Path

from whisper_stt_service.db import Database
from whisper_stt_service.repository import JobRepository
from whisper_stt_service.workers import recover_claimed_to_queued


def test_recover_claimed_to_queued_on_startup(tmp_path: Path) -> None:
    db = Database(tmp_path / "q.db")
    db.init_schema()
    repo = JobRepository(db)
    _ = repo.enqueue("/tmp/d.mp4", "ja")
    claimed = repo.claim_next("extract", "w1", 60)
    assert claimed is not None

    recovered = recover_claimed_to_queued(db)
    assert recovered >= 1

    with db.connect() as conn:
        row = conn.execute(
            "SELECT status FROM tasks WHERE task_id=?", (claimed.task_id,)
        ).fetchone()
        assert row["status"] == "queued"
