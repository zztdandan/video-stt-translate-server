"""入队规则单元测试：幂等返回与拒绝策略。"""

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
