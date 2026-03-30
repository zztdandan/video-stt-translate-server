"""流水线服务配置加载模块。

该模块把 config.ini 转换成强类型 dataclass，避免业务层直接使用字符串键。
"""

from __future__ import annotations

import configparser
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorkerSettings:
    """Worker 池与调度轮询配置。"""

    extract_workers: int
    stt_workers: int
    translate_workers: int
    scheduler_interval_sec: int
    poll_interval_sec: int


@dataclass(frozen=True)
class TimeoutSettings:
    """各阶段执行超时与领取租约超时配置。"""

    extract_timeout_sec: int
    stt_timeout_sec: int
    translate_timeout_sec: int
    lease_timeout_sec: int


@dataclass(frozen=True)
class RetrySettings:
    """各阶段最大重试次数配置。"""

    extract_max_retries: int
    stt_max_retries: int
    translate_max_retries: int


@dataclass(frozen=True)
class RuntimeSettings:
    """运行期路径与内存进度保留策略配置。"""

    db_path: Path
    progress_ttl_sec: int
    log_root: Path
    model_path: Path


@dataclass(frozen=True)
class Settings:
    """服务总配置对象（不可变）。"""

    workers: WorkerSettings
    timeouts: TimeoutSettings
    retry: RetrySettings
    runtime: RuntimeSettings


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
        translate_workers=cp.getint("workers", "translate_workers"),
        scheduler_interval_sec=cp.getint(
            "workers", "scheduler_interval_sec", fallback=180
        ),
        poll_interval_sec=cp.getint("workers", "poll_interval_sec", fallback=1),
    )
    timeouts = TimeoutSettings(
        extract_timeout_sec=cp.getint("timeouts", "extract_timeout_sec"),
        stt_timeout_sec=cp.getint("timeouts", "stt_timeout_sec"),
        translate_timeout_sec=cp.getint("timeouts", "translate_timeout_sec"),
        lease_timeout_sec=cp.getint("timeouts", "lease_timeout_sec"),
    )
    retry = RetrySettings(
        extract_max_retries=cp.getint("retry", "extract_max_retries"),
        stt_max_retries=cp.getint("retry", "stt_max_retries"),
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
    return Settings(workers=workers, timeouts=timeouts, retry=retry, runtime=runtime)
