# whisper-stt

Whisper-only STT subproject for full-video transcription to SRT.

This subproject **reuses the root shared venv** at `/.venv` and does not create a separate environment.

## Usage

From repository root:

```bash
bash whisper-stt/scripts/run_video_ja_srt.sh
```

Runtime defaults:

- `DEVICE=auto` (prefer CUDA when available)
- `COMPUTE_TYPE=auto` (`float16` on CUDA, `int8` on CPU)
- `PREEXTRACT_WAV=true` (extract 16k mono WAV first)

The script prints extraction/transcription progress with:

- progress bar
- elapsed time
- ETA
- expected finish time

## Translation (JA -> ZH)

```bash
python whisper-stt/whisper_stt/translate_srt_ja_to_zh.py \
  --input /path/to/input.ja.srt \
  --output /path/to/output.zh.srt \
  --base-url https://www.right.codes/codex/v1 \
  --api-key <key> \
  --model gpt-5.1-codex-mini \
  --parallel 16 \
  --request-interval 1
```

Translation stage prints dispatch/retry and a progress bar with elapsed, ETA, and finish time.

Current sending strategy:

- one request handles at least 200 subtitle entries
- max concurrency is 16, actual workers scale by batch count
- dispatch interval between requests is 1 second

Default input video:

`/srv/media/jellyfin/JELLYFIN/JAV/atmp/115_downloads/PFES-115/489155.com@PFES-115.mp4`

Output subtitle:

`whisper-stt/output/489155.com@PFES-115.ja.srt`
