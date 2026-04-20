"""服务装配与生命周期绑定。"""

from __future__ import annotations

from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path

from fastapi import FastAPI

from whisper_stt_service.api import create_app
from whisper_stt_service.core.config import (
    Settings,
    ensure_config_file,
    find_missing_required_fields,
    load_settings,
)
from whisper_stt_service.core.progress import ProgressStore
from whisper_stt_service.repo.database import Database
from whisper_stt_service.repo.job_repository import JobRepository
from whisper_stt_service.service.runtime import WorkerRuntime


LOGGER = logging.getLogger(__name__)


def _resolve_settings() -> tuple[Settings, Path]:
    """解析服务配置文件路径并加载配置。"""

    config_path = Path(os.getenv("WHISPER_STT_CONFIG", "config.ini")).resolve()
    default_example_path = (
        Path(__file__).resolve().parent.parent.parent / "config.example.ini"
    )
    example_path = config_path.with_name("config.example.ini")
    if not example_path.is_file():
        # 支持自定义 WHISPER_STT_CONFIG 路径时回退到仓库内默认模板。
        example_path = default_example_path
    if ensure_config_file(config_path=config_path, example_path=example_path):
        LOGGER.warning(
            "config file not found, created default from example: %s",
            config_path,
        )

    missing_fields = find_missing_required_fields(config_path)
    if missing_fields:
        detail = ", ".join(
            f"{section}.{option}"
            for section, options in missing_fields.items()
            for option in options
        )
        LOGGER.error("missing required config entries: %s", detail)

    settings = load_settings(config_path)
    return settings, config_path


def _resolve_runtime_path(base_path: Path, value: Path) -> Path:
    """把相对路径解释为基于配置文件目录的路径。"""

    if value.is_absolute():
        return value
    return (base_path.parent / value).resolve()


def _build_stage_effective_defaults(
    settings: Settings, config_path: Path
) -> dict[str, dict]:
    """集中构造各阶段默认配置，便于后续扩展更多阶段。"""

    return {
        "extract": {},
        "stt": {
            "device": settings.stt.device,
            "compute_type": settings.stt.compute_type,
            "batch_size": settings.stt.batch_size,
            "beam_size": settings.stt.beam_size,
            "best_of": settings.stt.best_of,
            "patience": settings.stt.patience,
            "condition_on_previous_text": settings.stt.condition_on_previous_text,
            "vad_filter": settings.stt.vad_filter,
            "vad_threshold": settings.stt.vad_threshold,
            "vad_min_speech_duration_ms": settings.stt.vad_min_speech_duration_ms,
            "vad_max_speech_duration_s": settings.stt.vad_max_speech_duration_s,
            "vad_min_silence_duration_ms": settings.stt.vad_min_silence_duration_ms,
            "vad_speech_pad_ms": settings.stt.vad_speech_pad_ms,
            "no_speech_threshold": settings.stt.no_speech_threshold,
            "compression_ratio_threshold": settings.stt.compression_ratio_threshold,
            "log_prob_threshold": settings.stt.log_prob_threshold,
            "hallucination_silence_threshold": settings.stt.hallucination_silence_threshold,
            "initial_prompt": settings.stt.initial_prompt,
            "hotwords": settings.stt.hotwords,
        },
        "stt_whisperx": {
            "model": str(
                _resolve_runtime_path(config_path, Path(settings.stt_whisperx.model))
            ),
            "device": settings.stt_whisperx.device,
            "compute_type": settings.stt_whisperx.compute_type,
            "batch_size": settings.stt_whisperx.batch_size,
            "vad_config_path": str(
                _resolve_runtime_path(
                    config_path, settings.stt_whisperx.vad_config_path
                )
            ),
            "align_model_root": str(
                _resolve_runtime_path(
                    config_path, settings.stt_whisperx.align_model_root
                )
            ),
            "align_enabled": settings.stt_whisperx.align_enabled,
            "vad_backend": settings.stt_whisperx.vad_backend,
            "vad_onset": settings.stt_whisperx.vad_onset,
            "vad_offset": settings.stt_whisperx.vad_offset,
            "local_files_only": settings.stt_whisperx.local_files_only,
        },
        "translate": {
            "chunk_minutes": 30,
            "retry": 4,
            "copy_back": "__video_dir__",
        },
    }


def build_app() -> FastAPI:
    """创建完整应用并绑定生命周期。"""

    settings, config_path = _resolve_settings()
    db_path = _resolve_runtime_path(config_path, settings.runtime.db_path)
    log_root = _resolve_runtime_path(config_path, settings.runtime.log_root)
    artifact_root = _resolve_runtime_path(config_path, settings.runtime.artifact_root)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    log_root.mkdir(parents=True, exist_ok=True)
    artifact_root.mkdir(parents=True, exist_ok=True)

    db = Database(db_path)
    db.init_schema()
    repo = JobRepository(
        db,
        stage_max_retries={
            "extract": settings.retry.extract_max_retries,
            "stt": settings.retry.stt_max_retries,
            "stt_whisperx": settings.retry.stt_whisperx_max_retries,
            "translate": settings.retry.translate_max_retries,
        },
        stage_timeouts={
            "extract": settings.timeouts.extract_timeout_sec,
            "stt": settings.timeouts.stt_timeout_sec,
            "stt_whisperx": settings.timeouts.stt_whisperx_timeout_sec,
            "translate": settings.timeouts.translate_timeout_sec,
        },
        stage_effective_defaults=_build_stage_effective_defaults(settings, config_path),
        log_root=log_root,
        artifact_root=artifact_root,
    )
    progress_store = ProgressStore(settings.runtime.progress_ttl_sec)
    model_path_cfg = _resolve_runtime_path(config_path, settings.runtime.model_path)
    model_path = os.getenv("WHISPER_STT_MODEL", str(model_path_cfg))
    runtime = WorkerRuntime(
        repo=repo,
        progress_store=progress_store,
        settings=settings,
        config_path=config_path,
        model_path=model_path,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        runtime.start()
        try:
            yield
        finally:
            runtime.stop()

    app = create_app(
        repo=repo,
        progress_store=progress_store,
        runtime=runtime,
        api_token=settings.security.api_token,
    )
    app.router.lifespan_context = lifespan
    return app
