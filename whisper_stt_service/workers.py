from __future__ import annotations

from threading import Event
from time import sleep

from whisper_stt_service.db import Database
from whisper_stt_service.repository import JobRepository


def recover_claimed_to_queued(db: Database) -> int:
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
    while not stop_event.is_set():
        task = repo.claim_next(stage=stage, worker_id=worker_id, lease_timeout_sec=600)
        if task is None:
            sleep(poll_interval_sec)
            continue
        repo.mark_task_succeeded(task.task_id)
