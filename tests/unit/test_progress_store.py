"""进度内存存储 TTL 行为测试。"""

from datetime import datetime, timedelta, timezone

from whisper_stt_service.progress import ProgressStore


def test_completed_progress_expires() -> None:
    """已完成任务进度在超过 TTL 后应被清理。"""

    store = ProgressStore(ttl_seconds=10)
    now = datetime.now(timezone.utc)
    # 先写入运行中进度，再标记完成。
    store.update("t1", percent=50.0, message="running", worker_id="w1", ts=now)
    store.mark_done("t1", ts=now)

    # 快照存在，说明写入与完成标记均生效。
    assert "t1" in store.snapshot("t1")

    # 推进到 TTL 之后执行清理，条目应被移除。
    future = now + timedelta(seconds=11)
    store.cleanup(now=future)

    assert store.snapshot("t1") == {}
