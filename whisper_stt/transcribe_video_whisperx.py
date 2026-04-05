#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from whisper_stt_service.executor.stt_whisperx import run_stt_whisperx


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transcribe a full video to Japanese SRT using WhisperX"
    )
    parser.add_argument(
        "--input", type=Path, required=True, help="Input video/audio file"
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="Output .srt file path"
    )
    parser.add_argument(
        "--model", type=str, required=True, help="Local whisper model path"
    )
    parser.add_argument("--language", type=str, default="ja", help="Language code")
    parser.add_argument("--device", type=str, default="auto", help="Inference device")
    parser.add_argument(
        "--compute-type", type=str, default="auto", help="CTranslate2 compute type"
    )
    parser.add_argument(
        "--batch-size", type=int, default=16, help="WhisperX batch size"
    )
    parser.add_argument(
        "--vad-config-path",
        type=Path,
        required=True,
        help="Local pyannote VAD config.yaml path",
    )
    parser.add_argument(
        "--align-model-root",
        type=Path,
        required=True,
        help="Local alignment model root (contains ja/zh directories)",
    )
    parser.add_argument(
        "--align-enabled",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable/disable alignment",
    )
    parser.add_argument(
        "--vad-backend",
        type=str,
        default="pyannote",
        help="VAD backend name",
    )
    parser.add_argument("--vad-onset", type=float, default=0.5, help="VAD onset")
    parser.add_argument("--vad-offset", type=float, default=0.363, help="VAD offset")
    parser.add_argument(
        "--local-files-only",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Disallow any runtime model downloads",
    )
    parser.add_argument(
        "--timeout-sec",
        type=int,
        default=7200,
        help="Execution timeout in seconds",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    repo_root = Path(__file__).resolve().parents[2]

    input_path = args.input if args.input.is_absolute() else repo_root / args.input
    output_path = args.output if args.output.is_absolute() else repo_root / args.output
    vad_cfg = (
        args.vad_config_path
        if args.vad_config_path.is_absolute()
        else repo_root / args.vad_config_path
    )
    align_root = (
        args.align_model_root
        if args.align_model_root.is_absolute()
        else repo_root / args.align_model_root
    )
    model_path = Path(args.model)
    model_ref = str(
        model_path if model_path.is_absolute() else (repo_root / model_path)
    )

    if not input_path.is_file():
        raise FileNotFoundError(f"input file not found: {input_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    effective = run_stt_whisperx(
        input_video=input_path,
        output_ja_srt=output_path,
        language=args.language,
        timeout_sec=max(args.timeout_sec, 1),
        model=model_ref,
        device=args.device,
        compute_type=args.compute_type,
        batch_size=max(args.batch_size, 1),
        vad_config_path=str(vad_cfg),
        align_model_root=str(align_root),
        align_enabled=bool(args.align_enabled),
        vad_backend=args.vad_backend,
        vad_onset=args.vad_onset,
        vad_offset=args.vad_offset,
        local_files_only=bool(args.local_files_only),
    )
    elapsed = time.perf_counter() - started

    print(f"input={input_path}", flush=True)
    print(f"output={output_path}", flush=True)
    print(f"elapsed_sec={elapsed:.2f}", flush=True)
    print(f"effective_config={json.dumps(effective, ensure_ascii=False)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
