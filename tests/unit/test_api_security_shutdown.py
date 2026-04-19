"""全局 API 鉴权与停机接口测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from whisper_stt_service.api import create_app


class _FakeRuntime:
    """最小 runtime 替身，用于验证 admin 路由行为。"""

    def __init__(self) -> None:
        self._drain = False

    def request_shutdown(self, reason: str = "manual_shutdown") -> dict:
        self._drain = True
        return {
            "drain_requested": True,
            "shutdown_reason": reason,
            "claimed_count": 0,
            "inflight_count": 0,
            "can_exit": True,
        }

    def shutdown_status(self) -> dict:
        return {
            "drain_requested": self._drain,
            "claimed_count": 0,
            "inflight_count": 0,
            "can_exit": self._drain,
        }


def test_api_token_guard_rejects_missing_token() -> None:
    """配置 API token 后，缺失请求头应返回 401。"""

    app = create_app(api_token="token-123")
    client = TestClient(app)

    resp = client.get("/jobs/unknown/progress")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid_api_token"


def test_api_token_guard_allows_valid_token() -> None:
    """配置 API token 后，携带正确请求头应放行。"""

    app = create_app(api_token="token-123")
    client = TestClient(app)

    resp = client.get("/jobs/unknown/progress", headers={"X-API-Token": "token-123"})
    assert resp.status_code == 404


def test_admin_shutdown_route_requires_token_and_invokes_runtime() -> None:
    """停机接口应受全局鉴权保护，且可触发 runtime drain。"""

    app = create_app(api_token="token-123", runtime=_FakeRuntime())
    client = TestClient(app)

    denied = client.post("/admin/shutdown", json={"reason": "e2e_round1"})
    assert denied.status_code == 401

    ok = client.post(
        "/admin/shutdown",
        json={"reason": "e2e_round1"},
        headers={"X-API-Token": "token-123"},
    )
    assert ok.status_code == 200
    assert ok.json()["drain_requested"] is True
    assert ok.json()["shutdown_reason"] == "e2e_round1"
