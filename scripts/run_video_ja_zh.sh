#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

INPUT_VIDEO="${INPUT_VIDEO:-}"
if [[ -z "${INPUT_VIDEO}" ]]; then
  echo "INPUT_VIDEO is required" >&2
  exit 1
fi

VIDEO_BASENAME="$(basename "${INPUT_VIDEO}")"
VIDEO_STEM="${VIDEO_BASENAME%.*}"
OUTPUT_DIR="${OUTPUT_DIR:-${REPO_ROOT}/whisper-stt/output}"
OUTPUT_JA="${OUTPUT_JA:-${OUTPUT_DIR}/${VIDEO_STEM}.ja.srt}"
OUTPUT_ZH="${OUTPUT_ZH:-${OUTPUT_DIR}/${VIDEO_STEM}.zh.srt}"
CONFIG_PATH="${CONFIG_PATH:-${REPO_ROOT}/config.ini}"

mkdir -p "${OUTPUT_DIR}"

INPUT_VIDEO="${INPUT_VIDEO}" \
OUTPUT_SRT="${OUTPUT_JA}" \
bash "${REPO_ROOT}/whisper-stt/scripts/run_video_ja_srt.sh"

"${REPO_ROOT}/.venv/bin/python" "${REPO_ROOT}/whisper-stt/whisper_stt/translate_srt_ja_to_zh.py" \
  --input "${OUTPUT_JA}" \
  --output "${OUTPUT_ZH}" \
  --config "${CONFIG_PATH}"
