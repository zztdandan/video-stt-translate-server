# video-stt-translate-server v0.3.0

A local movie translation pipeline that combines `ffmpeg`, `Whisper/WhisperX`, and LLM subtitle translation in one DAG-based system.

[English](./README.md) | [简体中文](./README.zh-CN.md)

## Project purpose

This project unifies three core steps for end-to-end movie translation into one batch-ready pipeline:

- `extract`: audio extraction and preprocessing via `ffmpeg`
- `stt` or `stt_whisperx`: Japanese subtitle transcription via Faster-Whisper / WhisperX
- `translate`: LLM-based Japanese-to-Chinese subtitle translation

Instead of isolated scripts, the service runs these stages as a DAG so jobs are traceable, restartable, and scalable for bulk video processing.

## Key capabilities (v0.3.0)

- Script mode and service mode for single-video to large-batch workflows
- Queue workers with default DAG and explicit DAG execution
- WhisperX stage support (`stt_whisperx`) for local VAD + batched ASR + optional alignment
- Subtitle copy-back (`[translation] copy_back`) to source video directories (`.ja.srt` and `.zh.srt`)
- E2E drivers with deterministic exit reason logging (`E2E_EXIT ...`)

## Project layout

- `whisper_stt_service/`: REST APIs, queue workers, DAG scheduling
- `whisper_stt/`: standalone script workflows
- `tests/`: unit and E2E tests

## Prerequisites

- Python `>=3.10`
- `ffmpeg` and `ffprobe` in `PATH`
- Local Faster-Whisper model path (`runtime.model_path` or `WHISPER_STT_MODEL`)
- LLM config (`llm.base_url`, `llm.api_key`, `llm.model`)

Hardware/runtime options:

- **CPU-only mode**: supported for full pipeline execution
- **CUDA acceleration (NVIDIA GPU)**: recommended for Whisper/WhisperX throughput

WhisperX recommendation for long-running GPU jobs: `workers.stt_whisperx_workers <= 2` to reduce OOM risk.

Recommended model variants:

- `faster-whisper-large-v2`
- `faster-whisper-large-v3`
- `faster-whisper-large-v3-turbo`

## Install with uv

```bash
uv sync --group dev
```

Optional GPU dependencies:

```bash
uv sync --group dev --group gpu
```

## Configuration

1. Keep `config.example.ini` tracked in repo.
2. Use `config.ini` for local runtime values.
3. If `config.ini` is missing, the service auto-generates it from `config.example.ini`.
4. Missing required keys are logged as `section.option`.
5. Set `[security] api_token` to enforce global API access control via `X-API-Token`.

## Three run modes

### 1) Python script mode

```bash
bash scripts/run_video_ja_srt.sh
bash scripts/run_video_ja_zh.sh
```

Or run scripts directly:

```bash
uv run python whisper_stt/transcribe_video.py --help
uv run python whisper_stt/transcribe_video_whisperx.py --help
uv run python whisper_stt/translate_srt_ja_to_zh.py --help
```

### 2) Service mode

```bash
uv run uvicorn whisper_stt_service.main:app --host 0.0.0.0 --port 18000
```

Config override:

```bash
WHISPER_STT_CONFIG=/abs/path/config.ini uv run uvicorn whisper_stt_service.main:app --host 0.0.0.0 --port 18000
```

All API requests (except docs/openapi pages) must include:

```bash
-H "X-API-Token: <your_api_token>"
```

Graceful drain shutdown (stop claiming new tasks, finish in-flight, then exit):

```bash
curl -X POST "http://127.0.0.1:18000/admin/shutdown" \
  -H "Content-Type: application/json" \
  -H "X-API-Token: <your_api_token>" \
  -d '{"reason":"manual_shutdown"}'
```

### 3) E2E mode

The E2E runner starts the service, submits jobs through REST APIs, polls status, and writes monitor/server logs for full pipeline verification.

Recommended background command with logging:

```bash
nohup /home/base/repo/video-stt-whisper-server/.venv/bin/python tests/e2e/run_e2e_explicit_dag_flow.py \
  --run-mode until_done \
  --video-paths tests/e2e/video_paths.txt \
  --poll-sec 15 \
  --deadline-sec 43200 \
  --monitor-log tmp/e2e/explicit_dag_monitor.log \
  --server-log tmp/e2e/explicit_dag_server.log \
  > tmp/e2e/explicit_dag_nohup.log 2>&1 < /dev/null &
echo $! > tmp/e2e/explicit_dag.pid
```

Monitor progress:

```bash
tail -f tmp/e2e/explicit_dag_monitor.log
tail -f tmp/e2e/explicit_dag_nohup.log
```

Example monitor excerpt:

```text
=== E2E Round 1 @ 2026-04-06T04:46:12.725612+00:00 ===
jobs_done=0/4; job_status=(queued=2, running=2)
queue=extract:q=0,c=0,s=19,f=0 | stt_whisperx:q=2,c=2,s=15,f=0 | translate:q=4,c=0,s=15,f=0
task_logs_root=/.../tmp/logs
```

## Tests

```bash
uv run pytest -q
```

## Roadmap

### Completed

- [x] `v0.1.0`: REST service baseline and pipeline scheduling
- [x] `v0.2.0`: DAG planning, archive API, copy-back, and runtime knobs
- [x] `v0.3.0`: WhisperX stage, explicit DAG E2E, deterministic E2E exits, interrupted-run recovery

### Planned

- MCP integration and protocol interoperability
- Agent-skill integration for stable automation
- Web console for submit/monitor/troubleshoot
- Docker/Compose reproducible deployment
- Better observability (logs, metrics, alerts)
- Multi-user permission and quota control

## Contributing

Issues and pull requests are welcome. Before submitting:

```bash
uv run pytest -q
```

## License

Licensed under MIT. See `LICENSE`.
