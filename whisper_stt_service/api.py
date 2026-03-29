from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


class CreateJobReq(BaseModel):
    video_path: str
    language: str


def create_app() -> FastAPI:
    app = FastAPI(title="whisper-stt-service")

    @app.post("/jobs")
    def create_job(req: CreateJobReq):
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
        raise HTTPException(status_code=404, detail="job_not_found")

    @app.get("/jobs/{job_id}/progress")
    def get_progress(job_id: str):
        raise HTTPException(status_code=404, detail="job_not_found")

    @app.get("/jobs/by-path")
    def by_path(video_path: str):
        return {"video_path": video_path, "item": None}

    @app.get("/jobs")
    def list_jobs(page: int = 1, page_size: int = 20):
        return {"items": [], "total": 0, "page": page, "page_size": page_size}

    @app.get("/tasks")
    def list_tasks(page: int = 1, page_size: int = 20):
        return {"items": [], "total": 0, "page": page, "page_size": page_size}

    @app.get("/queue/summary")
    def queue_summary():
        return {"stages": {}, "workers": {}, "throughput": {}}

    return app
