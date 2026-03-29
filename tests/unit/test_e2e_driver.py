from pathlib import Path

from tests.e2e.run_e2e_real_flow import load_video_paths


def test_load_video_paths_requires_non_empty(tmp_path: Path) -> None:
    path_file = tmp_path / "video_paths.txt"
    path_file.write_text("", encoding="utf-8")

    try:
        load_video_paths(path_file)
        assert False, "should raise"
    except ValueError:
        assert True
