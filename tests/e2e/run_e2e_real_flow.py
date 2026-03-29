from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import time

import requests


def load_video_paths(path_file: Path) -> list[str]:
    lines = [
        line.strip()
        for line in path_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not lines:
        raise ValueError("video_paths.txt is empty")
    return lines


def main() -> int:
    base = "http://127.0.0.1:8000"
    path_file = Path("tests/e2e/video_paths.txt")
    videos = load_video_paths(path_file)

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
        time.sleep(2)
        job_ids: list[str] = []
        for video in videos:
            response = requests.post(
                f"{base}/jobs",
                json={"video_path": video, "language": "ja"},
                timeout=30,
            )
            response.raise_for_status()
            job_ids.append(response.json()["job_id"])

        deadline = time.time() + 1800
        while time.time() < deadline:
            done = 0
            for job_id in job_ids:
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
            time.sleep(15)
        return 1
    finally:
        server.terminate()
        server.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
