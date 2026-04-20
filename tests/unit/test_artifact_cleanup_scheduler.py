"""artifact 小时级清理调度测试。"""

from __future__ import annotations

from pathlib import Path

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
from whisper_stt_service.db import Database
from whisper_stt_service.progress import ProgressStore
from whisper_stt_service.repository import JobRepository
from whisper_stt_service.service.runtime import WorkerRuntime


def _settings(artifact_root: Path) -> Settings:
    """构造开启 artifact 清理线程的最小配置。"""

    return Settings(
        workers=WorkerSettings(
            extract_workers=0,
            stt_workers=0,
            stt_whisperx_workers=0,
            translate_workers=0,
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
            artifact_root=artifact_root,
            artifact_cleanup_enabled=True,
            artifact_cleanup_interval_sec=3600,
            artifact_cleanup_statuses=("succeeded",),
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
            vad_onset=0.35,
            vad_offset=0.2,
            local_files_only=True,
        ),
        security=SecuritySettings(api_token=""),
    )


def test_artifact_cleanup_removes_only_succeeded_job_dirs(tmp_path: Path) -> None:
    """仅已完成 job 的 artifact 目录会被清理。"""

    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(parents=True, exist_ok=True)

    db = Database(tmp_path / "q.db")
    db.init_schema()
    repo = JobRepository(db, artifact_root=artifact_root)

    done_job = repo.enqueue("/tmp/done.mp4", "ja").job_id
    running_job = repo.enqueue("/tmp/running.mp4", "ja").job_id

    with db.tx() as conn:
        conn.execute(
            "UPDATE jobs SET status='succeeded' WHERE job_id=?",
            (done_job,),
        )
        conn.execute(
            "UPDATE jobs SET status='running' WHERE job_id=?",
            (running_job,),
        )

    (artifact_root / done_job).mkdir(parents=True, exist_ok=True)
    (artifact_root / running_job).mkdir(parents=True, exist_ok=True)
    (artifact_root / "job-unknown").mkdir(parents=True, exist_ok=True)

    runtime = WorkerRuntime(
        repo=repo,
        progress_store=ProgressStore(300),
        settings=_settings(artifact_root),
        config_path=tmp_path / "config.ini",
        model_path="/tmp/model",
    )

    runtime._run_artifact_cleanup_once()

    assert not (artifact_root / done_job).exists()
    assert (artifact_root / running_job).exists()
    assert (artifact_root / "job-unknown").exists()
