from __future__ import annotations

from pathlib import Path
import subprocess


def preclean_output(path: Path) -> None:
    if path.exists():
        path.unlink()


def run_extract(input_video: Path, output_wav: Path, timeout_sec: int) -> None:
    preclean_output(output_wav)
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
        str(output_wav),
    ]
    subprocess.run(cmd, check=True, timeout=timeout_sec)


def run_stt(
    input_video: Path, output_ja_srt: Path, language: str, timeout_sec: int
) -> None:
    preclean_output(output_ja_srt)
    cmd = [
        "python",
        "whisper_stt/transcribe_video.py",
        "--input",
        str(input_video),
        "--output",
        str(output_ja_srt),
        "--language",
        language,
    ]
    subprocess.run(cmd, check=True, timeout=timeout_sec)


def run_translate(
    input_ja_srt: Path, output_zh_srt: Path, config_path: Path, timeout_sec: int
) -> None:
    preclean_output(output_zh_srt)
    progress_artifact = output_zh_srt.with_suffix(
        output_zh_srt.suffix + ".progress.json"
    )
    preclean_output(progress_artifact)
    cmd = [
        "python",
        "whisper_stt/translate_srt_ja_to_zh.py",
        "--input",
        str(input_ja_srt),
        "--output",
        str(output_zh_srt),
        "--config",
        str(config_path),
    ]
    subprocess.run(cmd, check=True, timeout=timeout_sec)
