"""流水线服务配置加载模块。

该模块把 config.ini 转换成强类型 dataclass，避免业务层直接使用字符串键。
"""

from __future__ import annotations

import configparser
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorkerSettings:
    """Worker 池与调度轮询配置。"""

    extract_workers: int
    stt_workers: int
    stt_whisperx_workers: int
    translate_workers: int
    scheduler_interval_sec: int
    poll_interval_sec: int


@dataclass(frozen=True)
class TimeoutSettings:
    """各阶段执行超时与领取租约超时配置。"""

    extract_timeout_sec: int
    stt_timeout_sec: int
    stt_whisperx_timeout_sec: int
    translate_timeout_sec: int
    lease_timeout_sec: int


@dataclass(frozen=True)
class RetrySettings:
    """各阶段最大重试次数配置。"""

    extract_max_retries: int
    stt_max_retries: int
    stt_whisperx_max_retries: int
    translate_max_retries: int


@dataclass(frozen=True)
class RuntimeSettings:
    """运行期路径与内存进度保留策略配置。"""

    db_path: Path
    progress_ttl_sec: int
    log_root: Path
    model_path: Path


@dataclass(frozen=True)
class SttSettings:
    """STT 阶段转写参数配置。"""

    device: str
    compute_type: str
    batch_size: int
    beam_size: int
    best_of: int
    patience: float
    condition_on_previous_text: bool
    vad_filter: bool
    vad_threshold: float
    vad_min_speech_duration_ms: int
    vad_max_speech_duration_s: float
    vad_min_silence_duration_ms: int
    vad_speech_pad_ms: int
    no_speech_threshold: float
    compression_ratio_threshold: float
    log_prob_threshold: float
    hallucination_silence_threshold: float
    initial_prompt: str
    hotwords: str


@dataclass(frozen=True)
class SttWhisperxSettings:
    """WhisperX STT 阶段转写参数配置。"""

    model: str
    device: str
    compute_type: str
    batch_size: int
    vad_config_path: Path
    align_model_root: Path
    align_enabled: bool
    vad_backend: str
    vad_onset: float
    vad_offset: float
    local_files_only: bool


@dataclass(frozen=True)
class Settings:
    """服务总配置对象（不可变）。"""

    workers: WorkerSettings
    timeouts: TimeoutSettings
    retry: RetrySettings
    runtime: RuntimeSettings
    stt: SttSettings
    stt_whisperx: SttWhisperxSettings


# 启动前必须存在的配置项（按 section/option 列举）。
REQUIRED_CONFIG_OPTIONS: dict[str, tuple[str, ...]] = {
    "workers": (
        "extract_workers",
        "stt_workers",
        "stt_whisperx_workers",
        "translate_workers",
    ),
    "timeouts": (
        "extract_timeout_sec",
        "stt_timeout_sec",
        "stt_whisperx_timeout_sec",
        "translate_timeout_sec",
        "lease_timeout_sec",
    ),
    "retry": (
        "extract_max_retries",
        "stt_max_retries",
        "stt_whisperx_max_retries",
        "translate_max_retries",
    ),
    "runtime": (
        "db_path",
        "log_root",
        "model_path",
    ),
    "llm": (
        "base_url",
        "api_key",
        "model",
    ),
}


def ensure_config_file(config_path: Path, example_path: Path) -> bool:
    """当目标配置不存在时，按 example 自动创建并返回 True。"""

    if config_path.exists():
        return False
    if not example_path.is_file():
        raise FileNotFoundError(f"config example not found: {example_path}")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(example_path, config_path)
    return True


def find_missing_required_fields(config_path: Path) -> dict[str, list[str]]:
    """检测配置文件缺失的 section/option，供启动日志输出。"""

    cp = configparser.ConfigParser()
    cp.read(config_path, encoding="utf-8")
    missing: dict[str, list[str]] = {}
    for section, options in REQUIRED_CONFIG_OPTIONS.items():
        if not cp.has_section(section):
            missing[section] = list(options)
            continue
        missing_options = [
            option for option in options if not cp.has_option(section, option)
        ]
        if missing_options:
            missing[section] = missing_options
    return missing


def load_settings(config_path: Path) -> Settings:
    """读取 config.ini 并返回强类型配置。

    对必填字段采用失败即报错策略，避免服务在配置不完整时继续启动。
    """

    # 先读取原始配置，再按 section 映射为明确的数据结构。
    cp = configparser.ConfigParser()
    cp.read(config_path, encoding="utf-8")
    workers = WorkerSettings(
        extract_workers=cp.getint("workers", "extract_workers"),
        stt_workers=cp.getint("workers", "stt_workers"),
        stt_whisperx_workers=cp.getint(
            "workers",
            "stt_whisperx_workers",
            fallback=cp.getint("workers", "stt_workers"),
        ),
        translate_workers=cp.getint("workers", "translate_workers"),
        scheduler_interval_sec=cp.getint(
            "workers", "scheduler_interval_sec", fallback=180
        ),
        poll_interval_sec=cp.getint("workers", "poll_interval_sec", fallback=1),
    )
    timeouts = TimeoutSettings(
        extract_timeout_sec=cp.getint("timeouts", "extract_timeout_sec"),
        stt_timeout_sec=cp.getint("timeouts", "stt_timeout_sec"),
        stt_whisperx_timeout_sec=cp.getint(
            "timeouts",
            "stt_whisperx_timeout_sec",
            fallback=cp.getint("timeouts", "stt_timeout_sec"),
        ),
        translate_timeout_sec=cp.getint("timeouts", "translate_timeout_sec"),
        lease_timeout_sec=cp.getint("timeouts", "lease_timeout_sec"),
    )
    retry = RetrySettings(
        extract_max_retries=cp.getint("retry", "extract_max_retries"),
        stt_max_retries=cp.getint("retry", "stt_max_retries"),
        stt_whisperx_max_retries=cp.getint(
            "retry",
            "stt_whisperx_max_retries",
            fallback=cp.getint("retry", "stt_max_retries"),
        ),
        translate_max_retries=cp.getint("retry", "translate_max_retries"),
    )
    runtime = RuntimeSettings(
        db_path=Path(cp.get("runtime", "db_path")),
        progress_ttl_sec=cp.getint("runtime", "progress_ttl_sec", fallback=3600),
        log_root=Path(cp.get("runtime", "log_root")),
        model_path=Path(
            cp.get("runtime", "model_path", fallback="models/faster-whisper-small")
        ),
    )
    stt = SttSettings(
        device=cp.get("stt", "device", fallback="auto"),
        compute_type=cp.get("stt", "compute_type", fallback="auto"),
        batch_size=max(cp.getint("stt", "batch_size", fallback=8), 1),
        beam_size=max(cp.getint("stt", "beam_size", fallback=5), 1),
        best_of=max(cp.getint("stt", "best_of", fallback=5), 1),
        patience=max(cp.getfloat("stt", "patience", fallback=1.0), 0.1),
        condition_on_previous_text=cp.getboolean(
            "stt", "condition_on_previous_text", fallback=False
        ),
        vad_filter=cp.getboolean("stt", "vad_filter", fallback=True),
        vad_threshold=min(
            max(cp.getfloat("stt", "vad_threshold", fallback=0.45), 0.01), 0.99
        ),
        vad_min_speech_duration_ms=max(
            cp.getint("stt", "vad_min_speech_duration_ms", fallback=200), 50
        ),
        vad_max_speech_duration_s=max(
            cp.getfloat("stt", "vad_max_speech_duration_s", fallback=18.0), 1.0
        ),
        vad_min_silence_duration_ms=max(
            cp.getint("stt", "vad_min_silence_duration_ms", fallback=700), 50
        ),
        vad_speech_pad_ms=max(cp.getint("stt", "vad_speech_pad_ms", fallback=300), 0),
        no_speech_threshold=min(
            max(cp.getfloat("stt", "no_speech_threshold", fallback=0.6), 0.01), 0.99
        ),
        compression_ratio_threshold=max(
            cp.getfloat("stt", "compression_ratio_threshold", fallback=2.2), 0.1
        ),
        log_prob_threshold=cp.getfloat("stt", "log_prob_threshold", fallback=-1.0),
        hallucination_silence_threshold=max(
            cp.getfloat("stt", "hallucination_silence_threshold", fallback=1.5), 0.0
        ),
        initial_prompt=cp.get("stt", "initial_prompt", fallback="").strip(),
        hotwords=cp.get("stt", "hotwords", fallback="").strip(),
    )
    stt_whisperx = SttWhisperxSettings(
        model=cp.get("stt_whisperx", "model", fallback=str(runtime.model_path)).strip(),
        device=cp.get("stt_whisperx", "device", fallback=stt.device),
        compute_type=cp.get("stt_whisperx", "compute_type", fallback=stt.compute_type),
        batch_size=max(
            cp.getint("stt_whisperx", "batch_size", fallback=stt.batch_size), 1
        ),
        vad_config_path=Path(
            cp.get(
                "stt_whisperx",
                "vad_config_path",
                fallback="models/whisperx/vad/pyannote/config.yaml",
            )
        ),
        align_model_root=Path(
            cp.get(
                "stt_whisperx",
                "align_model_root",
                fallback="models/whisperx/align",
            )
        ),
        align_enabled=cp.getboolean("stt_whisperx", "align_enabled", fallback=True),
        vad_backend=cp.get("stt_whisperx", "vad_backend", fallback="pyannote").strip(),
        vad_onset=min(
            max(cp.getfloat("stt_whisperx", "vad_onset", fallback=0.35), 0.01), 0.99
        ),
        vad_offset=min(
            max(cp.getfloat("stt_whisperx", "vad_offset", fallback=0.2), 0.01), 0.99
        ),
        local_files_only=cp.getboolean(
            "stt_whisperx", "local_files_only", fallback=True
        ),
    )
    return Settings(
        workers=workers,
        timeouts=timeouts,
        retry=retry,
        runtime=runtime,
        stt=stt,
        stt_whisperx=stt_whisperx,
    )
