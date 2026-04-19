"""FastAPI 应用构建。"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from whisper_stt_service.api.routes.jobs import router as jobs_router
from whisper_stt_service.api.routes.admin import router as admin_router
from whisper_stt_service.api.routes.queue import router as queue_router
from whisper_stt_service.core.progress import ProgressStore
from whisper_stt_service.repo.database import Database
from whisper_stt_service.repo.job_repository import JobRepository
from whisper_stt_service.service.runtime import WorkerRuntime


def create_app(
    *,
    repo: JobRepository | None = None,
    progress_store: ProgressStore | None = None,
    runtime: WorkerRuntime | None = None,
    worker_view: dict | None = None,
    api_token: str | None = None,
) -> FastAPI:
    """创建并返回 FastAPI 应用实例。"""

    if repo is None:
        # 兼容单元测试直接 create_app() 的调用方式。
        db_file = Path("./tmp/api-test.db")
        db_file.parent.mkdir(parents=True, exist_ok=True)
        db = Database(db_file)
        db.init_schema()
        repo = JobRepository(db)
    if progress_store is None:
        progress_store = ProgressStore(ttl_seconds=3600)

    app = FastAPI(title="whisper-stt-service")
    app.state.repo = repo
    app.state.progress_store = progress_store
    app.state.runtime = runtime
    app.state.worker_view = worker_view or {}
    app.state.api_token = (api_token or "").strip()

    @app.middleware("http")
    async def _api_token_guard(request, call_next):
        """按配置启用全局 API Token 鉴权。"""

        configured_token = str(getattr(request.app.state, "api_token", "")).strip()
        if not configured_token:
            return await call_next(request)

        exempt_paths = {
            "/docs",
            "/openapi.json",
            "/redoc",
            "/docs/oauth2-redirect",
        }
        if request.url.path in exempt_paths:
            return await call_next(request)

        provided = request.headers.get("X-API-Token", "").strip()
        if provided != configured_token:
            return JSONResponse(
                status_code=401, content={"detail": "invalid_api_token"}
            )
        return await call_next(request)

    app.include_router(admin_router)
    app.include_router(jobs_router)
    app.include_router(queue_router)
    return app
