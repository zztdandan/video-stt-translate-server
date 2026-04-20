# video-stt-translate-server v0.4.0

面向批量电影翻译的本地流水线系统：将 `ffmpeg` 媒体处理、`Whisper/WhisperX` 语音转写、LLM 字幕翻译整合为一个可编排 DAG。

[English](./README.md) | [简体中文](./README.zh-CN.md)

## 项目定位

本项目的目标是把电影翻译链路中的三个核心能力统一到一个可批处理、可恢复、可观测的执行系统中：

- `extract`：用 `ffmpeg` 抽取/预处理音频。
- `stt` 或 `stt_whisperx`：用 Faster-Whisper / WhisperX 完成日语字幕转写。
- `translate`：用大模型把日语字幕翻译为中文字幕。

服务层通过 DAG 组织任务依赖关系，支持批量视频流水线式处理，并通过 `job_id` / `task_id` 与阶段日志实现可追踪运行。

## 功能概览（v0.4.0）

- 支持脚本模式与服务模式，覆盖单视频到批量队列任务。
- 支持显式 DAG 与默认 DAG 执行路径，WhisperX 可作为独立阶段接入。
- WhisperX VAD 默认参数更激进（`vad_onset=0.35`、`vad_offset=0.2`），提升语音召回。
- 支持字幕产物回写（`[translation] copy_back`），输出 `.ja.srt` 与 `.zh.srt`。
- 支持可读任务 ID、任务归档、失败重试与中断续跑。
- 支持全局 API Token 鉴权（`X-API-Token`）与安全停机接口。
- 支持 drain 停机：停止领取新任务，等待在途任务完成后自动退出。
- 支持小时级 artifact 自动清理（按已完成 job 清理目录，降低磁盘占用）。
- 提供端到端验证脚本，支持轮询、超时、退出原因记录（`E2E_EXIT ...`）。

## 目录说明

- `whisper_stt_service/`：REST 服务、队列 worker、DAG 调度。
- `whisper_stt/`：本地脚本（转写、翻译）。
- `tests/`：单元测试与 E2E 驱动脚本。

## 运行前要求

- Python `>=3.10`
- 系统可用 `ffmpeg` / `ffprobe`
- 本地可访问 Faster-Whisper 模型目录（`runtime.model_path` 或 `WHISPER_STT_MODEL`）
- LLM 翻译配置可用（`llm.base_url` / `llm.api_key` / `llm.model`）

支持两种硬件运行方式：

- **纯 CPU 模式**：可完整运行整条流程，适合功能验证和低并发任务。
- **CUDA 加速模式（NVIDIA GPU）**：在 Whisper/WhisperX 阶段显著提速，适合大批量生产任务。

WhisperX 长时任务建议：`workers.stt_whisperx_workers <= 2`，可显著降低 CUDA OOM 风险。

推荐模型版本：

- `faster-whisper-large-v2`
- `faster-whisper-large-v3`
- `faster-whisper-large-v3-turbo`

## 安装依赖

```bash
uv sync --group dev
```

如需 GPU 依赖：

```bash
uv sync --group dev --group gpu
```

## 配置策略

1. `config.example.ini` 作为模板保留在仓库。
2. `config.ini` 作为本地运行配置（默认不提交）。
3. 服务启动时若 `config.ini` 不存在，会自动由 `config.example.ini` 生成。
4. 若缺少必填项，日志会输出 `section.option` 形式的缺失键。
5. 可在 `[security] api_token` 中配置全局 API 访问令牌（请求头 `X-API-Token`）。
6. 可通过 `[runtime] artifact_cleanup_*` 配置开启小时级 artifact 自动清理。

关键配置包括：worker 并发、超时、重试、模型路径、日志路径、LLM 接口配置。

## 三种运行模式

### 1) Python 脚本模式（适合单视频/调参）

```bash
bash scripts/run_video_ja_srt.sh
bash scripts/run_video_ja_zh.sh
```

或直接调用：

```bash
uv run python whisper_stt/transcribe_video.py --help
uv run python whisper_stt/transcribe_video_whisperx.py --help
uv run python whisper_stt/translate_srt_ja_to_zh.py --help
```

### 2) 服务模式（适合批量任务）

```bash
uv run uvicorn whisper_stt_service.main:app --host 0.0.0.0 --port 18000
```

指定配置文件：

```bash
WHISPER_STT_CONFIG=/abs/path/config.ini uv run uvicorn whisper_stt_service.main:app --host 0.0.0.0 --port 18000
```

除 docs/openapi 页面外，所有 API 请求都需要携带：

```bash
-H "X-API-Token: <your_api_token>"
```

优雅停机（停止领取新任务，等待在途任务完成后自动退出）：

```bash
curl -X POST "http://127.0.0.1:18000/admin/shutdown" \
  -H "Content-Type: application/json" \
  -H "X-API-Token: <your_api_token>" \
  -d '{"reason":"manual_shutdown"}'
```

### 3) E2E 模式（真实链路验证）

E2E 驱动会自动拉起服务、提交任务、轮询状态并持续记录监控日志，适合验证完整电影翻译流水线。

推荐命令（后台持续运行，含日志落盘）：

```bash
nohup /home/base/repo/video-stt-whisper-server/.venv/bin/python tests/e2e/run_e2e_explicit_dag_flow.py \
  --run-mode until_done \
  --video-paths tests/e2e/video_paths.txt \
  --poll-sec 15 \
  --deadline-sec 43200 \
  --monitor-log tmp/e2e/explicit_dag_monitor.log \
  --server-log tmp/e2e/explicit_dag_server.log \
  > tmp/e2e/explicit_dag_nohup.log 2>&1 < /dev/null &
echo $! > tmp/e2e/explicit_dag.pid
```

监控方式：

```bash
tail -f tmp/e2e/explicit_dag_monitor.log
tail -f tmp/e2e/explicit_dag_nohup.log
```

分轮停机验证：

```bash
bash /home/base/repo/video-stt-whisper-server/scripts/run_shutdown_round.sh round1
bash /home/base/repo/video-stt-whisper-server/scripts/run_shutdown_round.sh round2
```

典型监控日志片段（节选）：

```text
=== E2E Round 1 @ 2026-04-06T04:46:12.725612+00:00 ===
jobs_done=0/4; job_status=(queued=2, running=2)
queue=extract:q=0,c=0,s=19,f=0 | stt_whisperx:q=2,c=2,s=15,f=0 | translate:q=4,c=0,s=15,f=0
task_logs_root=/.../tmp/logs
```

## 测试

```bash
uv run pytest -q
```

## Roadmap

### 已完成

- [x] `v0.1.0`：REST API + 后台 worker + 基础流水线调度。
- [x] `v0.2.0`：DAG 规划、任务配置快照、归档接口、字幕回写、STT 参数配置化。
- [x] `v0.3.0`：WhisperX 阶段、显式 DAG E2E、确定性退出日志、中断续跑增强。
- [x] `v0.4.0`：全局 API 鉴权、安全 drain 停机、分轮停机 E2E、小时级 artifact 清理调度。

### 相比 v0.3.0 的增量

- WhisperX VAD 默认参数调优（`vad_onset=0.35`、`vad_offset=0.2`）
- 翻译提示词增强：重复口水词/无语义重复自动降噪
- 新增管理接口：`POST /admin/shutdown`、`GET /admin/shutdown/status`
- 新增按已完成 job 目录执行的定时 artifact 清理器

### 规划中

- MCP 服务化能力与标准协议接入。
- Agent Skill 集成与稳定调用方案。
- Web 控制台（提交、监控、排障）。
- Docker / Compose 可复现部署。
- 结构化日志、指标、告警等可观测性增强。
- 多用户权限、并发限制与资源配额治理。

## Contributing

欢迎 Issue 与 PR，提交前建议执行：

```bash
uv run pytest -q
```

## License

本项目采用 MIT 许可证，详见 `LICENSE`。
