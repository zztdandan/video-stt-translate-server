# video-stt-translate-server v0.1.0

Whisper STT + translation service for batch video processing.

[English](./README.md) | [简体中文](./README.zh-CN.md)

## Project overview 

This project is a fully local movie subtitle translation system.

- **CLI edition**: run directly from command line for single videos or small batches.
- **Service edition**: task-batched, pipeline-oriented scheduling for large workloads, with parallel CPU/GPU utilization.

If you are new to the project, start with the CLI path to validate model/runtime settings, then move to the service path for large-scale processing.

## What this release includes

- A locally runnable CLI conversion flow under `whisper_stt/`.
- A REST service that can be started for API access under `whisper_stt_service/`.
- An end-to-end verification flow under `tests/e2e/run_e2e_real_flow.py` to validate behavior and API endpoints.

## Project layout

- `whisper_stt_service/`: queue-based service (extract -> stt -> translate).
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

## Configuration

1. Keep `config.example.ini` in repo (tracked file).
2. Your local runtime config is `config.ini` (ignored by git).
3. On startup:
   - if `config.ini` does not exist, the service auto-creates it from `config.example.ini`.
   - if required fields are missing, logs print entries in `section.option` format.

Required config fields checked at startup:

- `workers.extract_workers`
- `workers.stt_workers`
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

Example startup log messages:

- `config file not found, created default from example: /abs/path/config.ini`
- `missing required config entries: llm.api_key, runtime.model_path`

## Usage mode 1: Run by scripts (CLI conversion)

Use the built-in scripts for local video processing:

```bash
bash scripts/run_video_ja_srt.sh
bash scripts/run_video_ja_zh.sh
```

Manual CLI examples:

```bash
uv run python whisper_stt/transcribe_video.py --help
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

### Implemented

- Service runtime is available (REST APIs + background workers).
- Highly controllable multi-task pipeline scheduling is available for stepwise large-batch subtitle translation.
- Real-time task progress querying and status polling are available.

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
