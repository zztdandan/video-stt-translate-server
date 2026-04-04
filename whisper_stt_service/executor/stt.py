"""stt 阶段执行器。"""

from __future__ import annotations

from pathlib import Path
from queue import Queue
from typing import Any
import time

from whisper_stt_service.executor.common import _emit_progress, _probe_duration, preclean_output


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
