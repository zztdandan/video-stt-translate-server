"""服务层导出。"""

from whisper_stt_service.service.runtime import WorkerRuntime, recover_claimed_to_queued


def build_app():
    """延迟导入应用装配，避免包初始化阶段形成循环依赖。"""

    from whisper_stt_service.service.bootstrap import build_app as _build_app

    return _build_app()


__all__ = ["WorkerRuntime", "build_app", "recover_claimed_to_queued"]
