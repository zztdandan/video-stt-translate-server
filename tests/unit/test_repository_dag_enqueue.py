"""DAG 化 enqueue 行为测试。"""

import re
import uuid
from pathlib import Path

from whisper_stt_service.db import Database
from whisper_stt_service.repo import job_repository
from whisper_stt_service.repo.dependency_payload import decode_dependency_payload
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
    job_config = {"stt": {"batch_size": 8, "beam_size": 3, "best_of": 3}}

    created = repo.enqueue(
        video_path="/tmp/b.mp4", language="ja", dag=dag, job_config=job_config
    )
    detail = repo.get_job_detail(created.job_id)
    assert detail is not None
    assert [x["stage"] for x in detail["dag"]["stages"]] == ["extract", "stt"]
    assert detail["job_config"]["stt"]["batch_size"] == 8
    assert detail["job_config"]["stt"]["beam_size"] == 3
    stt_task = [t for t in detail["tasks"] if t["stage"] == "stt"][0]
    assert stt_task["task_config"]["effective_config"]["batch_size"] == 8
    assert stt_task["task_config"]["effective_config"]["beam_size"] == 3


def test_enqueue_generates_readable_task_id_and_keeps_dependency_graph(
    tmp_path: Path,
) -> None:
    """task_id 应包含 task_name+stage，且依赖关系在可读 ID 下保持正确。"""

    db = Database(tmp_path / "q.db")
    db.init_schema()
    repo = JobRepository(db)
    dag = {
        "version": 1,
        "stages": [
            {"stage": "extract", "depends_on": []},
            {"stage": "stt", "depends_on": ["extract"]},
            {"stage": "translate", "depends_on": ["stt"]},
        ],
    }
    noisy = "X-" * 30 + "ReadableTailForTaskId9876543210" * 3
    cleaned = "".join(ch for ch in noisy if ch.isalnum())
    expected_task_name = cleaned[-64:]

    created = repo.enqueue(video_path=f"/tmp/{noisy}.mp4", language="ja", dag=dag)
    detail = repo.get_job_detail(created.job_id)
    assert detail is not None

    stage_to_task = {task["stage"]: task for task in detail["tasks"]}
    for stage in ["extract", "stt", "translate"]:
        task_id = stage_to_task[stage]["task_id"]
        assert re.fullmatch(
            rf"task-{expected_task_name}-{stage}-\d{{14}}-[0-9a-f]{{4}}",
            task_id,
        )

    assert (
        decode_dependency_payload(stage_to_task["extract"]["depends_on_task_id"]) == []
    )
    assert decode_dependency_payload(stage_to_task["stt"]["depends_on_task_id"]) == [
        stage_to_task["extract"]["task_id"]
    ]
    assert decode_dependency_payload(
        stage_to_task["translate"]["depends_on_task_id"]
    ) == [stage_to_task["stt"]["task_id"]]


def test_enqueue_retries_when_readable_id_collides(tmp_path: Path, monkeypatch) -> None:
    """短后缀冲突时应重试并成功创建新任务。"""

    db = Database(tmp_path / "q.db")
    db.init_schema()
    repo = JobRepository(db)
    fixed_ts = "20260405121212"

    monkeypatch.setattr(job_repository, "_readable_timestamp", lambda: fixed_ts)

    suffixes = iter(
        [
            "a" * 32,
            "b" * 32,
            "c" * 32,
            "d" * 32,
            "e" * 32,
            "f" * 32,
            "1" * 32,
            "2" * 32,
        ]
    )

    def _fake_uuid4() -> uuid.UUID:
        return uuid.UUID(next(suffixes))

    monkeypatch.setattr(job_repository.uuid, "uuid4", _fake_uuid4)

    conflicting_job_id = f"job-collision-job-{fixed_ts}-aaaa"
    now = "2026-04-05T12:00:00+00:00"
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO jobs(job_id,video_path,source_language,status,output_ja_path,output_zh_path,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (
                conflicting_job_id,
                "/tmp/other-video.mp4",
                "ja",
                "queued",
                "/tmp/other.ja.srt",
                "/tmp/other.zh.srt",
                now,
                now,
            ),
        )

    created = repo.enqueue(video_path="/tmp/collision.mp4", language="ja")
    assert created.accepted is True
    assert created.job_id != conflicting_job_id
    assert created.job_id.startswith(f"job-collision-job-{fixed_ts}-")
