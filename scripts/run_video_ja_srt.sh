#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

INPUT_VIDEO="${INPUT_VIDEO:-/srv/media/jellyfin/JELLYFIN/JAV/atmp/115_downloads/PFES-115/489155.com@PFES-115.mp4}"
OUTPUT_SRT="${OUTPUT_SRT:-${REPO_ROOT}/whisper-stt/output/489155.com@PFES-115.ja.srt}"
MODEL_REF="${MODEL_REF:-models/faster-whisper-small}"
DEVICE="${DEVICE:-auto}"
COMPUTE_TYPE="${COMPUTE_TYPE:-auto}"
PREEXTRACT_WAV="${PREEXTRACT_WAV:-true}"
KEEP_TEMP_AUDIO="${KEEP_TEMP_AUDIO:-false}"
PROGRESS_EVERY="${PROGRESS_EVERY:-25}"

PREEXTRACT_FLAG="--preextract-wav"
if [[ "${PREEXTRACT_WAV}" != "true" ]]; then
  PREEXTRACT_FLAG="--no-preextract-wav"
fi

KEEP_TEMP_FLAG="--no-keep-temp-audio"
if [[ "${KEEP_TEMP_AUDIO}" == "true" ]]; then
  KEEP_TEMP_FLAG="--keep-temp-audio"
fi

"${REPO_ROOT}/.venv/bin/python" "${REPO_ROOT}/whisper-stt/whisper_stt/transcribe_video.py" \
  --input "${INPUT_VIDEO}" \
  --output "${OUTPUT_SRT}" \
  --model "${MODEL_REF}" \
  --language "ja" \
  --device "${DEVICE}" \
  --compute-type "${COMPUTE_TYPE}" \
  "${PREEXTRACT_FLAG}" \
  "${KEEP_TEMP_FLAG}" \
  --progress-every "${PROGRESS_EVERY}"
