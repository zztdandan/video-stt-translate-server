"""执行器清理行为单元测试。"""

from pathlib import Path

from whisper_stt_service.executors import preclean_output


def test_preclean_output_deletes_existing_file(tmp_path: Path) -> None:
    """目标文件存在时，preclean_output 应先删除旧文件。"""

    out = tmp_path / "video.ja.srt"
    out.write_text("dirty", encoding="utf-8")
    assert out.exists()

    # 执行前置清理后，旧文件必须消失。
    preclean_output(out)
    assert not out.exists()
