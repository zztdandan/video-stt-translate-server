"""三阶段执行器封装：extract / stt / translate。"""

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
from typing import Any, Callable

import requests


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
        "consistent across this whole chunk, and avoid literal word-by-word translation. Think carefully, "
        "but output ONLY JSON."
    )
    user_prompt = (
        "Translate the following JSON array from Japanese to Simplified Chinese subtitles. "
        "Return ONLY a JSON array with objects in the form: "
        '{"id": <same id>, "text_zh": "..."}. Keep the same ids and same item count.\n\n'
        f"{json.dumps(payload_items, ensure_ascii=False)}"
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


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


def _format_timestamp(seconds: float) -> str:
    """秒转 SRT 时间戳格式。"""

    total_millis = int(max(seconds, 0.0) * 1000)
    hours = total_millis // 3_600_000
    minutes = (total_millis % 3_600_000) // 60_000
    secs = (total_millis % 60_000) // 1000
    millis = total_millis % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _resolve_runtime(device_arg: str, compute_arg: str) -> tuple[str, str]:
    """根据 auto 配置解析运行设备与精度。"""

    device = device_arg
    if device_arg == "auto":
        try:
            import torch  # type: ignore

            device = "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            device = "cpu"

    compute = compute_arg
    if compute_arg == "auto":
        compute = "float16" if device == "cuda" else "int8"
    return device, compute


def build_stt_effective_config(
    *,
    model: str,
    language: str,
    timeout_sec: int,
    max_retries: int,
    device: str,
    compute_type: str,
    beam_size: int,
    best_of: int,
    patience: float,
    condition_on_previous_text: bool,
    vad_filter: bool,
    vad_threshold: float,
    vad_min_speech_duration_ms: int,
    vad_max_speech_duration_s: float,
    vad_min_silence_duration_ms: int,
    vad_speech_pad_ms: int,
    no_speech_threshold: float,
    compression_ratio_threshold: float,
    log_prob_threshold: float,
    hallucination_silence_threshold: float,
    initial_prompt: str,
    hotwords: str,
) -> dict[str, Any]:
    """构造 STT 实际生效参数快照，便于日志审计与问题排查。"""

    resolved_device, resolved_compute = _resolve_runtime(device, compute_type)
    return {
        "timeout_sec": int(timeout_sec),
        "max_retries": int(max_retries),
        "model": model,
        "language": language,
        "device": device,
        "compute_type": compute_type,
        "resolved_device": resolved_device,
        "resolved_compute_type": resolved_compute,
        "beam_size": max(beam_size, 1),
        "best_of": max(best_of, 1),
        "patience": max(patience, 0.1),
        "condition_on_previous_text": condition_on_previous_text,
        "vad_filter": vad_filter,
        "vad_threshold": min(max(vad_threshold, 0.01), 0.99),
        "vad_min_speech_duration_ms": max(vad_min_speech_duration_ms, 50),
        "vad_max_speech_duration_s": max(vad_max_speech_duration_s, 1.0),
        "vad_min_silence_duration_ms": max(vad_min_silence_duration_ms, 50),
        "vad_speech_pad_ms": max(vad_speech_pad_ms, 0),
        "no_speech_threshold": min(max(no_speech_threshold, 0.01), 0.99),
        "compression_ratio_threshold": max(compression_ratio_threshold, 0.1),
        "log_prob_threshold": log_prob_threshold,
        "hallucination_silence_threshold": max(hallucination_silence_threshold, 0.0),
        "initial_prompt": initial_prompt.strip(),
        "hotwords": hotwords.strip(),
    }


def run_stt(
    input_video: Path,
    output_ja_srt: Path,
    language: str,
    timeout_sec: int,
    *,
    model: str = "models/faster-whisper-small",
    device: str = "auto",
    compute_type: str = "auto",
    beam_size: int = 5,
    best_of: int = 5,
    patience: float = 1.0,
    condition_on_previous_text: bool = False,
    vad_filter: bool = True,
    vad_threshold: float = 0.45,
    vad_min_speech_duration_ms: int = 200,
    vad_max_speech_duration_s: float = 18.0,
    vad_min_silence_duration_ms: int = 700,
    vad_speech_pad_ms: int = 300,
    no_speech_threshold: float = 0.6,
    compression_ratio_threshold: float = 2.2,
    log_prob_threshold: float = -1.0,
    hallucination_silence_threshold: float = 1.5,
    initial_prompt: str = "",
    hotwords: str = "",
    progress_every: int = 25,
    progress_queue: Queue[dict[str, Any]] | None = None,
    task_id: str | None = None,
    worker_id: str | None = None,
) -> dict[str, Any]:
    """直接在 service 内部执行 STT，不再 `python` 调 `python`。"""

    # 每次都删除旧字幕，确保结果来自本次运行。
    preclean_output(output_ja_srt)
    output_ja_srt.parent.mkdir(parents=True, exist_ok=True)

    # 惰性导入，避免服务仅做队列查询时就加载大模型依赖。
    from faster_whisper import WhisperModel

    effective_config = build_stt_effective_config(
        model=model,
        language=language,
        timeout_sec=timeout_sec,
        max_retries=0,
        device=device,
        compute_type=compute_type,
        beam_size=beam_size,
        best_of=best_of,
        patience=patience,
        condition_on_previous_text=condition_on_previous_text,
        vad_filter=vad_filter,
        vad_threshold=vad_threshold,
        vad_min_speech_duration_ms=vad_min_speech_duration_ms,
        vad_max_speech_duration_s=vad_max_speech_duration_s,
        vad_min_silence_duration_ms=vad_min_silence_duration_ms,
        vad_speech_pad_ms=vad_speech_pad_ms,
        no_speech_threshold=no_speech_threshold,
        compression_ratio_threshold=compression_ratio_threshold,
        log_prob_threshold=log_prob_threshold,
        hallucination_silence_threshold=hallucination_silence_threshold,
        initial_prompt=initial_prompt,
        hotwords=hotwords,
    )
    resolved_device = str(effective_config["resolved_device"])
    resolved_compute = str(effective_config["resolved_compute_type"])
    model_runtime = WhisperModel(
        model,
        device=resolved_device,
        compute_type=resolved_compute,
        local_files_only=True,
    )

    media_duration = _probe_duration(input_video)
    started = time.perf_counter()
    transcribe_kwargs: dict[str, Any] = {
        "language": str(effective_config["language"]),
        "beam_size": int(effective_config["beam_size"]),
        "best_of": int(effective_config["best_of"]),
        "patience": float(effective_config["patience"]),
        "condition_on_previous_text": condition_on_previous_text,
        "vad_filter": vad_filter,
        "vad_parameters": {
            "threshold": float(effective_config["vad_threshold"]),
            "min_speech_duration_ms": int(
                effective_config["vad_min_speech_duration_ms"]
            ),
            "max_speech_duration_s": float(
                effective_config["vad_max_speech_duration_s"]
            ),
            "min_silence_duration_ms": int(
                effective_config["vad_min_silence_duration_ms"]
            ),
            "speech_pad_ms": int(effective_config["vad_speech_pad_ms"]),
        },
        "no_speech_threshold": float(effective_config["no_speech_threshold"]),
        "compression_ratio_threshold": float(
            effective_config["compression_ratio_threshold"]
        ),
        "log_prob_threshold": float(effective_config["log_prob_threshold"]),
        "hallucination_silence_threshold": float(
            effective_config["hallucination_silence_threshold"]
        ),
    }
    if str(effective_config["initial_prompt"]):
        transcribe_kwargs["initial_prompt"] = str(effective_config["initial_prompt"])
    if str(effective_config["hotwords"]):
        transcribe_kwargs["hotwords"] = str(effective_config["hotwords"])

    segments, _ = model_runtime.transcribe(
        str(input_video),
        **transcribe_kwargs,
    )

    _emit_progress(
        progress_queue,
        stage="stt",
        percent=0.0,
        message="stt_started",
        task_id=task_id,
        worker_id=worker_id,
    )

    count = 0
    last_end = 0.0
    with output_ja_srt.open("w", encoding="utf-8") as f:
        for seg_no, seg in enumerate(segments, start=1):
            text = seg.text.strip()
            if not text:
                continue
            count += 1
            last_end = float(seg.end)
            f.write(f"{count}\n")
            f.write(
                f"{_format_timestamp(seg.start)} --> {_format_timestamp(seg.end)}\n"
            )
            f.write(f"{text}\n\n")

            if (
                seg_no % max(progress_every, 1) == 0
                and media_duration
                and media_duration > 0
            ):
                _emit_progress(
                    progress_queue,
                    stage="stt",
                    percent=min(last_end / media_duration, 1.0) * 100.0,
                    message="stt_running",
                    task_id=task_id,
                    worker_id=worker_id,
                )

            if time.perf_counter() - started > timeout_sec:
                raise TimeoutError("stt_timeout")

    _emit_progress(
        progress_queue,
        stage="stt",
        percent=100.0,
        message="stt_done",
        task_id=task_id,
        worker_id=worker_id,
    )
    return effective_config


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


def _call_translate_api(
    post_func: Callable[..., requests.Response],
    *,
    base_url: str,
    api_key: str,
    model: str,
    timeout_sec: int,
    batch: list[SrtEntry],
) -> dict[int, str]:
    """调用 LLM 翻译单个批次并返回 id -> 中文文本映射。"""

    messages = _build_translate_messages(batch)
    response = post_func(
        f"{base_url.rstrip('/')}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "temperature": 0.2,
            "messages": messages,
        },
        timeout=timeout_sec,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    parsed = _extract_json_object(content)
    if not isinstance(parsed, list):
        raise ValueError("model output is not a JSON array")

    mapping: dict[int, str] = {}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        text_zh = item.get("text_zh")
        if isinstance(item_id, int) and isinstance(text_zh, str) and text_zh.strip():
            mapping[item_id] = text_zh.strip()
    expected = {e.index for e in batch}
    if set(mapping.keys()) != expected:
        missing = sorted(expected - set(mapping.keys()))
        raise ValueError(f"batch translation ids mismatch, missing={missing[:10]}")
    return mapping


def run_translate(
    input_ja_srt: Path,
    output_zh_srt: Path,
    config_path: Path,
    timeout_sec: int,
    *,
    chunk_minutes: int | None = None,
    retry: int | None = None,
    progress_queue: Queue[dict[str, Any]] | None = None,
    task_id: str | None = None,
    worker_id: str | None = None,
) -> None:
    """直接在 service 内部执行翻译，并把进度写入队列。"""

    # 先清理目标字幕与翻译进度副产物，保障可恢复可重跑。
    preclean_output(output_zh_srt)
    progress_artifact = output_zh_srt.with_suffix(
        output_zh_srt.suffix + ".progress.json"
    )
    preclean_output(progress_artifact)

    config = _load_llm_config(config_path)
    base_url = config.get(
        "llm", "base_url", fallback="https://www.right.codes/codex/v1"
    )
    api_key = config.get("llm", "api_key", fallback="").strip()
    if not api_key:
        raise RuntimeError("missing api key in config")
    model = config.get("llm", "model", fallback="gpt-5.4-mini")
    cfg_chunk_minutes = max(
        config.getint("translation", "chunk_minutes", fallback=30), 1
    )
    parallel = max(config.getint("translation", "parallel", fallback=16), 1)
    cfg_retry = max(config.getint("translation", "retry", fallback=4), 1)
    request_interval = max(
        config.getfloat("translation", "request_interval", fallback=1.0), 0.0
    )

    effective_chunk_minutes = max(int(chunk_minutes or cfg_chunk_minutes), 1)
    effective_retry = max(int(retry or cfg_retry), 1)

    entries = _parse_srt(input_ja_srt.read_text(encoding="utf-8"))
    if not entries:
        raise RuntimeError(f"no valid srt entries found: {input_ja_srt}")

    _emit_progress(
        progress_queue,
        stage="translate",
        percent=0.0,
        message="translate_started",
        task_id=task_id,
        worker_id=worker_id,
    )

    batches = _split_entries_by_time_window(
        entries, window_minutes=effective_chunk_minutes
    )
    translations: dict[int, str] = {}
    started = time.perf_counter()
    session = requests.Session()
    session.trust_env = False

    for batch_no, batch in enumerate(batches, start=1):
        last_err: Exception | None = None
        for _attempt in range(1, effective_retry + 1):
            if time.perf_counter() - started > timeout_sec:
                raise TimeoutError("translate_timeout")
            try:
                mapped = _call_translate_api(
                    session.post,
                    base_url=base_url,
                    api_key=api_key,
                    model=model,
                    timeout_sec=min(timeout_sec, 180),
                    batch=batch,
                )
                translations.update(mapped)
                progress_artifact.write_text(
                    json.dumps(
                        {str(k): v for k, v in translations.items()}, ensure_ascii=False
                    ),
                    encoding="utf-8",
                )
                _emit_progress(
                    progress_queue,
                    stage="translate",
                    percent=batch_no / max(len(batches), 1) * 100.0,
                    message="translate_running",
                    task_id=task_id,
                    worker_id=worker_id,
                )
                break
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                time.sleep(min(request_interval * 2.0, 5.0))
        else:
            raise RuntimeError(f"translate batch failed: {last_err}")

        if parallel > 0:
            # 保留请求节流行为，避免对上游接口形成瞬时冲击。
            time.sleep(request_interval)

    _dump_srt(entries, translations, output_zh_srt)
    _emit_progress(
        progress_queue,
        stage="translate",
        percent=100.0,
        message="translate_done",
        task_id=task_id,
        worker_id=worker_id,
    )
