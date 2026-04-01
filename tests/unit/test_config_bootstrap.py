"""配置文件引导与缺项检测单元测试。"""

from pathlib import Path

from whisper_stt_service.config import ensure_config_file, find_missing_required_fields


def test_ensure_config_file_creates_from_example(tmp_path: Path) -> None:
    """当目标配置不存在时，应按 example 创建默认配置。"""

    config_path = tmp_path / "config.ini"
    example_path = tmp_path / "config.example.ini"
    example_path.write_text("[workers]\nextract_workers = 1\n", encoding="utf-8")

    created = ensure_config_file(config_path=config_path, example_path=example_path)

    assert created is True
    assert config_path.read_text(encoding="utf-8") == "[workers]\nextract_workers = 1\n"


def test_find_missing_required_fields_reports_missing_entries(tmp_path: Path) -> None:
    """应返回 section/option 维度的缺失项，便于启动日志输出。"""

    config_path = tmp_path / "config.ini"
    config_path.write_text(
        """
[workers]
extract_workers = 2

[llm]
base_url = https://example.com
""".strip(),
        encoding="utf-8",
    )

    missing = find_missing_required_fields(config_path)

    assert "workers" in missing
    assert "stt_workers" in missing["workers"]
    assert "llm" in missing
    assert "api_key" in missing["llm"]
    assert "timeouts" in missing
