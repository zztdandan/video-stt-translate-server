"""依赖载荷编码与领取规则测试。"""

from pathlib import Path

from whisper_stt_service.db import Database
from whisper_stt_service.repository import JobRepository, decode_dependency_payload


def test_decode_dependency_payload_compatible_formats() -> None:
    """应兼容 NULL/单值/JSON 数组编码。"""

    assert decode_dependency_payload(None) == []
    assert decode_dependency_payload("") == []
    assert decode_dependency_payload("task-a") == ["task-a"]
    assert decode_dependency_payload('["a", "b"]') == ["a", "b"]


def test_claim_next_requires_all_dependencies_succeeded(tmp_path: Path) -> None:
    """多依赖节点在所有上游成功前不可领取。"""

    db = Database(tmp_path / "q.db")
    db.init_schema()
    repo = JobRepository(db)

    dag = {
        "version": 1,
        "stages": [
            {"stage": "extract", "depends_on": []},
            {"stage": "stt", "depends_on": []},
            {"stage": "translate", "depends_on": ["extract", "stt"]},
        ],
    }
    created = repo.enqueue(video_path="/tmp/dep.mp4", language="ja", dag=dag)
    detail = repo.get_job_detail(created.job_id)
    assert detail is not None
    tasks = {t["stage"]: t for t in detail["tasks"]}

    repo.mark_task_succeeded(tasks["extract"]["task_id"])
    claimed_before = repo.claim_next("translate", worker_id="w1", lease_timeout_sec=60)
    assert claimed_before is None

    repo.mark_task_succeeded(tasks["stt"]["task_id"])
    claimed_after = repo.claim_next("translate", worker_id="w1", lease_timeout_sec=60)
    assert claimed_after is not None
