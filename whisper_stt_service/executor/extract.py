"""extract 阶段执行器。"""

from __future__ import annotations

import time
from pathlib import Path
from queue import Queue
from typing import Any
import subprocess

from whisper_stt_service.executor.common import (
    _emit_progress,
    _probe_duration,
    preclean_output,
)


def run_extract(
    input_video: Path,
    output_wav: Path,
    timeout_sec: int,
    *,
    progress_queue: Queue[dict[str, Any]] | None = None,
    task_id: str | None = None,
    worker_id: str | None = None,
) -> None:
    """调用 ffmpeg 抽取 16k 单声道 wav，并把进度写入队列。"""

    # 启动前清理，避免断点产物影响重复执行。
    preclean_output(output_wav)
    output_wav.parent.mkdir(parents=True, exist_ok=True)

    duration = _probe_duration(input_video)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_video),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        "-progress",
        "pipe:1",
        "-nostats",
        str(output_wav),
    ]
    _emit_progress(
        progress_queue,
        stage="extract",
        percent=0.0,
        message="extract_started",
        task_id=task_id,
        worker_id=worker_id,
    )

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    started = time.perf_counter()
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.strip()
        if not line.startswith("out_time_ms="):
            continue
        if duration is None or duration <= 0:
            continue
        try:
            out_sec = float(line.split("=", 1)[1].strip()) / 1_000_000.0
        except ValueError:
            continue
        _emit_progress(
            progress_queue,
            stage="extract",
            percent=min(out_sec / duration, 1.0) * 100.0,
            message="extract_running",
            task_id=task_id,
            worker_id=worker_id,
        )
        if time.perf_counter() - started > timeout_sec:
            proc.kill()
            raise TimeoutError("extract_timeout")

    ret = proc.wait(timeout=max(timeout_sec, 1))
    if ret != 0:
        stderr_text = ""
        if proc.stderr is not None:
            stderr_text = proc.stderr.read().strip()
        raise RuntimeError(f"ffmpeg extraction failed: {stderr_text}")

    _emit_progress(
        progress_queue,
        stage="extract",
        percent=100.0,
        message="extract_done",
        task_id=task_id,
        worker_id=worker_id,
    )
