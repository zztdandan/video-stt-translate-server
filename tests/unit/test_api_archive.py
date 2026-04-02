"""归档 API 行为测试。"""

from pathlib import Path

from fastapi.testclient import TestClient

from whisper_stt_service.api import create_app
from whisper_stt_service.db import Database
from whisper_stt_service.repository import JobRepository


def test_post_archive_allows_queued_job(tmp_path: Path) -> None:
    """全 queued 的 job 应可归档。"""

    db = Database(tmp_path / "q.db")
    db.init_schema()
    repo = JobRepository(db)
    app = create_app(repo=repo)
    client = TestClient(app)

    created = repo.enqueue(video_path="/tmp/api-archive.mp4", language="ja")
    resp = client.post(
        f"/jobs/{created.job_id}/archive", json={"reason": "manual_archive"}
    )

    assert resp.status_code == 200
    assert resp.json()["archived"] is True
