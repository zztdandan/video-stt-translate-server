#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

INPUT_VIDEO="${INPUT_VIDEO:-/srv/media/jellyfin/JELLYFIN/JAV/atmp/MIDA-574-U/489155.com@MIDA-574-U.mp4}"
OUTPUT_SRT="${OUTPUT_SRT:-${REPO_ROOT}/output/489155.com@MIDA-574-U.whisperx.ja.srt}"
MODEL_REF="${MODEL_REF:-/home/base/repo/video-stt-whisper-server/models/faster-whisper-large-v3-turbo}"
DEVICE="${DEVICE:-auto}"
COMPUTE_TYPE="${COMPUTE_TYPE:-auto}"
BATCH_SIZE="${BATCH_SIZE:-16}"
VAD_CONFIG_PATH="${VAD_CONFIG_PATH:-/home/base/repo/video-stt-whisper-server/models/whisperx/vad/pyannote/config.yaml}"
ALIGN_MODEL_ROOT="${ALIGN_MODEL_ROOT:-/home/base/repo/video-stt-whisper-server/models/whisperx/align}"
ALIGN_ENABLED="${ALIGN_ENABLED:-true}"

ALIGN_FLAG="--align-enabled"
if [[ "${ALIGN_ENABLED}" != "true" ]]; then
  ALIGN_FLAG="--no-align-enabled"
fi

"/home/base/repo/video-stt-whisper-server/.venv/bin/python" "${REPO_ROOT}/whisper_stt/transcribe_video_whisperx.py" \
  --input "${INPUT_VIDEO}" \
  --output "${OUTPUT_SRT}" \
  --model "${MODEL_REF}" \
  --language "ja" \
  --device "${DEVICE}" \
  --compute-type "${COMPUTE_TYPE}" \
  --batch-size "${BATCH_SIZE}" \
  --vad-config-path "${VAD_CONFIG_PATH}" \
  --align-model-root "${ALIGN_MODEL_ROOT}" \
  "${ALIGN_FLAG}" \
  --local-files-only
