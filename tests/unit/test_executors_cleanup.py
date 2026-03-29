from pathlib import Path

from whisper_stt_service.executors import preclean_output


def test_preclean_output_deletes_existing_file(tmp_path: Path) -> None:
    out = tmp_path / "video.ja.srt"
    out.write_text("dirty", encoding="utf-8")
    assert out.exists()

    preclean_output(out)
    assert not out.exists()
