"""校验 E2E 任务日志/监控日志/服务日志无错误。"""

from __future__ import annotations

import argparse
from pathlib import Path


FORBIDDEN_KEYWORDS = (
    "traceback",
    "exception",
    " task_failed",
    "error",
    "invalid_dependency_payload",
    "invalid_task_config",
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="Verify e2e logs")
    parser.add_argument("--task-log-root", required=True)
    parser.add_argument("--monitor-log", required=True)
    parser.add_argument("--server-log", required=True)
    return parser.parse_args(argv)


def assert_no_error_keywords(path: Path) -> None:
    """断言指定日志文件不包含错误关键字。"""

    if not path.exists() or not path.is_file():
        raise AssertionError(f"missing log file: {path}")
    text = path.read_text(encoding="utf-8", errors="ignore").lower()
    for word in FORBIDDEN_KEYWORDS:
        if word in text:
            raise AssertionError(f"found forbidden keyword '{word.strip()}' in {path}")


def main(argv: list[str] | None = None) -> int:
    """执行日志校验。"""

    args = parse_args(argv)
    task_root = Path(args.task_log_root)
    monitor_log = Path(args.monitor_log)
    server_log = Path(args.server_log)

    task_logs = list(task_root.glob("**/task.log"))
    if not task_logs:
        raise AssertionError(f"no task logs found under {task_root}")

    for path in task_logs:
        assert_no_error_keywords(path)
    assert_no_error_keywords(monitor_log)
    assert_no_error_keywords(server_log)

    print("E2E log verification passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
