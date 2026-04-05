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
stt_whisperx_workers = 2
translate_workers = 4
scheduler_interval_sec = 180
poll_interval_sec = 1

[timeouts]
extract_timeout_sec = 1200
stt_timeout_sec = 7200
stt_whisperx_timeout_sec = 7000
translate_timeout_sec = 7200
lease_timeout_sec = 600

[retry]
extract_max_retries = 2
stt_max_retries = 2
stt_whisperx_max_retries = 1
translate_max_retries = 2

[runtime]
db_path = /tmp/test.db
progress_ttl_sec = 3600
log_root = /tmp/logs
model_path = /tmp/model-dir

[stt]
batch_size = 8
beam_size = 6
vad_threshold = 0.4
condition_on_previous_text = false
initial_prompt = 
hotwords = 東京タワー, 田中

[stt_whisperx]
model = /tmp/whisperx-model
batch_size = 16
vad_config_path = /tmp/vad/config.yaml
align_model_root = /tmp/align
local_files_only = true
""".strip(),
        encoding="utf-8",
    )

    # 执行加载并断言关键字段，确保强类型映射正确。
    settings = load_settings(cfg)
    assert settings.workers.extract_workers == 2
    assert settings.workers.stt_workers == 3
    assert settings.workers.stt_whisperx_workers == 2
    assert settings.runtime.progress_ttl_sec == 3600
    assert str(settings.runtime.model_path) == "/tmp/model-dir"
    assert settings.stt.batch_size == 8
    assert settings.stt.beam_size == 6
    assert settings.stt.vad_threshold == 0.4
    assert settings.stt.condition_on_previous_text is False
    assert settings.stt.initial_prompt == ""
    assert settings.stt.hotwords == "東京タワー, 田中"
    assert settings.stt_whisperx.model == "/tmp/whisperx-model"
    assert settings.stt_whisperx.batch_size == 16
    assert str(settings.stt_whisperx.vad_config_path) == "/tmp/vad/config.yaml"
