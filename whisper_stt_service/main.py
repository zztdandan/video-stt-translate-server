"""服务入口：装配 DB/Repository/Workers，并暴露 ASGI app。"""

from __future__ import annotations

from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path

from fastapi import FastAPI

from whisper_stt_service.api import create_app
from whisper_stt_service.config import (
    Settings,
    ensure_config_file,
    find_missing_required_fields,
    load_settings,
)
from whisper_stt_service.db import Database
from whisper_stt_service.progress import ProgressStore
from whisper_stt_service.repository import JobRepository
from whisper_stt_service.workers import WorkerRuntime


LOGGER = logging.getLogger(__name__)


def _resolve_settings() -> tuple[Settings, Path]:
    """解析服务配置文件路径并加载配置。"""

    config_path = Path(os.getenv("WHISPER_STT_CONFIG", "config.ini")).resolve()
    default_example_path = Path(__file__).resolve().parent.parent / "config.example.ini"
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


def build_app() -> FastAPI:
    """创建完整应用并绑定生命周期。"""

    settings, config_path = _resolve_settings()
    db_path = _resolve_runtime_path(config_path, settings.runtime.db_path)
    log_root = _resolve_runtime_path(config_path, settings.runtime.log_root)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    log_root.mkdir(parents=True, exist_ok=True)

    db = Database(db_path)
    db.init_schema()
    repo = JobRepository(
        db,
        stage_max_retries={
            "extract": settings.retry.extract_max_retries,
            "stt": settings.retry.stt_max_retries,
            "translate": settings.retry.translate_max_retries,
        },
        stage_timeouts={
            "extract": settings.timeouts.extract_timeout_sec,
            "stt": settings.timeouts.stt_timeout_sec,
            "translate": settings.timeouts.translate_timeout_sec,
        },
        log_root=log_root,
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

    app = create_app(repo=repo, progress_store=progress_store, runtime=runtime)
    app.router.lifespan_context = lifespan
    return app


# 在模块导入时创建 app，确保 Uvicorn 能直接发现并加载应用实例。
app = build_app()
