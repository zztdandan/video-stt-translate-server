from fastapi.testclient import TestClient

from whisper_stt_service.api import create_app


def test_post_jobs_requires_existing_video(tmp_path):
    app = create_app()
    client = TestClient(app)
    resp = client.post(
        "/jobs", json={"video_path": "/not-found/a.mp4", "language": "ja"}
    )
    assert resp.status_code == 400


def test_get_progress_returns_task_status_and_optional_progress(tmp_path):
    app = create_app()
    client = TestClient(app)
    resp = client.get("/jobs/unknown/progress")
    assert resp.status_code in (404, 200)
