from __future__ import annotations

from pathlib import Path

from whisper_stt_service.config import load_settings


def bootstrap(config_path: Path) -> None:
    _ = load_settings(config_path)
