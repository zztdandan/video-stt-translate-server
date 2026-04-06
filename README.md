# video-stt-translate-server v0.3.0

Whisper STT + translation service for batch video processing.

[English](./README.md) | [简体中文](./README.zh-CN.md)

## Project overview 

This project is a fully local movie subtitle translation system.

- **CLI edition**: run directly from command line for single videos or small batches.
- **Service edition**: task-batched, pipeline-oriented scheduling for large workloads, with parallel CPU/GPU utilization.

If you are new to the project, start with the CLI path to validate model/runtime settings, then move to the service path for large-scale processing.

## What v0.3.0 includes

- A locally runnable CLI conversion flow under `whisper_stt/`.
- A REST service with queue workers and explicit DAG planning under `whisper_stt_service/`.
- Readable `job_id` / `task_id` generation, plus job archive support (`POST /jobs/{job_id}/archive`) for reusable video paths.
- Configurable STT batch runtime knobs (`[stt] batch_size` and related options) and runtime-effective STT config logging.
- Subtitle output copy-back support via `[translation] copy_back` for delivering `.ja.srt` and `.zh.srt` back to the source video directory.
- WhisperX stage support (`stt_whisperx`) with local VAD + batched ASR + optional alignment.
- End-to-end verification flows in `tests/e2e/run_e2e_real_flow.py` and `tests/e2e/run_e2e_explicit_dag_flow.py`.
- Deterministic E2E exit reason logging (`E2E_EXIT ...`) for operational traceability.

## Project layout

- `whisper_stt_service/`: queue-based service (default extract -> stt -> translate, explicit DAG supports `stt_whisperx`).
- `whisper_stt/`: standalone scripts (`transcribe_video.py`, `translate_srt_ja_to_zh.py`).
- `tests/`: unit/e2e tests.

## Requirements

- Python `>=3.10`
- `ffmpeg` and `ffprobe` in `PATH`
- A local Faster-Whisper model directory

Recommended model variants:

- `faster-whisper-large-v2`
- `faster-whisper-large-v3`
- `faster-whisper-large-v3-turbo`

Use your own local model path in `config.ini` (`runtime.model_path`) or via environment variable `WHISPER_STT_MODEL`.

## Setup with uv

```bash
uv sync --group dev
```

Optional GPU runtime dependencies:

```bash
uv sync --group dev --group gpu
```

## Configuration

1. Keep `config.example.ini` in repo (tracked file).
2. Your local runtime config is `config.ini` (ignored by git).
3. On startup:
   - if `config.ini` does not exist, the service auto-creates it from `config.example.ini`.
   - if required fields are missing, logs print entries in `section.option` format.

Required config fields checked at startup:

- `workers.extract_workers`
- `workers.stt_workers`
- `workers.stt_whisperx_workers`
- `workers.translate_workers`
- `timeouts.extract_timeout_sec`
- `timeouts.stt_timeout_sec`
- `timeouts.translate_timeout_sec`
- `timeouts.lease_timeout_sec`
- `retry.extract_max_retries`
- `retry.stt_max_retries`
- `retry.translate_max_retries`
- `runtime.db_path`
- `runtime.log_root`
- `runtime.model_path`
- `llm.base_url`
- `llm.api_key`
- `llm.model`

WhisperX worker recommendation:

- For long-running GPU jobs, set `workers.stt_whisperx_workers` to `<= 2`.
- The recommended maximum in this release is `2` to reduce CUDA OOM risk.

Example startup log messages:

- `config file not found, created default from example: /abs/path/config.ini`
- `missing required config entries: llm.api_key, runtime.model_path`

Subtitle artifact paths and copy-back behavior:

- Service-internal output (`.ja.srt` / `.zh.srt`) is stored under `artifacts/<job_id>/` next to `runtime.log_root`.
- You can configure `[translation] copy_back` to copy final `.ja.srt` and `.zh.srt` after translate stage.
- Default `copy_back = __video_dir__` copies subtitles back to the input video's folder.

## Usage mode 1: Run by scripts (CLI conversion)

Use the built-in scripts for local video processing:

```bash
bash scripts/run_video_ja_srt.sh
bash scripts/run_video_ja_zh.sh
```

Manual CLI examples:

```bash
uv run python whisper_stt/transcribe_video.py --help
uv run python whisper_stt/transcribe_video_whisperx.py --help
uv run python whisper_stt/translate_srt_ja_to_zh.py --help
```

## Usage mode 2: Start REST service

```bash
uv run uvicorn whisper_stt_service.main:app --host 0.0.0.0 --port 18000
```

Optional config path override:

```bash
WHISPER_STT_CONFIG=/abs/path/config.ini uv run uvicorn whisper_stt_service.main:app --host 0.0.0.0 --port 18000
```

## Usage mode 3: Configure and run E2E test

1. Edit `tests/e2e/video_paths.txt` and provide real absolute video paths.
2. Start the E2E driver:

```bash
uv run python tests/e2e/run_e2e_real_flow.py
```

The script starts the service, submits jobs through REST APIs, and polls job states until completion (or timeout), so you can validate the full pipeline and interface behavior.

## Tests

```bash
uv run pytest -q
```

## Roadmap

### Versioned delivery tree

- `v0.1.0`
  - Service runtime (REST APIs + background workers).
  - Multi-task pipeline scheduling and real-time progress polling.
- `v0.2.0`
  - Job DAG planning model (explicit dependency graph + default DAG fallback).
  - Stage-level `job_config` overrides and `task_config` snapshot solidification.
  - Job archive API (`POST /jobs/{job_id}/archive`).
  - Readable `job_id` / `task_id` generation with collision retry.
  - Translate subtitle copy-back (`[translation] copy_back`).
  - STT runtime knobs (including `[stt] batch_size`) and effective-config logging.
- `v0.3.0` (this release)
  - WhisperX stage (`stt_whisperx`) with local VAD + batched ASR + optional alignment.
  - Official dependency baseline for WhisperX runtime compatibility.
  - New E2E coverage added: explicit DAG runner (`tests/e2e/run_e2e_explicit_dag_flow.py`) with baseline/continuous/until_done modes.
  - Deterministic E2E exit-reason logging (`E2E_EXIT ...`) for success/failure/timeout/interruption traceability.
  - Recovery hardening for interrupted runs (failed/claimed task requeue path) and operational guidance (`stt_whisperx_workers <= 2` recommended for long GPU runs).

### Planned

- MCP service capability for standard protocol integration and tooling interoperability.
- Agent skill integration so coding agents can use this service in a stable way.
- Operation and visualization UI (web console) for submission, monitoring, and troubleshooting.
- Containerization plan (Docker / Compose) with reproducible deployment workflow.
- Better observability (structured logs, metrics, alerts) and production operations support.
- Permission and quota controls (multi-user isolation, concurrency limits, resource governance).

## Contributing

Issues and pull requests are welcome. Before submitting, run:

```bash
uv run pytest -q
```

Please include change summary, test results, and relevant runtime logs/screenshots.

## License

This project follows the repository license. See `LICENSE`.
