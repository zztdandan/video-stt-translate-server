"""显式 DAG 真实链路 E2E 驱动脚本。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import time

import requests


def load_video_paths(path_file: Path) -> list[str]:
    """读取视频绝对路径列表；文件为空时抛出异常。"""

    lines = [
        line.strip()
        for line in path_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not lines:
        raise ValueError("video_paths.txt is empty")
    return lines


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="Run explicit DAG e2e flow")
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="service base url",
    )
    parser.add_argument(
        "--api-token",
        default="",
        help="global API token for X-API-Token header",
    )
    parser.add_argument(
        "--video-paths",
        default="tests/e2e/video_paths.txt",
        help="video paths file",
    )
    parser.add_argument(
        "--run-mode",
        choices=("baseline", "continuous", "until_done"),
        default="continuous",
        help="baseline=5min gate, continuous=1min quick check, until_done=wait all jobs done",
    )
    parser.add_argument(
        "--min-monitor-sec",
        type=int,
        default=0,
        help="minimum monitor duration in seconds; <=0 means auto by run-mode",
    )
    parser.add_argument(
        "--poll-sec",
        type=int,
        default=15,
        help="poll interval seconds",
    )
    parser.add_argument(
        "--monitor-log",
        default="tmp/e2e/explicit_dag_monitor.log",
        help="monitor log path",
    )
    parser.add_argument(
        "--server-log",
        default="tmp/e2e/explicit_dag_server.log",
        help="server log path",
    )
    parser.add_argument(
        "--deadline-sec",
        type=int,
        default=7200,
        help="absolute timeout for the whole e2e run",
    )
    return parser.parse_args(argv)


def _safe_json(resp: requests.Response) -> dict:
    """解析响应为 JSON；失败时返回空字典。"""

    try:
        data = resp.json()
    except Exception:
        return {}
    if isinstance(data, dict):
        return data
    return {}


def _build_payload(video_path: str) -> dict:
    """构建显式 DAG + 显式 job_config 的请求体。"""

    return {
        "video_path": video_path,
        "language": "ja",
        "dag": {
            "version": 1,
            "stages": [
                {"stage": "extract", "depends_on": []},
                {"stage": "stt_whisperx", "depends_on": ["extract"]},
                {"stage": "translate", "depends_on": ["stt_whisperx"]},
            ],
        },
        "job_config": {
            "stt_whisperx": {
                "batch_size": 16,
                "align_enabled": True,
            },
            "translate": {
                "chunk_minutes": 20,
                "retry": 8,
            },
        },
    }


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


def _build_round_lines(
    *,
    round_no: int,
    snapshots: list[dict],
    queue_summary: dict,
    log_root: Path,
) -> list[str]:
    """生成和旧脚本风格一致的轮询摘要文本。"""

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

    lines: list[str] = []
    lines.append("")
    lines.append(f"=== E2E Round {round_no} @ {now} ===")
    lines.append(f"jobs_done={done}/{total}; job_status=({compact_counts})")

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
            lines.append("queue=" + " | ".join(stage_parts))

    workers = queue_summary.get("workers", {})
    if isinstance(workers, dict) and workers:
        worker_parts: list[str] = []
        for worker_id in sorted(workers.keys()):
            worker = workers.get(worker_id, {})
            if not isinstance(worker, dict):
                continue
            worker_parts.append(
                f"{worker_id}:{worker.get('stage')}:{worker.get('task_id') or '-'}"
            )
        lines.append("workers=" + " | ".join(worker_parts))

    lines.append(f"task_logs_root={log_root}")
    for item in snapshots:
        job_id = str(item.get("job_id", ""))
        short_job = job_id[:16] if job_id else "unknown"
        status = str(item.get("status", "unknown"))
        video = str(item.get("video_path", ""))
        tasks = item.get("tasks", [])
        if not isinstance(tasks, list):
            tasks = []
        parts = [_format_task_line(t) for t in tasks if isinstance(t, dict)]
        lines.append(f"- job {short_job} {status} | {video}")
        if parts:
            lines.append("  tasks: " + " || ".join(parts))
    return lines


def _append_monitor_lines(path: Path, lines: list[str]) -> None:
    """把监控文本块追加写入日志并同步输出。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")
            print(line, flush=True)


def main(argv: list[str] | None = None) -> int:
    """执行显式 DAG E2E。"""

    args = parse_args(argv)
    base_url = args.base_url.rstrip("/")
    api_token = str(args.api_token).strip()
    videos = load_video_paths(Path(args.video_paths))
    monitor_log = Path(args.monitor_log)
    server_log = Path(args.server_log)
    monitor_sec = args.min_monitor_sec
    if monitor_sec <= 0:
        if args.run_mode == "baseline":
            monitor_sec = 300
        elif args.run_mode == "continuous":
            monitor_sec = 60
        else:
            monitor_sec = 0

    monitor_log.parent.mkdir(parents=True, exist_ok=True)
    monitor_log.write_text("", encoding="utf-8")
    server_log.parent.mkdir(parents=True, exist_ok=True)

    server_fp = server_log.open("w", encoding="utf-8")
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
        ],
        stdout=server_fp,
        stderr=server_fp,
    )

    session = requests.Session()
    session.trust_env = False
    headers: dict[str, str] = {}
    if api_token:
        headers["X-API-Token"] = api_token
    started_at = time.time()
    deadline = started_at + max(args.deadline_sec, monitor_sec)
    min_end = started_at + max(monitor_sec, 1)

    exit_code = 1
    exit_reason = "unexpected_exit"

    try:
        time.sleep(2)
        health = session.get(f"{base_url}/docs", timeout=30)
        health.raise_for_status()

        job_ids: list[str] = []
        for video_path in videos:
            resp = session.post(
                f"{base_url}/jobs",
                json=_build_payload(video_path),
                headers=headers,
                timeout=30,
            )
            resp.raise_for_status()
            payload = _safe_json(resp)
            job_ids.append(str(payload["job_id"]))

        round_no = 0
        while time.time() < deadline:
            round_no += 1
            failed = 0
            all_succeeded = True
            snapshots: list[dict] = []
            for job_id in job_ids:
                job_resp = session.get(
                    f"{base_url}/jobs/{job_id}", headers=headers, timeout=30
                )
                prog_resp = session.get(
                    f"{base_url}/jobs/{job_id}/progress", headers=headers, timeout=30
                )
                job_payload = _safe_json(job_resp)
                progress_payload = _safe_json(prog_resp)

                merged = dict(job_payload)
                if isinstance(progress_payload.get("tasks"), list):
                    merged["tasks"] = progress_payload["tasks"]

                status = str(merged.get("status", "unknown"))
                if status == "failed":
                    failed += 1
                if status != "succeeded":
                    all_succeeded = False
                snapshots.append(merged)

            queue_summary = _safe_json(
                session.get(f"{base_url}/queue/summary", headers=headers, timeout=30)
            )
            lines = _build_round_lines(
                round_no=round_no,
                snapshots=snapshots,
                queue_summary=queue_summary,
                log_root=Path("tmp/logs").resolve(),
            )
            _append_monitor_lines(monitor_log, lines)

            reached_min = time.time() >= min_end
            if failed > 0:
                exit_reason = f"failed_jobs_detected:{failed}"
                exit_code = 1
                break
            if args.run_mode == "until_done" and all_succeeded:
                exit_reason = "all_jobs_succeeded"
                exit_code = 0
                break
            if args.run_mode != "until_done" and reached_min:
                exit_reason = f"monitor_window_reached:{args.run_mode}"
                exit_code = 0
                break
            time.sleep(max(args.poll_sec, 1))
        else:
            exit_reason = "deadline_reached"
            exit_code = 1
    except KeyboardInterrupt:
        exit_reason = "interrupted_by_user"
        exit_code = 130
    except Exception as exc:
        exit_reason = f"exception:{type(exc).__name__}:{exc}"
        exit_code = 1
    finally:
        server.terminate()
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
        finally:
            server_fp.close()

        end_line = (
            f"E2E_EXIT code={exit_code} reason={exit_reason} "
            f"at={datetime.now(timezone.utc).isoformat()}"
        )
        _append_monitor_lines(monitor_log, [end_line])

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
