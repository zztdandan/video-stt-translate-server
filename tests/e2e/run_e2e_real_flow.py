"""真实链路 E2E 驱动脚本。

脚本职责：启动服务、批量入队、轮询状态、在超时后退出。
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone

import requests


def load_video_paths(path_file: Path) -> list[str]:
    """读取视频绝对路径列表；文件为空时抛出异常。"""

    # 过滤空行，避免注入空路径到真实 API。
    lines = [
        line.strip()
        for line in path_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not lines:
        raise ValueError("video_paths.txt is empty")
    return lines


def _safe_json(resp: requests.Response) -> dict:
    """解析响应为 JSON；失败时返回空字典。"""

    try:
        data = resp.json()
        if isinstance(data, dict):
            return data
    except Exception:
        return {}
    return {}


def _format_task_line(task: dict) -> str:
    """把单个 task 结果格式化为易读行。"""

    task_id = str(task.get("task_id", ""))
    short_task = task_id[:16] if task_id else "unknown"
    stage = str(task.get("stage", "?"))
    status = str(task.get("status", "?"))
    attempt = int(task.get("attempt", 0))
    max_retries = int(task.get("max_retries", 0))
    part = f"{stage}:{status}({attempt}/{max_retries})#{short_task}"

    progress = task.get("progress")
    if isinstance(progress, dict):
        percent = progress.get("percent")
        message = progress.get("message")
        if isinstance(percent, (int, float)):
            part += f" {percent:6.2f}%"
        if isinstance(message, str) and message:
            part += f" {message}"

    err = task.get("last_error")
    if isinstance(err, str) and err.strip():
        compact_err = err.strip().replace("\n", " ")[:120]
        part += f" err={compact_err}"
    return part


def _print_round_summary(
    *,
    round_no: int,
    snapshots: list[dict],
    queue_summary: dict,
    log_root: Path,
) -> None:
    """每一轮轮询后统一打印 jobs/tasks/progress 的综合摘要。"""

    now = datetime.now(timezone.utc).isoformat()
    total = len(snapshots)
    status_counts: dict[str, int] = {}
    done = 0
    for item in snapshots:
        status = str(item.get("status", "unknown"))
        status_counts[status] = status_counts.get(status, 0) + 1
        if status in {"succeeded", "failed"}:
            done += 1

    ordered = sorted(status_counts.items(), key=lambda x: x[0])
    compact_counts = ", ".join([f"{k}={v}" for k, v in ordered])

    print(f"\n=== E2E Round {round_no} @ {now} ===", flush=True)
    print(f"jobs_done={done}/{total}; job_status=({compact_counts})", flush=True)

    stages = queue_summary.get("stages", {})
    if isinstance(stages, dict):
        stage_parts: list[str] = []
        for stage in sorted(stages.keys()):
            stat = stages.get(stage, {})
            if not isinstance(stat, dict):
                continue
            stage_parts.append(
                f"{stage}:q={int(stat.get('queued', 0))},c={int(stat.get('claimed', 0))},s={int(stat.get('succeeded', 0))},f={int(stat.get('failed', 0))}"
            )
        if stage_parts:
            print("queue=" + " | ".join(stage_parts), flush=True)

    workers = queue_summary.get("workers", {})
    if isinstance(workers, dict) and workers:
        worker_parts: list[str] = []
        for worker_id in sorted(workers.keys()):
            w = workers.get(worker_id, {})
            if not isinstance(w, dict):
                continue
            worker_parts.append(
                f"{worker_id}:{w.get('stage')}:{w.get('task_id') or '-'}"
            )
        print("workers=" + " | ".join(worker_parts), flush=True)

    print(f"task_logs_root={log_root}", flush=True)
    for item in snapshots:
        job_id = str(item.get("job_id", ""))
        short_job = job_id[:16] if job_id else "unknown"
        status = str(item.get("status", "unknown"))
        video = str(item.get("video_path", ""))
        tasks = item.get("tasks", [])
        if not isinstance(tasks, list):
            tasks = []
        parts = [_format_task_line(t) for t in tasks if isinstance(t, dict)]
        print(f"- job {short_job} {status} | {video}", flush=True)
        if parts:
            print("  tasks: " + " || ".join(parts), flush=True)


def main() -> int:
    """执行 E2E 主流程，成功返回 0，超时返回 1。"""

    base = "http://127.0.0.1:8000"
    path_file = Path("tests/e2e/video_paths.txt")
    videos = load_video_paths(path_file)

    # requests 默认会读取系统代理环境变量，某些 NO_PROXY 通配写法可能不生效。
    # 这里强制关闭环境代理，确保 127.0.0.1 请求直连本地 uvicorn。
    session = requests.Session()
    session.trust_env = False

    # 独立子进程启动服务，模拟真实部署中的 API 入口。
    log_root = Path("tmp/logs").resolve()
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "whisper_stt_service.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
            "--no-access-log",
        ]
    )
    try:
        # 给服务一个最小启动窗口，避免首个请求连接失败。
        time.sleep(2)

        # 先做健康检查，便于区分“服务未启动”和“业务接口失败”。
        health = session.get(f"{base}/docs", timeout=30)
        health.raise_for_status()

        job_ids: list[str] = []
        for video in videos:
            # 逐条入队，记录返回的 job_id 用于后续轮询。
            response = session.post(
                f"{base}/jobs",
                json={"video_path": video, "language": "ja"},
                timeout=30,
            )
            response.raise_for_status()
            job_ids.append(response.json()["job_id"])

        # 最长等待 120 分钟，覆盖大视频耗时场景。
        deadline = time.time() + 7200
        round_no = 0
        while time.time() < deadline:
            round_no += 1
            done = 0
            snapshots: list[dict] = []
            for job_id in job_ids:
                # 先请求 jobs 与 progress，再统一打印综合摘要。
                job_resp = session.get(f"{base}/jobs/{job_id}", timeout=30)
                prog_resp = session.get(f"{base}/jobs/{job_id}/progress", timeout=30)
                job_payload = _safe_json(job_resp)
                progress_payload = _safe_json(prog_resp)

                # progress 接口同样返回 tasks，这里优先使用 progress 中的 tasks（含进度快照）。
                merged = dict(job_payload)
                if isinstance(progress_payload.get("tasks"), list):
                    merged["tasks"] = progress_payload["tasks"]
                snapshots.append(merged)

                status = merged.get("status")
                if status in {"succeeded", "failed"}:
                    done += 1

            queue_summary_resp = session.get(f"{base}/queue/summary", timeout=30)
            queue_summary = _safe_json(queue_summary_resp)
            _print_round_summary(
                round_no=round_no,
                snapshots=snapshots,
                queue_summary=queue_summary,
                log_root=log_root,
            )

            if done == len(job_ids):
                return 0
            # 轮询间隔固定 15 秒，平衡时效与请求开销。
            time.sleep(15)
        return 1
    finally:
        # 无论成功或失败都尝试回收服务进程。
        server.terminate()
        server.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
