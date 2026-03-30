"""HTTP API 路由定义：真实入队、查询、筛选分页与队列摘要。"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from whisper_stt_service.db import Database
from whisper_stt_service.progress import ProgressStore
from whisper_stt_service.repository import JobRepository
from whisper_stt_service.workers import WorkerRuntime


class CreateJobReq(BaseModel):
    """创建任务请求体。"""

    video_path: str
    language: str


def _get_repo(app: FastAPI) -> JobRepository:
    """读取 app.state 上的仓储实例。"""

    repo = getattr(app.state, "repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="service_not_ready")
    return repo


def _get_progress_store(app: FastAPI) -> ProgressStore | None:
    """读取 app.state 上的进度存储。"""

    return getattr(app.state, "progress_store", None)


def _get_runtime(app: FastAPI) -> WorkerRuntime | None:
    """读取 app.state 上的 worker 运行时。"""

    return getattr(app.state, "runtime", None)


def create_app(
    *,
    repo: JobRepository | None = None,
    progress_store: ProgressStore | None = None,
    runtime: WorkerRuntime | None = None,
    worker_view: dict | None = None,
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

    @app.post("/jobs")
    def create_job(req: CreateJobReq):
        """创建 job：先校验视频路径，再写入 SQLite 队列。"""

        path = Path(req.video_path)
        if not path.is_absolute() or not path.is_file():
            raise HTTPException(status_code=400, detail="video_path_not_found")
        repo_impl = _get_repo(app)
        result = repo_impl.enqueue(video_path=req.video_path, language=req.language)
        return {
            "job_id": result.job_id,
            "accepted": result.accepted,
            "queue_ahead": result.queue_ahead,
            "message": result.message,
        }

    @app.get("/jobs/{job_id}")
    def get_job(job_id: str):
        """查询单个 job 及其三阶段任务明细。"""

        repo_impl = _get_repo(app)
        item = repo_impl.get_job_detail(job_id)
        if item is None:
            raise HTTPException(status_code=404, detail="job_not_found")
        return item

    @app.get("/jobs/{job_id}/progress")
    def get_progress(job_id: str):
        """查询 job 状态并附带可用的内存进度快照。"""

        repo_impl = _get_repo(app)
        item = repo_impl.get_job_detail(job_id)
        if item is None:
            raise HTTPException(status_code=404, detail="job_not_found")

        store = _get_progress_store(app)
        if store is not None:
            for task in item["tasks"]:
                snap = store.snapshot(task["task_id"])
                if task["task_id"] in snap:
                    task["progress"] = snap[task["task_id"]]
        return item

    @app.get("/jobs/by-path")
    def by_path(video_path: str):
        """按视频路径查询最近一条 job。"""

        repo_impl = _get_repo(app)
        item = repo_impl.get_job_latest_by_path(video_path)
        return {"video_path": video_path, "item": item}

    @app.get("/jobs")
    def list_jobs(
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

        repo_impl = _get_repo(app)
        safe_page = max(page, 1)
        safe_size = max(1, min(page_size, 200))
        return repo_impl.list_jobs(
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

    @app.get("/tasks")
    def list_tasks(
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

        repo_impl = _get_repo(app)
        safe_page = max(page, 1)
        safe_size = max(1, min(page_size, 200))
        return repo_impl.list_tasks(
            page=safe_page,
            page_size=safe_size,
            stage=stage,
            status=status,
            job_id=job_id,
            lease_owner=lease_owner,
            updated_from=updated_from,
            updated_to=updated_to,
        )

    @app.get("/queue/summary")
    def queue_summary():
        """返回队列摘要、worker 活动视图与配置快照。"""

        repo_impl = _get_repo(app)
        summary = repo_impl.queue_summary()
        runtime_impl = _get_runtime(app)
        workers = app.state.worker_view
        if runtime_impl is not None:
            workers = runtime_impl.active_workers()
        summary["workers"] = workers
        summary["throughput"] = {}
        return summary

    return app
