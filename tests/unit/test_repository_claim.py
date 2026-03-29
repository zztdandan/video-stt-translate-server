from pathlib import Path

from whisper_stt_service.db import Database
from whisper_stt_service.repository import JobRepository


def test_claim_is_atomic_for_same_stage(tmp_path: Path) -> None:
    db = Database(tmp_path / "q.db")
    db.init_schema()
    repo = JobRepository(db)
    repo.enqueue("/tmp/c.mp4", "ja")

    first = repo.claim_next(stage="extract", worker_id="w1", lease_timeout_sec=60)
    second = repo.claim_next(stage="extract", worker_id="w2", lease_timeout_sec=60)

    assert first is not None
    assert second is None
