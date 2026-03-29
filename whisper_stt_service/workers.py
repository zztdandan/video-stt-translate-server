"""Worker 循环与启动恢复逻辑。"""

from __future__ import annotations

from threading import Event
from time import sleep

from whisper_stt_service.db import Database
from whisper_stt_service.repository import JobRepository


def recover_claimed_to_queued(db: Database) -> int:
    """服务启动时把历史 claimed 任务回退到 queued。"""

    with db.tx() as conn:
        changed = conn.execute(
            "UPDATE tasks SET status='queued', lease_owner=NULL, lease_expires_at=NULL, updated_at=datetime('now') WHERE status='claimed'"
        ).rowcount
        return int(changed)


def worker_loop(
    repo: JobRepository,
    stage: str,
    worker_id: str,
    stop_event: Event,
    poll_interval_sec: int,
) -> None:
    """最小 worker 主循环：领取任务，空闲则休眠。"""

    while not stop_event.is_set():
        task = repo.claim_next(stage=stage, worker_id=worker_id, lease_timeout_sec=600)
        if task is None:
            # 无任务可做时短暂休眠，减少空转查询。
            sleep(poll_interval_sec)
            continue
        # 当前最小骨架仅验证流转，把任务直接标记成功。
        repo.mark_task_succeeded(task.task_id)
