"""API 层共享依赖读取。"""

from __future__ import annotations

from fastapi import HTTPException, Request

from whisper_stt_service.core.progress import ProgressStore
from whisper_stt_service.repo.job_repository import JobRepository
from whisper_stt_service.service.runtime import WorkerRuntime


def get_repo(request: Request) -> JobRepository:
    """读取 app.state 上的仓储实例。"""

    repo = getattr(request.app.state, "repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="service_not_ready")
    return repo


def get_progress_store(request: Request) -> ProgressStore | None:
    """读取 app.state 上的进度存储。"""

    return getattr(request.app.state, "progress_store", None)


def get_runtime(request: Request) -> WorkerRuntime | None:
    """读取 app.state 上的 worker 运行时。"""

    return getattr(request.app.state, "runtime", None)
