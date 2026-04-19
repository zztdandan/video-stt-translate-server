"""服务管理路由（优雅停机）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from whisper_stt_service.api.dependencies import get_runtime
from whisper_stt_service.api.schemas import ShutdownReq


router = APIRouter()


@router.post("/admin/shutdown")
def shutdown_service(req: ShutdownReq, request: Request):
    """触发 drain 停机：停止领取新任务并等待在途任务结束。"""

    runtime = get_runtime(request)
    if runtime is None:
        raise HTTPException(status_code=503, detail="service_not_ready")
    return runtime.request_shutdown(reason=req.reason)


@router.get("/admin/shutdown/status")
def shutdown_status(request: Request):
    """查询 drain 停机状态与退出前置条件。"""

    runtime = get_runtime(request)
    if runtime is None:
        raise HTTPException(status_code=503, detail="service_not_ready")
    return runtime.shutdown_status()
