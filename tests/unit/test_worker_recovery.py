"""启动恢复逻辑单元测试。"""

from pathlib import Path

from whisper_stt_service.db import Database
from whisper_stt_service.repository import JobRepository
from whisper_stt_service.workers import recover_claimed_to_queued


def test_recover_claimed_to_queued_on_startup(tmp_path: Path) -> None:
    """服务启动恢复应把 claimed 任务回退为 queued。"""

    db = Database(tmp_path / "q.db")
    db.init_schema()
    repo = JobRepository(db)
    _ = repo.enqueue("/tmp/d.mp4", "ja")
    # 先领取一个任务，制造需要恢复的 claimed 状态。
    claimed = repo.claim_next("extract", "w1", 60)
    assert claimed is not None

    # 执行恢复后，至少应有一条记录被回退。
    recovered = recover_claimed_to_queued(db)
    assert recovered >= 1

    # 直接查库确认被领取的任务状态已回到 queued。
    with db.connect() as conn:
        row = conn.execute(
            "SELECT status FROM tasks WHERE task_id=?", (claimed.task_id,)
        ).fetchone()
        assert row["status"] == "queued"
