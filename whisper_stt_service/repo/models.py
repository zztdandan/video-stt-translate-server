"""仓储层数据结构。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EnqueueResult:
    """入队结果载体。"""

    job_id: str
    accepted: bool
    message: str
    queue_ahead: int
    plan_mode: str
    stages: list[str]


@dataclass
class ClaimedTask:
    """领取到的任务最小信息。"""

    task_id: str
    job_id: str
    stage: str


@dataclass
class TaskExecutionContext:
    """worker 执行任务时需要的上下文。"""

    task_id: str
    job_id: str
    stage: str
    video_path: str
    source_language: str
    output_ja_path: str
    output_zh_path: str
    timeout_sec: int
    attempt: int
    max_retries: int
    log_dir: str
    log_file: str
    task_config: dict
