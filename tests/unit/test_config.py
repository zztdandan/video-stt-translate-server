"""配置加载单元测试。"""

from pathlib import Path

from whisper_stt_service.config import load_settings


def test_load_settings_reads_worker_counts(tmp_path: Path) -> None:
    """验证配置文件可正确解析 worker 数量与运行时配置。"""

    # 构造最小可用 config.ini，覆盖 workers/timeouts/retry/runtime 四个分组。
    cfg = tmp_path / "config.ini"
    cfg.write_text(
        """
[workers]
extract_workers = 2
stt_workers = 3
translate_workers = 4
scheduler_interval_sec = 180
poll_interval_sec = 1

[timeouts]
extract_timeout_sec = 1200
stt_timeout_sec = 7200
translate_timeout_sec = 7200
lease_timeout_sec = 600

[retry]
extract_max_retries = 2
stt_max_retries = 2
translate_max_retries = 2

[runtime]
db_path = /tmp/test.db
progress_ttl_sec = 3600
log_root = /tmp/logs
""".strip(),
        encoding="utf-8",
    )

    # 执行加载并断言关键字段，确保强类型映射正确。
    settings = load_settings(cfg)
    assert settings.workers.extract_workers == 2
    assert settings.workers.stt_workers == 3
    assert settings.runtime.progress_ttl_sec == 3600
