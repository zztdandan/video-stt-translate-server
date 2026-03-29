from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock


@dataclass
class ProgressItem:
    percent: float
    message: str
    worker_id: str
    ts: datetime
    done_at: datetime | None = None


class ProgressStore:
    def __init__(self, ttl_seconds: int) -> None:
        self.ttl = timedelta(seconds=ttl_seconds)
        self._items: dict[str, ProgressItem] = {}
        self._lock = Lock()

    def update(
        self,
        task_id: str,
        percent: float,
        message: str,
        worker_id: str,
        ts: datetime | None = None,
    ) -> None:
        now = ts or datetime.now(timezone.utc)
        with self._lock:
            self._items[task_id] = ProgressItem(
                percent=percent, message=message, worker_id=worker_id, ts=now
            )

    def mark_done(self, task_id: str, ts: datetime | None = None) -> None:
        now = ts or datetime.now(timezone.utc)
        with self._lock:
            item = self._items.get(task_id)
            if item is not None:
                item.done_at = now
                item.ts = now

    def cleanup(self, now: datetime | None = None) -> None:
        ref = now or datetime.now(timezone.utc)
        with self._lock:
            expired = [
                key
                for key, item in self._items.items()
                if item.done_at is not None and (ref - item.done_at) > self.ttl
            ]
            for key in expired:
                self._items.pop(key, None)

    def snapshot(self, task_id: str) -> dict[str, dict]:
        with self._lock:
            item = self._items.get(task_id)
            if item is None:
                return {}
            return {
                task_id: {
                    "percent": item.percent,
                    "message": item.message,
                    "worker_id": item.worker_id,
                    "updated_at": item.ts.isoformat(),
                }
            }
