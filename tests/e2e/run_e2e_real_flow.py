"""真实链路 E2E 驱动脚本。

脚本职责：启动服务、批量入队、轮询状态、在超时后退出。
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import time

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


def main() -> int:
    """执行 E2E 主流程，成功返回 0，超时返回 1。"""

    base = "http://127.0.0.1:8000"
    path_file = Path("tests/e2e/video_paths.txt")
    videos = load_video_paths(path_file)

    # 独立子进程启动服务，模拟真实部署中的 API 入口。
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
        ]
    )
    try:
        # 给服务一个最小启动窗口，避免首个请求连接失败。
        time.sleep(2)
        job_ids: list[str] = []
        for video in videos:
            # 逐条入队，记录返回的 job_id 用于后续轮询。
            response = requests.post(
                f"{base}/jobs",
                json={"video_path": video, "language": "ja"},
                timeout=30,
            )
            response.raise_for_status()
            job_ids.append(response.json()["job_id"])

        # 最长等待 30 分钟，覆盖大视频耗时场景。
        deadline = time.time() + 1800
        while time.time() < deadline:
            done = 0
            for job_id in job_ids:
                # 组合查询 job 状态与 progress 快照。
                status = (
                    requests.get(f"{base}/jobs/{job_id}", timeout=30)
                    .json()
                    .get("status")
                )
                _ = requests.get(f"{base}/jobs/{job_id}/progress", timeout=30).json()
                if status in {"succeeded", "failed"}:
                    done += 1
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
