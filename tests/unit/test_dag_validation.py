"""DAG 归一化与校验测试。"""

import pytest

from whisper_stt_service.dag import (
    build_default_dag,
    normalize_and_validate_dag,
    normalize_and_validate_job_config,
)


def test_default_dag_shape() -> None:
    """默认 DAG 应为 extract->stt->translate。"""

    dag = build_default_dag()
    assert dag["version"] == 1
    assert [x["stage"] for x in dag["stages"]] == ["extract", "stt", "translate"]


def test_validate_dag_rejects_cycle() -> None:
    """有环依赖应被拒绝。"""

    dag = {
        "version": 1,
        "stages": [
            {"stage": "extract", "depends_on": ["translate"]},
            {"stage": "translate", "depends_on": ["extract"]},
        ],
    }
    with pytest.raises(ValueError, match="dag_cycle_detected"):
        normalize_and_validate_dag(dag)


def test_validate_dag_rejects_missing_dependency() -> None:
    """引用不存在 stage 应失败。"""

    dag = {
        "version": 1,
        "stages": [{"stage": "stt", "depends_on": ["extract"]}],
    }
    with pytest.raises(ValueError, match="dag_dependency_missing"):
        normalize_and_validate_dag(dag)


def test_validate_dag_rejects_duplicate_stage() -> None:
    """stage 重复应失败。"""

    dag = {
        "version": 1,
        "stages": [
            {"stage": "extract", "depends_on": []},
            {"stage": "extract", "depends_on": []},
        ],
    }
    with pytest.raises(ValueError, match="dag_stage_duplicated"):
        normalize_and_validate_dag(dag)


def test_validate_dag_rejects_unknown_stage() -> None:
    """未知 stage 应失败。"""

    dag = {
        "version": 1,
        "stages": [{"stage": "foo", "depends_on": []}],
    }
    with pytest.raises(ValueError, match="dag_stage_not_supported"):
        normalize_and_validate_dag(dag)


def test_job_config_rejects_invalid_type() -> None:
    """job_config 的字段类型非法时应拒绝。"""

    dag = build_default_dag()
    with pytest.raises(ValueError, match="job_config_value_type_invalid"):
        normalize_and_validate_job_config({"stt": {"beam_size": "3"}}, dag)


def test_job_config_accepts_stt_batch_size() -> None:
    """stt.batch_size 应允许作为单任务覆盖参数。"""

    dag = build_default_dag()
    normalized = normalize_and_validate_job_config({"stt": {"batch_size": 8}}, dag)
    assert normalized["stt"]["batch_size"] == 8


def test_job_config_accepts_translate_copy_back() -> None:
    """translate.copy_back 应允许作为单任务覆盖参数。"""

    dag = build_default_dag()
    normalized = normalize_and_validate_job_config(
        {"translate": {"copy_back": "/tmp/subtitles"}}, dag
    )
    assert normalized["translate"]["copy_back"] == "/tmp/subtitles"


def test_job_config_accepts_stt_whisperx_batch_size() -> None:
    """stt_whisperx.batch_size 应允许作为单任务覆盖参数。"""

    dag = {
        "version": 1,
        "stages": [
            {"stage": "extract", "depends_on": []},
            {"stage": "stt_whisperx", "depends_on": ["extract"]},
            {"stage": "translate", "depends_on": ["stt_whisperx"]},
        ],
    }
    normalized_dag = normalize_and_validate_dag(dag)
    normalized = normalize_and_validate_job_config(
        {"stt_whisperx": {"batch_size": 16, "align_enabled": True}},
        normalized_dag,
    )
    assert normalized["stt_whisperx"]["batch_size"] == 16
