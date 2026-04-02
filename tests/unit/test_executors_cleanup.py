"""执行器清理行为单元测试。"""

import subprocess
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


def test_preclean_output_always_calls_rm_force(monkeypatch) -> None:
    """即使文件不存在，也应先执行 rm -f 规避 SMB 残留目录项。"""

    called: list[list[str]] = []

    def fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        called.append(list(args[0]))
        return subprocess.CompletedProcess(args[0], 0)

    monkeypatch.setattr("whisper_stt_service.executors.subprocess.run", fake_run)
    preclean_output(Path("/tmp/not-exist.wav"))

    assert called
    assert called[0][0:2] == ["rm", "-f"]
    assert called[0][-2] == "--"
