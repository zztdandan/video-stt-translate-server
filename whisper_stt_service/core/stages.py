"""stage 注册表与配置键定义。"""

from __future__ import annotations

SUPPORTED_STAGES = ("extract", "stt", "stt_whisperx", "translate")

# 每个阶段允许覆盖的配置键。
STAGE_CONFIG_KEYS: dict[str, tuple[str, ...]] = {
    "extract": ("timeout_sec", "max_retries"),
    "stt": (
        "timeout_sec",
        "max_retries",
        "device",
        "compute_type",
        "batch_size",
        "beam_size",
        "best_of",
        "patience",
        "condition_on_previous_text",
        "vad_filter",
        "vad_threshold",
        "vad_min_speech_duration_ms",
        "vad_max_speech_duration_s",
        "vad_min_silence_duration_ms",
        "vad_speech_pad_ms",
        "no_speech_threshold",
        "compression_ratio_threshold",
        "log_prob_threshold",
        "hallucination_silence_threshold",
        "initial_prompt",
        "hotwords",
    ),
    "stt_whisperx": (
        "timeout_sec",
        "max_retries",
        "model",
        "device",
        "compute_type",
        "batch_size",
        "vad_config_path",
        "align_model_root",
        "align_enabled",
        "vad_backend",
        "vad_onset",
        "vad_offset",
        "local_files_only",
    ),
    "translate": (
        "timeout_sec",
        "max_retries",
        "chunk_minutes",
        "retry",
        "copy_back",
    ),
}
