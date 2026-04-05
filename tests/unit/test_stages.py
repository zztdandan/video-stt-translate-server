"""阶段注册表测试。"""

from whisper_stt_service.stages import STAGE_CONFIG_KEYS, SUPPORTED_STAGES


def test_supported_stages_include_stt_whisperx() -> None:
    """新阶段 stt_whisperx 应注册在支持列表内。"""

    assert "stt_whisperx" in SUPPORTED_STAGES
    assert "batch_size" in STAGE_CONFIG_KEYS["stt_whisperx"]
