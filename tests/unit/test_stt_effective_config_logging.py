"""验证 STT 生效参数快照与日志扩展字段。"""

from __future__ import annotations

from pathlib import Path

from whisper_stt_service.config import (
    RetrySettings,
    RuntimeSettings,
    Settings,
    SttSettings,
    TimeoutSettings,
    WorkerSettings,
)
from whisper_stt_service.executors import build_stt_effective_config
from whisper_stt_service.repository import TaskExecutionContext
from whisper_stt_service.workers import WorkerRuntime


def _fake_settings() -> Settings:
    """构造最小可用配置对象，供纯逻辑测试复用。"""

    return Settings(
        workers=WorkerSettings(
            extract_workers=1,
            stt_workers=1,
            translate_workers=1,
            scheduler_interval_sec=60,
            poll_interval_sec=1,
        ),
        timeouts=TimeoutSettings(
            extract_timeout_sec=120,
            stt_timeout_sec=3600,
            translate_timeout_sec=3600,
            lease_timeout_sec=60,
        ),
        retry=RetrySettings(
            extract_max_retries=2,
            stt_max_retries=2,
            translate_max_retries=2,
        ),
        runtime=RuntimeSettings(
            db_path=Path("/tmp/test.db"),
            progress_ttl_sec=300,
            log_root=Path("/tmp/logs"),
            model_path=Path("/tmp/model"),
        ),
        stt=SttSettings(
            device="auto",
            compute_type="auto",
            beam_size=3,
            best_of=3,
            patience=1.0,
            condition_on_previous_text=False,
            vad_filter=True,
            vad_threshold=0.45,
            vad_min_speech_duration_ms=200,
            vad_max_speech_duration_s=18.0,
            vad_min_silence_duration_ms=700,
            vad_speech_pad_ms=300,
            no_speech_threshold=0.6,
            compression_ratio_threshold=2.2,
            log_prob_threshold=-1.0,
            hallucination_silence_threshold=1.5,
            initial_prompt="",
            hotwords="",
        ),
    )


def test_build_stt_effective_config_contains_resolved_runtime(monkeypatch) -> None:
    """生效参数应包含 resolved_device/resolved_compute_type。"""

    monkeypatch.setattr(
        "whisper_stt_service.executor.stt._resolve_runtime",
        lambda device, compute: ("cuda", "float16"),
    )

    payload = build_stt_effective_config(
        model="/abs/model",
        language="ja",
        timeout_sec=7200,
        max_retries=2,
        device="auto",
        compute_type="auto",
        beam_size=3,
        best_of=3,
        patience=1.0,
        condition_on_previous_text=False,
        vad_filter=True,
        vad_threshold=0.45,
        vad_min_speech_duration_ms=200,
        vad_max_speech_duration_s=18.0,
        vad_min_silence_duration_ms=700,
        vad_speech_pad_ms=300,
        no_speech_threshold=0.6,
        compression_ratio_threshold=2.2,
        log_prob_threshold=-1.0,
        hallucination_silence_threshold=1.5,
        initial_prompt="",
        hotwords="",
    )

    assert payload["device"] == "auto"
    assert payload["compute_type"] == "auto"
    assert payload["resolved_device"] == "cuda"
    assert payload["resolved_compute_type"] == "float16"
    assert payload["beam_size"] == 3


def test_worker_task_started_extra_contains_stt_effective_config(monkeypatch) -> None:
    """worker 的 task_started 扩展字段应携带 STT 生效参数。"""

    runtime = WorkerRuntime(
        repo=None,  # type: ignore[arg-type]
        progress_store=None,  # type: ignore[arg-type]
        settings=_fake_settings(),
        config_path=Path("/tmp/config.ini"),
        model_path="/abs/model",
    )
    monkeypatch.setattr(
        "whisper_stt_service.service.runtime.build_stt_effective_config",
        lambda **kwargs: {
            "device": kwargs["device"],
            "compute_type": kwargs["compute_type"],
            "resolved_device": "cuda",
            "resolved_compute_type": "float16",
        },
    )

    ctx = TaskExecutionContext(
        task_id="t1",
        job_id="j1",
        stage="stt",
        video_path="/tmp/a.mp4",
        source_language="ja",
        output_ja_path="/tmp/a.ja.srt",
        output_zh_path="/tmp/a.zh.srt",
        timeout_sec=7200,
        attempt=1,
        max_retries=2,
        log_dir="/tmp/logs/j1/stt/t1",
        log_file="/tmp/logs/j1/stt/t1/task.log",
        task_config={"stage": "stt", "effective_config": {}},
    )

    extra = runtime._build_task_started_extra(ctx=ctx, stage="stt")
    assert extra["attempt"] == 1
    assert extra["max_retries"] == 2
    assert "effective_config" in extra
    assert extra["effective_config"]["resolved_device"] == "cuda"
