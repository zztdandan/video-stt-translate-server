"""队列与 worker 概览路由。"""

from __future__ import annotations

from fastapi import APIRouter, Request

from whisper_stt_service.api.dependencies import get_repo, get_runtime


router = APIRouter()


@router.get("/queue/summary")
def queue_summary(request: Request):
    """返回队列摘要、worker 活动视图与配置快照。"""

    repo = get_repo(request)
    summary = repo.queue_summary()
    runtime = get_runtime(request)
    workers = getattr(request.app.state, "worker_view", {})
    if runtime is not None:
        workers = runtime.active_workers()
    summary["workers"] = workers
    summary["throughput"] = {}
    return summary
