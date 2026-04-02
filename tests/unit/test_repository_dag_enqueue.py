"""DAG 化 enqueue 行为测试。"""

from pathlib import Path

from whisper_stt_service.db import Database
from whisper_stt_service.repository import JobRepository


def test_enqueue_persists_default_dag_and_task_config_snapshot(tmp_path: Path) -> None:
    """未传 dag/job_config 时应写入默认 DAG 和 task_config 快照。"""

    db = Database(tmp_path / "q.db")
    db.init_schema()
    repo = JobRepository(db)

    created = repo.enqueue(video_path="/tmp/a.mp4", language="ja")
    detail = repo.get_job_detail(created.job_id)
    assert detail is not None
    assert detail["dag"]["stages"][0]["stage"] == "extract"
    assert detail["job_config"] == {}
    extract_task = [t for t in detail["tasks"] if t["stage"] == "extract"][0]
    assert extract_task["task_config"]["stage"] == "extract"
    assert isinstance(extract_task["task_config"]["effective_config"], dict)


def test_enqueue_persists_explicit_dag_and_job_config(tmp_path: Path) -> None:
    """显式 dag/job_config 应落库并反映到 task_config。"""

    db = Database(tmp_path / "q.db")
    db.init_schema()
    repo = JobRepository(db)
    dag = {
        "version": 1,
        "stages": [
            {"stage": "extract", "depends_on": []},
            {"stage": "stt", "depends_on": ["extract"]},
        ],
    }
    job_config = {"stt": {"beam_size": 3, "best_of": 3}}

    created = repo.enqueue(
        video_path="/tmp/b.mp4", language="ja", dag=dag, job_config=job_config
    )
    detail = repo.get_job_detail(created.job_id)
    assert detail is not None
    assert [x["stage"] for x in detail["dag"]["stages"]] == ["extract", "stt"]
    assert detail["job_config"]["stt"]["beam_size"] == 3
    stt_task = [t for t in detail["tasks"] if t["stage"] == "stt"][0]
    assert stt_task["task_config"]["effective_config"]["beam_size"] == 3
