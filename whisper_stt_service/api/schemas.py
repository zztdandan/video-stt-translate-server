"""API 请求模型。"""

from __future__ import annotations

from pydantic import BaseModel


class CreateJobReq(BaseModel):
    """创建任务请求体。"""

    video_path: str
    language: str
    dag: dict | None = None
    job_config: dict | None = None


class ArchiveJobReq(BaseModel):
    """归档请求体。"""

    reason: str = "manual_archive"
