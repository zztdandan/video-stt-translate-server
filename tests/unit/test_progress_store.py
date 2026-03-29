from datetime import datetime, timedelta, timezone

from whisper_stt_service.progress import ProgressStore


def test_completed_progress_expires() -> None:
    store = ProgressStore(ttl_seconds=10)
    now = datetime.now(timezone.utc)
    store.update("t1", percent=50.0, message="running", worker_id="w1", ts=now)
    store.mark_done("t1", ts=now)

    assert "t1" in store.snapshot("t1")

    future = now + timedelta(seconds=11)
    store.cleanup(now=future)

    assert store.snapshot("t1") == {}
