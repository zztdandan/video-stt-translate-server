"""执行器共享数据结构与通用工具。"""

from __future__ import annotations

import configparser
import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from queue import Full, Queue
from typing import Any


logger = logging.getLogger(__name__)


@dataclass
class SrtEntry:
    """SRT 条目结构。"""

    index: int
    timestamp: str
    text: str


def _timestamp_to_seconds(ts: str) -> float:
    """将 `HH:MM:SS,mmm` 转为秒；格式不合法时抛 ValueError。"""

    hhmmss, millis = ts.split(",", 1)
    h, m, s = hhmmss.split(":", 2)
    return int(h) * 3600 + int(m) * 60 + int(s) + int(millis) / 1000.0


def _entry_start_seconds(timestamp: str) -> float:
    """解析 SRT 时间轴起点秒数，失败时退化为 0。"""

    try:
        start_text = timestamp.split("-->", 1)[0].strip()
        return _timestamp_to_seconds(start_text)
    except Exception:
        return 0.0


def _emit_progress(
    progress_queue: Queue[dict[str, Any]] | None,
    *,
    stage: str,
    percent: float,
    message: str,
    task_id: str | None,
    worker_id: str | None,
) -> None:
    """把进度写入内存队列；队列满时丢弃一个旧事件后重试。"""

    if progress_queue is None:
        return
    event = {
        "task_id": task_id or "",
        "stage": stage,
        "percent": max(0.0, min(percent, 100.0)),
        "message": message,
        "worker_id": worker_id or "",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    try:
        progress_queue.put_nowait(event)
    except Full:
        try:
            progress_queue.get_nowait()
        except Exception:
            pass
        try:
            progress_queue.put_nowait(event)
        except Exception:
            pass


def _probe_duration(path: Path) -> float | None:
    """用 ffprobe 获取媒体时长，失败时返回 None。"""

    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return None
    text = proc.stdout.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def preclean_output(path: Path) -> None:
    """执行前清理：组合多种策略，尽可能释放目标输出路径。"""

    failures: list[str] = []

    def _record_failure(step: str, exc: Exception) -> None:
        failures.append(f"{step}: {exc.__class__.__name__}: {exc}")

    def _run_silent_rm(target: Path, recursive: bool = False) -> int:
        args = ["rm", "-f"]
        if recursive:
            args.append("-r")
        args.extend(["--", str(target)])
        proc = subprocess.run(
            args,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return proc.returncode

    def _path_lexists(target: Path) -> bool:
        try:
            return os.path.lexists(target)
        except Exception:
            return target.exists()

    # SMB 挂载下可能出现 “ls 可见但路径操作报不存在” 的残留目录项。
    # 先执行 rm，再走 Python 与系统调用多轮清理，双保险降低 ffmpeg 输出冲突概率。
    for attempt in range(1, 6):
        # 第一层：直接走 rm，兼容网络文件系统上“可见但 stat 异常”的目录项。
        try:
            rc = _run_silent_rm(path, recursive=True)
            if rc != 0:
                failures.append(f"rm: returncode={rc}")
        except Exception as exc:
            _record_failure("rm", exc)

        # 第二层：尝试修正权限后删除，兼容只读位阻塞。
        try:
            path.chmod(0o666)
        except Exception as exc:
            _record_failure("chmod", exc)

        # 第三层：Python 侧按类型删除，覆盖文件/目录/符号链接。
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
        except Exception as exc:
            _record_failure("python_unlink", exc)

        # 第四层：直接系统调用兜底。
        try:
            os.remove(path)
        except Exception as exc:
            _record_failure("os.remove", exc)
        try:
            os.rmdir(path)
        except Exception as exc:
            _record_failure("os.rmdir", exc)

        # 第五层：路径仍被占用时，先改名让原目标路径可复用，再后台清理墓碑文件。
        if _path_lexists(path):
            tombstone = path.with_name(f".{path.name}.preclean.{time.time_ns()}")
            try:
                os.replace(path, tombstone)
                try:
                    rc = _run_silent_rm(tombstone, recursive=True)
                    if rc != 0:
                        failures.append(f"rm_tombstone: returncode={rc}")
                except Exception as exc:
                    _record_failure("rm_tombstone", exc)
                try:
                    if tombstone.is_dir() and not tombstone.is_symlink():
                        shutil.rmtree(tombstone, ignore_errors=True)
                    else:
                        tombstone.unlink(missing_ok=True)
                except Exception as exc:
                    _record_failure("python_unlink_tombstone", exc)
            except Exception as exc:
                _record_failure("os.replace_tombstone", exc)

        if not _path_lexists(path):
            return
        if attempt == 5:
            logger.error(
                "preclean_output failed, target remains: path=%s failures=%s",
                path,
                " | ".join(failures) if failures else "unknown",
            )
        time.sleep(0.05)

    # 清理失败不应阻断后续流程，让 ffmpeg 自行报告可复现错误。


def _split_entries_by_time_window(
    entries: list[SrtEntry], window_minutes: int = 30
) -> list[list[SrtEntry]]:
    """按字幕起始时间切分窗口，默认每 30 分钟一个翻译批次。"""

    window_sec = max(window_minutes, 1) * 60
    grouped: dict[int, list[SrtEntry]] = {}
    for entry in entries:
        bucket = int(_entry_start_seconds(entry.timestamp) // window_sec)
        grouped.setdefault(bucket, []).append(entry)
    return [grouped[k] for k in sorted(grouped)]


def _build_translate_messages(batch: list[SrtEntry]) -> list[dict[str, str]]:
    """构造翻译消息，强调剧情上下文推理和全段一致性。"""

    payload_items = [{"id": e.index, "text": e.text} for e in batch]
    system_prompt = (
        "You are a professional subtitle translator. Translate Japanese subtitles into natural, concise "
        "Simplified Chinese. Use context and plot continuity to infer omitted subjects, keep naming and tone "
        "consistent across this whole chunk, and avoid literal word-by-word translation. Intelligently clean "
        "disfluencies and meaningless repetitions (such as long runs of interjections or stutters like "
        "'ああああ', 'えええ', 'ううう'), compressing them into natural Chinese or removing them when they "
        "carry no semantic meaning, while preserving the speaker's intent and emotion. Think carefully, but "
        "output ONLY JSON."
    )
    user_prompt = (
        "Translate the following JSON array from Japanese to Simplified Chinese subtitles. "
        "Return ONLY a JSON array with objects in the form: "
        '{"id": <same id>, "text_zh": "..."}. Keep the same ids and same item count. '
        "For repetitive filler words, perform intelligent denoising and output fluent subtitles.\n\n"
        f"{json.dumps(payload_items, ensure_ascii=False)}"
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def _parse_srt(content: str) -> list[SrtEntry]:
    """解析 SRT 文本为结构化条目。"""

    blocks = [
        b.strip() for b in content.replace("\r\n", "\n").split("\n\n") if b.strip()
    ]
    entries: list[SrtEntry] = []
    for block in blocks:
        lines = block.split("\n")
        if len(lines) < 3:
            continue
        try:
            idx = int(lines[0].strip())
        except ValueError:
            continue
        entries.append(
            SrtEntry(
                index=idx, timestamp=lines[1].strip(), text="\n".join(lines[2:]).strip()
            )
        )
    return entries


def _dump_srt(
    entries: list[SrtEntry], translations: dict[int, str], out_path: Path
) -> None:
    """按原时间轴写回翻译后的 SRT 文件。"""

    with out_path.open("w", encoding="utf-8") as f:
        for entry in entries:
            text_zh = translations.get(entry.index, entry.text)
            f.write(f"{entry.index}\n{entry.timestamp}\n{text_zh}\n\n")


def _extract_json_object(text: str) -> Any:
    """从模型返回文本中提取 JSON 对象/数组。"""

    payload = text.strip()
    if payload.startswith("```") and payload.endswith("```"):
        lines = payload.splitlines()
        if len(lines) >= 3:
            payload = "\n".join(lines[1:-1]).strip()
    if payload.startswith("json"):
        payload = payload[4:].strip()
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        pass
    arr_start = payload.find("[")
    arr_end = payload.rfind("]")
    if arr_start != -1 and arr_end > arr_start:
        return json.loads(payload[arr_start : arr_end + 1])
    obj_start = payload.find("{")
    obj_end = payload.rfind("}")
    if obj_start != -1 and obj_end > obj_start:
        return json.loads(payload[obj_start : obj_end + 1])
    raise RuntimeError("unable to parse JSON response")


def _load_llm_config(config_path: Path) -> configparser.ConfigParser:
    """加载 config.ini（若不存在则返回空配置）。"""

    cfg = configparser.ConfigParser()
    if config_path.is_file():
        cfg.read(config_path, encoding="utf-8")
    return cfg
