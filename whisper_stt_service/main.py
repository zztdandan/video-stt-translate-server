"""服务入口：装配 DB/Repository/Workers，并暴露 ASGI app。"""

from whisper_stt_service.service.bootstrap import build_app


# 在模块导入时创建 app，确保 Uvicorn 能直接发现并加载应用实例。
app = build_app()
