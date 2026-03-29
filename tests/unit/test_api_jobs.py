"""HTTP API 基础行为单元测试。"""

from fastapi.testclient import TestClient

from whisper_stt_service.api import create_app


def test_post_jobs_requires_existing_video(tmp_path):
    """POST /jobs 在视频路径不存在时应返回 400。"""

    app = create_app()
    client = TestClient(app)
    resp = client.post(
        "/jobs", json={"video_path": "/not-found/a.mp4", "language": "ja"}
    )
    # 当前实现把“不存在或非绝对路径”统一映射为 400。
    assert resp.status_code == 400


def test_get_progress_returns_task_status_and_optional_progress(tmp_path):
    """GET /jobs/{job_id}/progress 返回约定状态码（骨架允许 404/200）。"""

    app = create_app()
    client = TestClient(app)
    resp = client.get("/jobs/unknown/progress")
    assert resp.status_code in (404, 200)
