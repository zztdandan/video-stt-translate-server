"""stage 注册表与配置键定义。"""

from __future__ import annotations

SUPPORTED_STAGES = ("extract", "stt", "translate")

# 每个阶段允许覆盖的配置键。
STAGE_CONFIG_KEYS: dict[str, tuple[str, ...]] = {
    "extract": ("timeout_sec", "max_retries"),
    "stt": (
        "timeout_sec",
        "max_retries",
        "device",
        "compute_type",
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
    "translate": ("timeout_sec", "max_retries", "chunk_minutes", "retry"),
}
