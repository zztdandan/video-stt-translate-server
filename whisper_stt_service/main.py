"""服务 ASGI 入口模块。"""

from __future__ import annotations

from whisper_stt_service.api import create_app

# 在模块导入时创建 app，确保 Uvicorn 能直接发现并加载应用实例。
app = create_app()
