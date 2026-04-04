"""Worker 运行时：启动恢复、三阶段执行、进度消费与优雅停机。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Lock, Thread
from time import sleep
from typing import Any

from whisper_stt_service.core.config import Settings
from whisper_stt_service.executor import (
    build_stt_effective_config,
    run_extract,
    run_stt,
    run_translate,
)
from whisper_stt_service.core.progress import ProgressStore
from whisper_stt_service.repo.job_repository import JobRepository


def recover_claimed_to_queued(db) -> int:
    """兼容旧测试入口：启动时把 claimed 任务统一回退。"""

    repo = JobRepository(db)
    return repo.recover_claimed_to_queued()


@dataclass
class WorkerState:
    """记录 worker 的当前活动状态，供队列摘要接口读取。"""

    worker_id: str
    stage: str
    task_id: str | None
    updated_at: str


class WorkerRuntime:
    """管理 worker 线程池、进度消费与停机回滚。"""

    def __init__(
        self,
        *,
        repo: JobRepository,
        progress_store: ProgressStore,
        settings: Settings,
        config_path: Path,
        model_path: str,
    ) -> None:
        """注入运行所需依赖。"""

        self.repo = repo
        self.progress_store = progress_store
        self.settings = settings
        self.config_path = config_path
        self.model_path = model_path

        self._stop_event = Event()
        self._progress_queue: Queue[dict[str, Any]] = Queue(maxsize=4096)
        self._threads: list[Thread] = []
        self._state_lock = Lock()
        self._worker_states: dict[str, WorkerState] = {}

    def start(self) -> int:
        """执行启动恢复并拉起所有后台线程。"""

        recovered = self.repo.recover_claimed_to_queued()
        self._start_thread(self._progress_loop, name="progress-consumer")
        self._start_thread(self._cleanup_loop, name="progress-cleanup")

        self._spawn_stage_workers("extract", self.settings.workers.extract_workers)
        self._spawn_stage_workers("stt", self.settings.workers.stt_workers)
        self._spawn_stage_workers("translate", self.settings.workers.translate_workers)
        return recovered

    def stop(self, timeout_sec: int = 10) -> None:
        """优雅停机：先停领取，再等待线程退出，最后回滚 claimed。"""

        self._stop_event.set()
        for t in self._threads:
            t.join(timeout=timeout_sec)

        # 双保险：停机后把仍处于 claimed 的任务回退，确保下次可恢复。
        self.repo.recover_claimed_to_queued()

    def active_workers(self) -> dict[str, dict]:
        """返回当前 worker 活动视图。"""

        with self._state_lock:
            return {
                worker_id: {
                    "stage": state.stage,
                    "task_id": state.task_id,
                    "updated_at": state.updated_at,
                }
                for worker_id, state in self._worker_states.items()
            }

    def _start_thread(self, target, *, name: str, args: tuple = ()) -> None:
        """统一创建守护线程并保存句柄。"""

        t = Thread(target=target, name=name, args=args, daemon=True)
        t.start()
        self._threads.append(t)

    def _spawn_stage_workers(self, stage: str, count: int) -> None:
        """按阶段配置数量启动 worker 线程。"""

        for idx in range(max(count, 0)):
            worker_id = f"{stage}-w{idx + 1}"
            self._start_thread(
                self._worker_loop,
                name=f"worker-{worker_id}",
                args=(stage, worker_id),
            )

    def _set_worker_state(
        self, worker_id: str, stage: str, task_id: str | None
    ) -> None:
        """更新 worker 当前状态。"""

        now = datetime.now(timezone.utc).isoformat()
        with self._state_lock:
            self._worker_states[worker_id] = WorkerState(
                worker_id=worker_id,
                stage=stage,
                task_id=task_id,
                updated_at=now,
            )

    def _progress_loop(self) -> None:
        """消费 progress_queue 并更新 ProgressStore。"""

        while not self._stop_event.is_set() or not self._progress_queue.empty():
            try:
                event = self._progress_queue.get(timeout=0.5)
            except Empty:
                continue
            task_id = str(event.get("task_id", "")).strip()
            if not task_id:
                continue
            percent = float(event.get("percent", 0.0))
            message = str(event.get("message", ""))
            worker_id = str(event.get("worker_id", ""))
            self.progress_store.update(
                task_id,
                percent=percent,
                message=message,
                worker_id=worker_id,
            )
            if percent >= 100.0 or message.endswith("_done"):
                self.progress_store.mark_done(task_id)

    def _cleanup_loop(self) -> None:
        """定期清理内存中过期完成进度。"""

        interval = max(self.settings.workers.scheduler_interval_sec, 5)
        while not self._stop_event.wait(timeout=interval):
            self.progress_store.cleanup()

    def _worker_loop(self, stage: str, worker_id: str) -> None:
        """单个 worker 主循环：领取任务并执行阶段函数。"""

        poll = max(self.settings.workers.poll_interval_sec, 1)
        lease = max(self.settings.timeouts.lease_timeout_sec, 1)
        while not self._stop_event.is_set():
            self._set_worker_state(worker_id, stage, None)
            claimed = self.repo.claim_next(
                stage=stage, worker_id=worker_id, lease_timeout_sec=lease
            )
            if claimed is None:
                sleep(poll)
                continue
            self._set_worker_state(worker_id, stage, claimed.task_id)
            ctx = self.repo.get_task_execution_context(claimed.task_id)
            if ctx is None:
                self.repo.mark_task_failed(claimed.task_id, "task_context_not_found")
                continue

            task_cfg = ctx.task_config.get("effective_config")
            if not isinstance(task_cfg, dict):
                self.repo.mark_task_failed(claimed.task_id, "invalid_task_config")
                continue

            self._write_task_log(
                log_file=ctx.log_file,
                job_id=ctx.job_id,
                task_id=ctx.task_id,
                stage=ctx.stage,
                worker_id=worker_id,
                event="task_started",
                result="running",
                extra=self._build_task_started_extra(ctx=ctx, stage=stage),
            )

            try:
                if stage == "extract":
                    out_wav = Path(ctx.output_ja_path).with_name(
                        f"{Path(ctx.video_path).stem}.wav"
                    )
                    run_extract(
                        Path(ctx.video_path),
                        out_wav,
                        int(task_cfg.get("timeout_sec", ctx.timeout_sec)),
                        progress_queue=self._progress_queue,
                        task_id=ctx.task_id,
                        worker_id=worker_id,
                    )
                elif stage == "stt":
                    effective_config = run_stt(
                        Path(ctx.video_path),
                        Path(ctx.output_ja_path),
                        language=ctx.source_language,
                        timeout_sec=int(task_cfg.get("timeout_sec", ctx.timeout_sec)),
                        model=self.model_path,
                        device=str(task_cfg.get("device", self.settings.stt.device)),
                        compute_type=str(
                            task_cfg.get("compute_type", self.settings.stt.compute_type)
                        ),
                        beam_size=int(
                            task_cfg.get("beam_size", self.settings.stt.beam_size)
                        ),
                        best_of=int(task_cfg.get("best_of", self.settings.stt.best_of)),
                        patience=float(
                            task_cfg.get("patience", self.settings.stt.patience)
                        ),
                        condition_on_previous_text=(
                            bool(
                                task_cfg.get(
                                    "condition_on_previous_text",
                                    self.settings.stt.condition_on_previous_text,
                                )
                            )
                        ),
                        vad_filter=bool(
                            task_cfg.get("vad_filter", self.settings.stt.vad_filter)
                        ),
                        vad_threshold=float(
                            task_cfg.get(
                                "vad_threshold", self.settings.stt.vad_threshold
                            )
                        ),
                        vad_min_speech_duration_ms=int(
                            task_cfg.get(
                                "vad_min_speech_duration_ms",
                                self.settings.stt.vad_min_speech_duration_ms,
                            )
                        ),
                        vad_max_speech_duration_s=float(
                            task_cfg.get(
                                "vad_max_speech_duration_s",
                                self.settings.stt.vad_max_speech_duration_s,
                            )
                        ),
                        vad_min_silence_duration_ms=int(
                            task_cfg.get(
                                "vad_min_silence_duration_ms",
                                self.settings.stt.vad_min_silence_duration_ms,
                            )
                        ),
                        vad_speech_pad_ms=int(
                            task_cfg.get(
                                "vad_speech_pad_ms", self.settings.stt.vad_speech_pad_ms
                            )
                        ),
                        no_speech_threshold=float(
                            task_cfg.get(
                                "no_speech_threshold",
                                self.settings.stt.no_speech_threshold,
                            )
                        ),
                        compression_ratio_threshold=float(
                            task_cfg.get(
                                "compression_ratio_threshold",
                                self.settings.stt.compression_ratio_threshold,
                            )
                        ),
                        log_prob_threshold=float(
                            task_cfg.get(
                                "log_prob_threshold",
                                self.settings.stt.log_prob_threshold,
                            )
                        ),
                        hallucination_silence_threshold=float(
                            task_cfg.get(
                                "hallucination_silence_threshold",
                                self.settings.stt.hallucination_silence_threshold,
                            )
                        ),
                        initial_prompt=str(
                            task_cfg.get(
                                "initial_prompt", self.settings.stt.initial_prompt
                            )
                        ),
                        hotwords=str(
                            task_cfg.get("hotwords", self.settings.stt.hotwords)
                        ),
                        progress_queue=self._progress_queue,
                        task_id=ctx.task_id,
                        worker_id=worker_id,
                    )
                elif stage == "translate":
                    run_translate(
                        Path(ctx.output_ja_path),
                        Path(ctx.output_zh_path),
                        config_path=self.config_path,
                        timeout_sec=int(task_cfg.get("timeout_sec", ctx.timeout_sec)),
                        chunk_minutes=int(task_cfg.get("chunk_minutes", 30)),
                        retry=int(task_cfg.get("retry", 4)),
                        progress_queue=self._progress_queue,
                        task_id=ctx.task_id,
                        worker_id=worker_id,
                    )
                else:
                    raise RuntimeError(f"unknown stage: {stage}")
                self.repo.mark_task_succeeded(ctx.task_id)
                self._write_task_log(
                    log_file=ctx.log_file,
                    job_id=ctx.job_id,
                    task_id=ctx.task_id,
                    stage=ctx.stage,
                    worker_id=worker_id,
                    event="task_finished",
                    result="succeeded",
                    extra={
                        "attempt": ctx.attempt,
                        **(
                            {"effective_config": effective_config}
                            if stage == "stt"
                            else {}
                        ),
                    },
                )
            except Exception as exc:  # noqa: BLE001
                failure_status = self.repo.mark_task_failed(ctx.task_id, str(exc))
                self.progress_store.update(
                    ctx.task_id,
                    percent=0.0,
                    message=f"{stage}_failed",
                    worker_id=worker_id,
                )
                self._write_task_log(
                    log_file=ctx.log_file,
                    job_id=ctx.job_id,
                    task_id=ctx.task_id,
                    stage=ctx.stage,
                    worker_id=worker_id,
                    event="task_failed",
                    result=failure_status,
                    extra={
                        "attempt": ctx.attempt + 1,
                        "error": str(exc),
                    },
                )

    def _build_task_started_extra(self, *, ctx, stage: str) -> dict[str, Any]:
        """构造 task_started 事件扩展字段，STT 阶段补充生效参数快照。"""

        payload: dict[str, Any] = {
            "attempt": ctx.attempt,
            "max_retries": ctx.max_retries,
            "video_path": ctx.video_path,
        }
        if stage == "stt":
            payload["effective_config"] = build_stt_effective_config(
                model=self.model_path,
                language=ctx.source_language,
                timeout_sec=ctx.timeout_sec,
                max_retries=ctx.max_retries,
                device=self.settings.stt.device,
                compute_type=self.settings.stt.compute_type,
                beam_size=self.settings.stt.beam_size,
                best_of=self.settings.stt.best_of,
                patience=self.settings.stt.patience,
                condition_on_previous_text=self.settings.stt.condition_on_previous_text,
                vad_filter=self.settings.stt.vad_filter,
                vad_threshold=self.settings.stt.vad_threshold,
                vad_min_speech_duration_ms=self.settings.stt.vad_min_speech_duration_ms,
                vad_max_speech_duration_s=self.settings.stt.vad_max_speech_duration_s,
                vad_min_silence_duration_ms=self.settings.stt.vad_min_silence_duration_ms,
                vad_speech_pad_ms=self.settings.stt.vad_speech_pad_ms,
                no_speech_threshold=self.settings.stt.no_speech_threshold,
                compression_ratio_threshold=self.settings.stt.compression_ratio_threshold,
                log_prob_threshold=self.settings.stt.log_prob_threshold,
                hallucination_silence_threshold=self.settings.stt.hallucination_silence_threshold,
                initial_prompt=self.settings.stt.initial_prompt,
                hotwords=self.settings.stt.hotwords,
            )
        return payload

    def _write_task_log(
        self,
        *,
        log_file: str,
        job_id: str,
        task_id: str,
        stage: str,
        worker_id: str,
        event: str,
        result: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """按 task.log jsonl 写业务事件，便于追踪 worker 与 job 行为。"""

        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "job_id": job_id,
            "task_id": task_id,
            "stage": stage,
            "worker_id": worker_id,
            "event": event,
            "result": result,
        }
        if extra:
            payload.update(extra)

        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
