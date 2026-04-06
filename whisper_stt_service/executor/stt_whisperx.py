"""stt_whisperx 阶段执行器。"""

from __future__ import annotations

from pathlib import Path
from queue import Queue
from typing import Any
import importlib
import time

from whisper_stt_service.executor.common import (
    _emit_progress,
    _probe_duration,
    preclean_output,
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


def _resolve_align_model_path(align_model_root: str, language: str) -> Path:
    """按语言解析 alignment 模型目录，优先 `<root>/<language>`。"""

    root = Path(align_model_root)
    language_dir = root / language
    if language_dir.exists():
        return language_dir
    return root


def _resolve_vad_model_file(vad_config_path: Path) -> Path:
    """解析本地 VAD config，并提取 segmentation 的 pytorch_model.bin 路径。"""

    import yaml

    raw = yaml.safe_load(vad_config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError(f"invalid_vad_config_yaml: {vad_config_path}")

    pipeline = raw.get("pipeline")
    if not isinstance(pipeline, dict):
        raise RuntimeError(f"invalid_vad_config_pipeline: {vad_config_path}")
    params = pipeline.get("params")
    if not isinstance(params, dict):
        raise RuntimeError(f"invalid_vad_config_params: {vad_config_path}")

    segmentation = params.get("segmentation")
    if not isinstance(segmentation, str) or not segmentation.strip():
        raise RuntimeError(f"missing_vad_segmentation_path: {vad_config_path}")

    seg_path = Path(segmentation.strip())
    if not seg_path.is_absolute():
        seg_path = (vad_config_path.parent / seg_path).resolve()

    if seg_path.is_dir():
        model_fp = seg_path / "pytorch_model.bin"
    else:
        model_fp = seg_path

    if not model_fp.is_file():
        raise FileNotFoundError(f"vad_segmentation_model_not_found: {model_fp}")
    return model_fp


def build_stt_whisperx_effective_config(
    *,
    model: str,
    language: str,
    timeout_sec: int,
    max_retries: int,
    device: str,
    compute_type: str,
    batch_size: int,
    vad_config_path: str,
    align_model_root: str,
    align_enabled: bool,
    vad_backend: str,
    vad_onset: float,
    vad_offset: float,
    local_files_only: bool,
) -> dict[str, Any]:
    """构造 stt_whisperx 生效参数快照，便于日志审计与问题排查。"""

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
        "batch_size": max(int(batch_size), 1),
        "vad_config_path": vad_config_path,
        "align_model_root": align_model_root,
        "align_enabled": bool(align_enabled),
        "vad_backend": vad_backend,
        "vad_onset": min(max(float(vad_onset), 0.01), 0.99),
        "vad_offset": min(max(float(vad_offset), 0.01), 0.99),
        "local_files_only": bool(local_files_only),
    }


def run_stt_whisperx(
    input_video: Path,
    output_ja_srt: Path,
    language: str,
    timeout_sec: int,
    *,
    model: str,
    device: str,
    compute_type: str,
    batch_size: int,
    vad_config_path: str,
    align_model_root: str,
    align_enabled: bool,
    vad_backend: str,
    vad_onset: float,
    vad_offset: float,
    local_files_only: bool,
    progress_every: int = 25,
    progress_queue: Queue[dict[str, Any]] | None = None,
    task_id: str | None = None,
    worker_id: str | None = None,
) -> dict[str, Any]:
    """在 service 内部执行 WhisperX 路径（VAD 切段 + batched ASR + 可选对齐）。"""

    preclean_output(output_ja_srt)
    output_ja_srt.parent.mkdir(parents=True, exist_ok=True)

    effective_config = build_stt_whisperx_effective_config(
        model=model,
        language=language,
        timeout_sec=timeout_sec,
        max_retries=0,
        device=device,
        compute_type=compute_type,
        batch_size=batch_size,
        vad_config_path=vad_config_path,
        align_model_root=align_model_root,
        align_enabled=align_enabled,
        vad_backend=vad_backend,
        vad_onset=vad_onset,
        vad_offset=vad_offset,
        local_files_only=local_files_only,
    )

    model_path = Path(str(effective_config["model"]))
    if not model_path.exists():
        raise FileNotFoundError(f"whisper_model_not_found: {model_path}")

    vad_cfg = Path(str(effective_config["vad_config_path"]))
    if not vad_cfg.is_file():
        raise FileNotFoundError(f"vad_config_not_found: {vad_cfg}")
    vad_model_fp = _resolve_vad_model_file(vad_cfg)

    align_model_path = _resolve_align_model_path(
        str(effective_config["align_model_root"]),
        str(effective_config["language"]),
    )
    if bool(effective_config["align_enabled"]) and not align_model_path.exists():
        raise FileNotFoundError(f"align_model_not_found: {align_model_path}")

    if not bool(effective_config["local_files_only"]):
        raise RuntimeError("stt_whisperx_requires_local_files_only")

    whisperx = importlib.import_module("whisperx")

    vad_method = str(effective_config["vad_backend"]).strip().lower()
    vad_options = {
        "vad_onset": float(effective_config["vad_onset"]),
        "vad_offset": float(effective_config["vad_offset"]),
    }
    vad_model = None
    if vad_method == "pyannote":
        pyannote_mod = importlib.import_module("whisperx.vads.pyannote")
        vad_model = pyannote_mod.Pyannote(
            device=str(effective_config["resolved_device"]),
            token=None,
            model_fp=str(vad_model_fp),
            vad_onset=vad_options["vad_onset"],
            vad_offset=vad_options["vad_offset"],
        )

    asr = whisperx.load_model(
        str(model_path),
        device=str(effective_config["resolved_device"]),
        compute_type=str(effective_config["resolved_compute_type"]),
        language=str(effective_config["language"]),
        vad_model=vad_model,
        vad_method=vad_method,
        asr_options={
            # 兼容新版本 faster-whisper 的 TranscriptionOptions 必填字段。
            "max_new_tokens": None,
            "clip_timestamps": "0",
            "hallucination_silence_threshold": None,
            "hotwords": None,
        },
        vad_options=vad_options,
    )

    media_duration = _probe_duration(input_video)
    started = time.perf_counter()
    _emit_progress(
        progress_queue,
        stage="stt_whisperx",
        percent=0.0,
        message="stt_whisperx_started",
        task_id=task_id,
        worker_id=worker_id,
    )

    audio = whisperx.load_audio(str(input_video))
    result = asr.transcribe(
        audio,
        batch_size=int(effective_config["batch_size"]),
        language=str(effective_config["language"]),
    )

    segments = result.get("segments", [])
    if bool(effective_config["align_enabled"]):
        align_model, align_meta = whisperx.load_align_model(
            language_code=str(effective_config["language"]),
            device=str(effective_config["resolved_device"]),
            model_dir=str(align_model_path),
        )
        try:
            aligned = whisperx.align(
                segments,
                align_model,
                align_meta,
                audio,
                str(effective_config["resolved_device"]),
                return_char_alignments=False,
            )
        except AttributeError as exc:
            # transformers 新版本下 processor.sampling_rate 可能不存在。
            # 这里不再走 preprocess=False 绕过路径，避免静默改变对齐行为。
            if "sampling_rate" not in str(exc):
                raise
            raise RuntimeError(
                "whisperx_alignment_api_mismatch: please use official whisperx release and compatible transformers version"
            ) from exc
        segments = aligned.get("segments", [])

    count = 0
    last_end = 0.0
    with output_ja_srt.open("w", encoding="utf-8") as f:
        for seg_no, seg in enumerate(segments, start=1):
            text = str(seg.get("text", "")).strip()
            if not text:
                continue
            start_sec = float(seg.get("start", 0.0))
            end_sec = float(seg.get("end", start_sec))
            count += 1
            last_end = end_sec
            f.write(f"{count}\n")
            f.write(
                f"{_format_timestamp(start_sec)} --> {_format_timestamp(end_sec)}\n"
            )
            f.write(f"{text}\n\n")

            if (
                seg_no % max(progress_every, 1) == 0
                and media_duration
                and media_duration > 0
            ):
                _emit_progress(
                    progress_queue,
                    stage="stt_whisperx",
                    percent=min(last_end / media_duration, 1.0) * 100.0,
                    message="stt_whisperx_running",
                    task_id=task_id,
                    worker_id=worker_id,
                )

            if time.perf_counter() - started > timeout_sec:
                raise TimeoutError("stt_whisperx_timeout")

    if count == 0:
        raise RuntimeError("stt_whisperx_empty_srt")

    _emit_progress(
        progress_queue,
        stage="stt_whisperx",
        percent=100.0,
        message="stt_whisperx_done",
        task_id=task_id,
        worker_id=worker_id,
    )
    return effective_config
