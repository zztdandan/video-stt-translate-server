"""验证 STT 生效参数快照与日志扩展字段。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys

from whisper_stt_service.config import (
    RetrySettings,
    RuntimeSettings,
    SecuritySettings,
    Settings,
    SttSettings,
    SttWhisperxSettings,
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
            stt_whisperx_workers=1,
            translate_workers=1,
            scheduler_interval_sec=60,
            poll_interval_sec=1,
        ),
        timeouts=TimeoutSettings(
            extract_timeout_sec=120,
            stt_timeout_sec=3600,
            stt_whisperx_timeout_sec=3600,
            translate_timeout_sec=3600,
            lease_timeout_sec=60,
        ),
        retry=RetrySettings(
            extract_max_retries=2,
            stt_max_retries=2,
            stt_whisperx_max_retries=2,
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
            batch_size=8,
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
        stt_whisperx=SttWhisperxSettings(
            model="/tmp/model",
            device="auto",
            compute_type="auto",
            batch_size=8,
            vad_config_path=Path("/tmp/vad/config.yaml"),
            align_model_root=Path("/tmp/align"),
            align_enabled=True,
            vad_backend="pyannote",
            vad_onset=0.5,
            vad_offset=0.363,
            local_files_only=True,
        ),
        security=SecuritySettings(api_token=""),
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
        batch_size=8,
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
    assert payload["batch_size"] == 8
    assert payload["use_batched_pipeline"] is True
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
            "batch_size": kwargs["batch_size"],
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
    assert extra["effective_config"]["batch_size"] == 8
    assert extra["effective_config"]["resolved_device"] == "cuda"


def test_run_stt_uses_batched_pipeline_for_cuda_batching(
    tmp_path: Path, monkeypatch
) -> None:
    """CUDA 且 batch_size>1 时应走 batched pipeline。"""

    calls: dict[str, object] = {}

    class FakeWhisperModel:
        def __init__(self, *args, **kwargs) -> None:
            calls["model_init"] = {"args": args, "kwargs": kwargs}

    class FakeBatchedInferencePipeline:
        def __init__(self, model) -> None:
            calls["pipeline_model"] = model

        def transcribe(self, audio, **kwargs):
            calls["batched_transcribe"] = {"audio": audio, "kwargs": kwargs}
            return iter([SimpleNamespace(text=" hello ", start=0.0, end=1.5)]), None

    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(
            WhisperModel=FakeWhisperModel,
            BatchedInferencePipeline=FakeBatchedInferencePipeline,
        ),
    )
    monkeypatch.setattr(
        "whisper_stt_service.executor.stt._probe_duration", lambda _path: 10.0
    )
    monkeypatch.setattr(
        "whisper_stt_service.executor.stt._emit_progress",
        lambda *args, **kwargs: None,
    )

    input_audio = tmp_path / "demo.wav"
    output_srt = tmp_path / "demo.ja.srt"
    input_audio.write_bytes(b"fake-audio")

    payload = build_stt_effective_config(
        model="/tmp/model",
        language="ja",
        timeout_sec=60,
        max_retries=0,
        device="cuda",
        compute_type="float16",
        batch_size=8,
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
    assert payload["use_batched_pipeline"] is True

    from whisper_stt_service.executor.stt import run_stt

    result = run_stt(
        input_audio,
        output_srt,
        language="ja",
        timeout_sec=60,
        model="/tmp/model",
        device="cuda",
        compute_type="float16",
        batch_size=8,
        beam_size=3,
        best_of=3,
    )

    assert result["use_batched_pipeline"] is True
    assert calls["batched_transcribe"] == {
        "audio": str(input_audio),
        "kwargs": {
            "language": "ja",
            "beam_size": 3,
            "best_of": 3,
            "patience": 1.0,
            "vad_filter": True,
            "vad_parameters": {
                "threshold": 0.45,
                "min_speech_duration_ms": 200,
                "max_speech_duration_s": 18.0,
                "min_silence_duration_ms": 700,
                "speech_pad_ms": 300,
            },
            "no_speech_threshold": 0.6,
            "compression_ratio_threshold": 2.2,
            "log_prob_threshold": -1.0,
            "hallucination_silence_threshold": 1.5,
            "batch_size": 8,
            "without_timestamps": False,
        },
    }
    assert (
        output_srt.read_text(encoding="utf-8")
        == "1\n00:00:00,000 --> 00:00:01,500\nhello\n\n"
    )
