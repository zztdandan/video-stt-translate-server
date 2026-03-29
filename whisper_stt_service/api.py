"""HTTP API 路由定义（当前为最小骨架实现）。"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


class CreateJobReq(BaseModel):
    """创建任务请求体。"""

    video_path: str
    language: str


def create_app() -> FastAPI:
    """创建并返回 FastAPI 应用实例。"""

    app = FastAPI(title="whisper-stt-service")

    @app.post("/jobs")
    def create_job(req: CreateJobReq):
        """创建 job：先校验视频路径，再返回占位入队结果。"""

        path = Path(req.video_path)
        if not path.is_absolute() or not path.is_file():
            raise HTTPException(status_code=400, detail="video_path_not_found")
        return {
            "job_id": "example-job-id",
            "accepted": True,
            "queue_ahead": 0,
            "message": "created",
        }

    @app.get("/jobs/{job_id}")
    def get_job(job_id: str):
        """查询单个 job（当前骨架固定返回 404）。"""

        raise HTTPException(status_code=404, detail="job_not_found")

    @app.get("/jobs/{job_id}/progress")
    def get_progress(job_id: str):
        """查询 job 进度（当前骨架固定返回 404）。"""

        raise HTTPException(status_code=404, detail="job_not_found")

    @app.get("/jobs/by-path")
    def by_path(video_path: str):
        """按视频路径查询最近 job（当前返回空占位）。"""

        return {"video_path": video_path, "item": None}

    @app.get("/jobs")
    def list_jobs(page: int = 1, page_size: int = 20):
        """分页查询 job 列表（当前返回空集合）。"""

        return {"items": [], "total": 0, "page": page, "page_size": page_size}

    @app.get("/tasks")
    def list_tasks(page: int = 1, page_size: int = 20):
        """分页查询 task 列表（当前返回空集合）。"""

        return {"items": [], "total": 0, "page": page, "page_size": page_size}

    @app.get("/queue/summary")
    def queue_summary():
        """返回队列摘要（当前返回空占位结构）。"""

        return {"stages": {}, "workers": {}, "throughput": {}}

    return app
