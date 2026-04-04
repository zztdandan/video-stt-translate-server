"""Job 与 task 查询相关路由。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request

from whisper_stt_service.api.dependencies import get_progress_store, get_repo
from whisper_stt_service.api.schemas import ArchiveJobReq, CreateJobReq


router = APIRouter()


@router.post("/jobs")
def create_job(req: CreateJobReq, request: Request):
    """创建 job：先校验视频路径，再写入 SQLite 队列。"""

    path = Path(req.video_path)
    if not path.is_absolute() or not path.is_file():
        raise HTTPException(status_code=400, detail="video_path_not_found")
    repo = get_repo(request)
    try:
        result = repo.enqueue(
            video_path=req.video_path,
            language=req.language,
            dag=req.dag,
            job_config=req.job_config,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "job_id": result.job_id,
        "accepted": result.accepted,
        "queue_ahead": result.queue_ahead,
        "message": result.message,
        "plan_mode": result.plan_mode,
        "stages": result.stages,
    }


@router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request):
    """查询单个 job 及其三阶段任务明细。"""

    repo = get_repo(request)
    item = repo.get_job_detail(job_id)
    if item is None:
        raise HTTPException(status_code=404, detail="job_not_found")
    return item


@router.get("/jobs/{job_id}/progress")
def get_progress(job_id: str, request: Request):
    """查询 job 状态并附带可用的内存进度快照。"""

    repo = get_repo(request)
    item = repo.get_job_detail(job_id)
    if item is None:
        raise HTTPException(status_code=404, detail="job_not_found")

    store = get_progress_store(request)
    if store is not None:
        for task in item["tasks"]:
            snap = store.snapshot(task["task_id"])
            if task["task_id"] in snap:
                task["progress"] = snap[task["task_id"]]
    return item


@router.post("/jobs/{job_id}/archive")
def archive_job(job_id: str, req: ArchiveJobReq, request: Request):
    """显式归档指定 job，释放路径占用资格。"""

    repo = get_repo(request)
    try:
        return repo.archive_job(job_id=job_id, reason=req.reason)
    except ValueError as exc:
        detail = str(exc)
        if detail == "job_not_found":
            raise HTTPException(status_code=404, detail=detail) from exc
        raise HTTPException(status_code=409, detail=detail) from exc


@router.get("/jobs/by-path")
def by_path(video_path: str, request: Request):
    """按视频路径查询最近一条 job。"""

    repo = get_repo(request)
    item = repo.get_job_latest_by_path(video_path)
    return {"video_path": video_path, "item": item}


@router.get("/jobs")
def list_jobs(
    request: Request,
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    video_path_like: str | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
    language: str | None = None,
    has_failed_tasks: bool | None = None,
    sort_by: str = Query("created_at", pattern="^(created_at|updated_at)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
):
    """分页查询 jobs，支持筛选条件。"""

    repo = get_repo(request)
    safe_page = max(page, 1)
    safe_size = max(1, min(page_size, 200))
    return repo.list_jobs(
        page=safe_page,
        page_size=safe_size,
        status=status,
        video_path_like=video_path_like,
        created_from=created_from,
        created_to=created_to,
        language=language,
        has_failed_tasks=has_failed_tasks,
        sort_by=sort_by,
        order=order,
    )


@router.get("/tasks")
def list_tasks(
    request: Request,
    page: int = 1,
    page_size: int = 20,
    stage: str | None = None,
    status: str | None = None,
    job_id: str | None = None,
    lease_owner: str | None = None,
    updated_from: str | None = None,
    updated_to: str | None = None,
):
    """分页查询 tasks，支持运维筛选。"""

    repo = get_repo(request)
    safe_page = max(page, 1)
    safe_size = max(1, min(page_size, 200))
    return repo.list_tasks(
        page=safe_page,
        page_size=safe_size,
        stage=stage,
        status=status,
        job_id=job_id,
        lease_owner=lease_owner,
        updated_from=updated_from,
        updated_to=updated_to,
    )
