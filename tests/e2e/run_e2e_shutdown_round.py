"""优雅停机双轮验证脚本（按 round 参数单轮执行）。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import time

import requests


def _safe_json(resp: requests.Response) -> dict:
    """解析 JSON 响应；失败时返回空对象。"""

    try:
        data = resp.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _load_videos(path_file: Path) -> list[str]:
    """读取视频列表；空文件直接报错。"""

    items = [
        line.strip()
        for line in path_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not items:
        raise ValueError("video_paths.txt is empty")
    return items


def _enqueue_job(
    session: requests.Session,
    *,
    base_url: str,
    headers: dict[str, str],
    video: str,
) -> tuple[bool, str]:
    """提交单条视频任务；遇到可接受冲突时返回 skipped。"""

    # round 压测关注点是 shutdown/drain 行为；缺失视频可直接跳过，避免阻断流程。
    if not Path(video).is_file():
        return False, "video_path_not_found"

    resp = session.post(
        f"{base_url}/jobs",
        json={
            "video_path": video,
            "language": "ja",
            "dag": {
                "version": 1,
                "stages": [
                    {"stage": "extract", "depends_on": []},
                    {"stage": "stt_whisperx", "depends_on": ["extract"]},
                    {"stage": "translate", "depends_on": ["stt_whisperx"]},
                ],
            },
        },
        headers=headers,
        timeout=30,
    )
    payload = _safe_json(resp)
    if resp.status_code < 400:
        return True, str(payload.get("job_id", ""))

    detail = str(payload.get("detail", "")).strip()
    # 兼容重复提交或历史已启动任务，round 测试无需因该类冲突失败。
    if detail in {"idempotent_returned", "rejected_started", "video_path_not_found"}:
        return False, detail

    try:
        resp.raise_for_status()
    except Exception as exc:
        raise RuntimeError(
            f"enqueue_failed status={resp.status_code} detail={detail or payload}"
        ) from exc
    return False, "unknown"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析 round 测试参数。"""

    parser = argparse.ArgumentParser(description="Run shutdown round e2e")
    parser.add_argument("--round", choices=("round1", "round2"), required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--api-token", required=True)
    parser.add_argument("--video-paths", default="tests/e2e/video_paths.txt")
    parser.add_argument("--shutdown-after-sec", type=int, default=10)
    parser.add_argument("--poll-sec", type=int, default=2)
    parser.add_argument("--deadline-sec", type=int, default=7200)
    parser.add_argument("--monitor-log", default="tmp/e2e/shutdown_round_monitor.log")
    parser.add_argument("--server-log", default="tmp/e2e/shutdown_round_server.log")
    return parser.parse_args(argv)


def _append_log(path: Path, line: str) -> None:
    """同时写监控日志并打印到控制台。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


def main(argv: list[str] | None = None) -> int:
    """执行单轮 round 测试：启动服务、可选提交任务、10 秒后发停机。"""

    args = parse_args(argv)
    monitor_log = Path(args.monitor_log)
    server_log = Path(args.server_log)
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
    headers = {"X-API-Token": args.api_token.strip()}
    started = time.time()
    shutdown_sent = False
    deadline = started + max(args.deadline_sec, args.shutdown_after_sec + 60)
    exit_code = 1

    _append_log(
        monitor_log,
        f"ROUND_START round={args.round} at={datetime.now(timezone.utc).isoformat()}",
    )

    try:
        time.sleep(2)
        health = session.get(f"{args.base_url.rstrip('/')}/docs", timeout=30)
        health.raise_for_status()

        if args.round == "round1":
            videos = _load_videos(Path(args.video_paths))
            time.sleep(1)
            for idx, video in enumerate(videos, start=1):
                accepted, message = _enqueue_job(
                    session,
                    base_url=args.base_url.rstrip("/"),
                    headers=headers,
                    video=video,
                )
                if accepted:
                    _append_log(
                        monitor_log,
                        f"ROUND_INFO round={args.round} enqueue={idx}/{len(videos)} job_id={message}",
                    )
                else:
                    _append_log(
                        monitor_log,
                        f"ROUND_WARN round={args.round} enqueue={idx}/{len(videos)} skipped_reason={message}",
                    )

        while time.time() < deadline:
            elapsed = int(time.time() - started)
            if (not shutdown_sent) and elapsed >= args.shutdown_after_sec:
                shutdown_sent = True
                resp = session.post(
                    f"{args.base_url.rstrip('/')}/admin/shutdown",
                    json={"reason": f"e2e_{args.round}"},
                    headers=headers,
                    timeout=30,
                )
                resp.raise_for_status()
                _append_log(
                    monitor_log,
                    "ROUND_EVENT shutdown_requested "
                    f"round={args.round} elapsed_sec={elapsed} payload={_safe_json(resp)}",
                )

            status_resp = session.get(
                f"{args.base_url.rstrip('/')}/admin/shutdown/status",
                headers=headers,
                timeout=30,
            )
            status_payload = _safe_json(status_resp)
            queue_resp = session.get(
                f"{args.base_url.rstrip('/')}/queue/summary",
                headers=headers,
                timeout=30,
            )
            queue_payload = _safe_json(queue_resp)
            _append_log(
                monitor_log,
                "ROUND_STATUS "
                f"round={args.round} elapsed_sec={elapsed} shutdown={status_payload} "
                f"queue={queue_payload.get('stages', {})}",
            )

            code = server.poll()
            if code is not None:
                exit_code = 0 if shutdown_sent else 1
                _append_log(
                    monitor_log,
                    "ROUND_EXIT "
                    f"round={args.round} code={code} shutdown_sent={shutdown_sent} "
                    f"at={datetime.now(timezone.utc).isoformat()}",
                )
                return exit_code

            time.sleep(max(args.poll_sec, 1))
    except Exception as exc:  # noqa: BLE001
        _append_log(
            monitor_log,
            f"ROUND_ERROR round={args.round} error_type={type(exc).__name__} error={exc}",
        )
        return 1
    finally:
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
        server_fp.close()

    _append_log(
        monitor_log,
        f"ROUND_TIMEOUT round={args.round} at={datetime.now(timezone.utc).isoformat()}",
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
