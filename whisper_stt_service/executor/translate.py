"""translate 阶段执行器。"""

from __future__ import annotations

from pathlib import Path
from queue import Queue
from typing import Any, Callable
import json
import time

import requests

from whisper_stt_service.executor.common import (
    SrtEntry,
    _build_translate_messages,
    _dump_srt,
    _emit_progress,
    _extract_json_object,
    _load_llm_config,
    _parse_srt,
    _split_entries_by_time_window,
    preclean_output,
)


def _call_translate_api(
    post_func: Callable[..., requests.Response],
    *,
    base_url: str,
    api_key: str,
    model: str,
    timeout_sec: int,
    batch: list[SrtEntry],
) -> dict[int, str]:
    """调用 LLM 翻译单个批次并返回 id -> 中文文本映射。"""

    messages = _build_translate_messages(batch)
    response = post_func(
        f"{base_url.rstrip('/')}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "temperature": 0.2,
            "messages": messages,
        },
        timeout=timeout_sec,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    parsed = _extract_json_object(content)
    if not isinstance(parsed, list):
        raise ValueError("model output is not a JSON array")

    mapping: dict[int, str] = {}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        text_zh = item.get("text_zh")
        if isinstance(item_id, int) and isinstance(text_zh, str) and text_zh.strip():
            mapping[item_id] = text_zh.strip()
    expected = {e.index for e in batch}
    if set(mapping.keys()) != expected:
        missing = sorted(expected - set(mapping.keys()))
        raise ValueError(f"batch translation ids mismatch, missing={missing[:10]}")
    return mapping


def run_translate(
    input_ja_srt: Path,
    output_zh_srt: Path,
    config_path: Path,
    timeout_sec: int,
    *,
    chunk_minutes: int | None = None,
    retry: int | None = None,
    progress_queue: Queue[dict[str, Any]] | None = None,
    task_id: str | None = None,
    worker_id: str | None = None,
) -> None:
    """直接在 service 内部执行翻译，并把进度写入队列。"""

    # 先清理目标字幕与翻译进度副产物，保障可恢复可重跑。
    preclean_output(output_zh_srt)
    progress_artifact = output_zh_srt.with_suffix(
        output_zh_srt.suffix + ".progress.json"
    )
    preclean_output(progress_artifact)

    config = _load_llm_config(config_path)
    base_url = config.get(
        "llm", "base_url", fallback="https://www.right.codes/codex/v1"
    )
    api_key = config.get("llm", "api_key", fallback="").strip()
    if not api_key:
        raise RuntimeError("missing api key in config")
    model = config.get("llm", "model", fallback="gpt-5.4-mini")
    cfg_chunk_minutes = max(
        config.getint("translation", "chunk_minutes", fallback=30), 1
    )
    parallel = max(config.getint("translation", "parallel", fallback=16), 1)
    cfg_retry = max(config.getint("translation", "retry", fallback=4), 1)
    request_interval = max(
        config.getfloat("translation", "request_interval", fallback=1.0), 0.0
    )

    effective_chunk_minutes = max(int(chunk_minutes or cfg_chunk_minutes), 1)
    effective_retry = max(int(retry or cfg_retry), 1)

    entries = _parse_srt(input_ja_srt.read_text(encoding="utf-8"))
    if not entries:
        raise RuntimeError(f"no valid srt entries found: {input_ja_srt}")

    _emit_progress(
        progress_queue,
        stage="translate",
        percent=0.0,
        message="translate_started",
        task_id=task_id,
        worker_id=worker_id,
    )

    batches = _split_entries_by_time_window(
        entries, window_minutes=effective_chunk_minutes
    )
    translations: dict[int, str] = {}
    started = time.perf_counter()
    session = requests.Session()
    session.trust_env = False

    for batch_no, batch in enumerate(batches, start=1):
        last_err: Exception | None = None
        for _attempt in range(1, effective_retry + 1):
            if time.perf_counter() - started > timeout_sec:
                raise TimeoutError("translate_timeout")
            try:
                mapped = _call_translate_api(
                    session.post,
                    base_url=base_url,
                    api_key=api_key,
                    model=model,
                    timeout_sec=min(timeout_sec, 180),
                    batch=batch,
                )
                translations.update(mapped)
                progress_artifact.write_text(
                    json.dumps(
                        {str(k): v for k, v in translations.items()}, ensure_ascii=False
                    ),
                    encoding="utf-8",
                )
                _emit_progress(
                    progress_queue,
                    stage="translate",
                    percent=batch_no / max(len(batches), 1) * 100.0,
                    message="translate_running",
                    task_id=task_id,
                    worker_id=worker_id,
                )
                break
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                time.sleep(min(request_interval * 2.0, 5.0))
        else:
            raise RuntimeError(f"translate batch failed: {last_err}")

        if parallel > 0:
            # 保留请求节流行为，避免对上游接口形成瞬时冲击。
            time.sleep(request_interval)

    _dump_srt(entries, translations, output_zh_srt)
    _emit_progress(
        progress_queue,
        stage="translate",
        percent=100.0,
        message="translate_done",
        task_id=task_id,
        worker_id=worker_id,
    )
