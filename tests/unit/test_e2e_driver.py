"""E2E 驱动辅助函数单元测试。"""

from pathlib import Path

from tests.e2e.run_e2e_explicit_dag_flow import parse_args
from tests.e2e.run_e2e_real_flow import load_video_paths


def test_load_video_paths_requires_non_empty(tmp_path: Path) -> None:
    """视频路径文件为空时，load_video_paths 应抛出 ValueError。"""

    path_file = tmp_path / "video_paths.txt"
    path_file.write_text("", encoding="utf-8")

    # 用显式 try/except 保证异常类型与行为可读。
    try:
        load_video_paths(path_file)
        assert False, "should raise"
    except ValueError:
        assert True


def test_explicit_e2e_args_defaults() -> None:
    """显式 DAG E2E 默认应为 continuous，按 1 分钟门运行。"""

    args = parse_args([])
    assert args.run_mode == "continuous"
    assert args.min_monitor_sec == 0


def test_explicit_e2e_args_baseline_mode() -> None:
    """baseline 模式用于首轮 5 分钟门。"""

    args = parse_args(["--run-mode", "baseline"])
    assert args.run_mode == "baseline"
