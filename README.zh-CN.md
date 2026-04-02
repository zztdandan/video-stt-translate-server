# video-stt-translate-server v0.1.0

Whisper STT + translation service for batch video processing.

[English](./README.md) | [简体中文](./README.zh-CN.md)

## 项目介绍

本项目是一个全本地化的电影字幕翻译服务项目。

- **轻量 CLI 版本**：可直接在本地命令行运行，适合单视频或小批量快速处理。
- **服务化版本**：支持分任务、分阶段、流水线式批次调度，可并行利用 CPU/GPU 资源，面向大量视频翻译场景。

如果你是第一次使用，建议先走 CLI 路径确认模型与参数，再切换到服务化路径进行批处理。

## 这个 0.1.0 版本包含什么

- 提供一套可本地运行的 CLI 转化方案，位于 `whisper_stt/`。
- 提供一个可通过 REST 访问的服务实现，位于 `whisper_stt_service/`。
- 提供端到端验证脚本 `tests/e2e/run_e2e_real_flow.py`，用于验证流程效果与接口行为。

## 目录说明

- `whisper_stt_service/`：队列化服务主流程（extract -> stt -> translate）。
- `whisper_stt/`：独立脚本（转写/翻译）。
- `tests/`：单元测试与 e2e 测试。

## 运行前要求

- Python `>=3.10`
- 系统可用 `ffmpeg` / `ffprobe`
- 本地可访问 Faster-Whisper 模型目录

推荐模型版本：

- `faster-whisper-large-v2`
- `faster-whisper-large-v3`
- `faster-whisper-large-v3-turbo`

请在你自己的本地环境中设置模型路径，可通过 `config.ini` 的 `runtime.model_path` 或环境变量 `WHISPER_STT_MODEL` 指定。

## 使用 uv 安装依赖

```bash
uv sync --group dev
```

## 配置文件策略

1. 仓库内保留 `config.example.ini`（可提交）。
2. 本地运行使用 `config.ini`（已在 `.gitignore` 中忽略）。
3. 启动行为：
   - 若 `config.ini` 不存在，自动从 `config.example.ini` 生成；
   - 若必填配置缺失，日志按 `section.option` 形式输出。

启动时检查的必填项：

- `workers.extract_workers`
- `workers.stt_workers`
- `workers.translate_workers`
- `timeouts.extract_timeout_sec`
- `timeouts.stt_timeout_sec`
- `timeouts.translate_timeout_sec`
- `timeouts.lease_timeout_sec`
- `retry.extract_max_retries`
- `retry.stt_max_retries`
- `retry.translate_max_retries`
- `runtime.db_path`
- `runtime.log_root`
- `runtime.model_path`
- `llm.base_url`
- `llm.api_key`
- `llm.model`

日志示例：

- `config file not found, created default from example: /abs/path/config.ini`
- `missing required config entries: llm.api_key, runtime.model_path`

## 使用方式 1：脚本/CLI 本地转化

可直接使用仓库脚本执行本地视频处理：

```bash
bash scripts/run_video_ja_srt.sh
bash scripts/run_video_ja_zh.sh
```

也可直接运行 CLI：

```bash
uv run python whisper_stt/transcribe_video.py --help
uv run python whisper_stt/translate_srt_ja_to_zh.py --help
```

## 使用方式 2：启动 REST 服务

```bash
uv run uvicorn whisper_stt_service.main:app --host 0.0.0.0 --port 18000
```

指定配置文件路径：

```bash
WHISPER_STT_CONFIG=/abs/path/config.ini uv run uvicorn whisper_stt_service.main:app --host 0.0.0.0 --port 18000
```

## 使用方式 3：配置并运行端到端测试

1. 编辑 `tests/e2e/video_paths.txt`，填入真实绝对路径视频。
2. 运行端到端驱动：

```bash
uv run python tests/e2e/run_e2e_real_flow.py
```

该脚本会自动拉起服务，通过 REST 接口提交任务并轮询状态直到完成（或超时），用于验证完整链路和接口可用性。

## 运行测试

```bash
uv run pytest -q
```

## Roadmap

### 已完成

- 已实现服务化能力（REST API + 后台 worker）。
- 已实现高度可控的多任务流水线式调度，可分步骤处理大量电影翻译任务。
- 已实现实时任务进度查看与状态轮询。
- 已实现 Job DAG 规划模型（显式 stage 依赖图 + 默认 DAG 兼容回退）。
- 已实现按 stage 的 `job_config` 覆盖与 `task_config` 快照固化。
- 已实现 Job 归档接口（`POST /jobs/{job_id}/archive`），可在保留历史的同时释放路径复用资格。
- 已实现显式 DAG 的 E2E 验收门（首轮 5 分钟 + 连续 1 分钟，且监控/服务/task 日志无错误）。

### 规划中

- MCP 服务化能力，便于标准协议接入与工具互操作。
- Agent Skill 方案，用于让 agent 更稳定地调用服务能力。
- 操作与展示界面（Web 控制台），用于任务提交、监控与排障。
- 容器化交付（Docker / Compose）与可复现部署流程。
- 可观测性增强（结构化日志、指标、告警）与生产级运维支持。
- 权限与配额能力（多用户隔离、并发限制、资源治理）。

## Contributing

欢迎 Issue 与 PR。建议在提交前完成：

```bash
uv run pytest -q
```

提交时请附上变更说明、测试结果和必要的运行截图/日志。

## License

本项目遵循仓库内许可证文件，详见 `LICENSE`。
