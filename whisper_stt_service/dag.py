"""DAG 规划、校验与配置归一化。"""

from __future__ import annotations

from collections import deque

from whisper_stt_service.stages import STAGE_CONFIG_KEYS, SUPPORTED_STAGES


def build_default_dag() -> dict:
    """返回与历史行为兼容的默认三阶段 DAG。"""

    return {
        "version": 1,
        "stages": [
            {"stage": "extract", "depends_on": []},
            {"stage": "stt", "depends_on": ["extract"]},
            {"stage": "translate", "depends_on": ["stt"]},
        ],
    }


def normalize_and_validate_dag(raw: dict | None) -> dict:
    """归一化并校验 DAG。"""

    src = build_default_dag() if raw is None else raw
    stages = src.get("stages") if isinstance(src, dict) else None
    if not isinstance(stages, list) or len(stages) == 0:
        raise ValueError("dag_empty")

    normalized_stages: list[dict[str, object]] = []
    stage_names: set[str] = set()
    for item in stages:
        if not isinstance(item, dict):
            raise ValueError("dag_stage_invalid")
        stage = str(item.get("stage", "")).strip()
        if stage == "":
            raise ValueError("dag_stage_empty")
        if stage not in SUPPORTED_STAGES:
            raise ValueError("dag_stage_not_supported")
        if stage in stage_names:
            raise ValueError("dag_stage_duplicated")
        depends_raw = item.get("depends_on", [])
        if depends_raw is None:
            depends_raw = []
        if not isinstance(depends_raw, list):
            raise ValueError("dag_depends_on_invalid")
        deps = [str(x).strip() for x in depends_raw if str(x).strip()]
        if stage in deps:
            raise ValueError("dag_self_dependency")
        normalized_stages.append({"stage": stage, "depends_on": deps})
        stage_names.add(stage)

    for item in normalized_stages:
        for dep in item["depends_on"]:
            if dep not in stage_names:
                raise ValueError("dag_dependency_missing")

    _ensure_no_cycle(normalized_stages)
    return {"version": 1, "stages": normalized_stages}


def normalize_and_validate_job_config(job_config: dict | None, dag: dict) -> dict:
    """归一化并校验 job_config（按 stage 分组）。"""

    if job_config is None:
        return {}
    if not isinstance(job_config, dict):
        raise ValueError("job_config_invalid")

    dag_stages = {str(item["stage"]) for item in dag["stages"]}
    result: dict[str, dict] = {}
    for stage, raw_cfg in job_config.items():
        stage_name = str(stage)
        if stage_name not in dag_stages:
            raise ValueError("job_config_stage_not_in_dag")
        if not isinstance(raw_cfg, dict):
            raise ValueError("job_config_stage_invalid")
        allowed = set(STAGE_CONFIG_KEYS.get(stage_name, ()))
        normalized_cfg: dict[str, object] = {}
        for key, value in raw_cfg.items():
            key_name = str(key)
            if key_name not in allowed:
                raise ValueError("job_config_key_not_supported")
            _validate_stage_config_value(stage_name, key_name, value)
            normalized_cfg[key_name] = value
        result[stage_name] = normalized_cfg
    return result


def _validate_stage_config_value(stage: str, key: str, value: object) -> None:
    """校验 job_config 单个字段的类型和范围。"""

    int_min_rules: dict[tuple[str, str], int] = {
        ("extract", "timeout_sec"): 1,
        ("extract", "max_retries"): 0,
        ("stt", "timeout_sec"): 1,
        ("stt", "max_retries"): 0,
        ("stt", "beam_size"): 1,
        ("stt", "best_of"): 1,
        ("stt", "vad_min_speech_duration_ms"): 50,
        ("stt", "vad_min_silence_duration_ms"): 50,
        ("stt", "vad_speech_pad_ms"): 0,
        ("translate", "timeout_sec"): 1,
        ("translate", "max_retries"): 0,
        ("translate", "chunk_minutes"): 1,
        ("translate", "retry"): 1,
    }
    float_min_rules: dict[tuple[str, str], float] = {
        ("stt", "patience"): 0.1,
        ("stt", "vad_max_speech_duration_s"): 1.0,
        ("stt", "compression_ratio_threshold"): 0.1,
        ("stt", "hallucination_silence_threshold"): 0.0,
    }
    float_range_rules: dict[tuple[str, str], tuple[float, float]] = {
        ("stt", "vad_threshold"): (0.01, 0.99),
        ("stt", "no_speech_threshold"): (0.01, 0.99),
    }
    bool_rules = {
        ("stt", "condition_on_previous_text"),
        ("stt", "vad_filter"),
    }
    str_rules = {
        ("stt", "device"),
        ("stt", "compute_type"),
        ("stt", "initial_prompt"),
        ("stt", "hotwords"),
    }

    key_pair = (stage, key)
    if key_pair in int_min_rules:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("job_config_value_type_invalid")
        if value < int_min_rules[key_pair]:
            raise ValueError("job_config_value_out_of_range")
        return

    if key_pair in float_min_rules:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("job_config_value_type_invalid")
        if float(value) < float_min_rules[key_pair]:
            raise ValueError("job_config_value_out_of_range")
        return

    if key_pair in float_range_rules:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("job_config_value_type_invalid")
        low, high = float_range_rules[key_pair]
        if float(value) < low or float(value) > high:
            raise ValueError("job_config_value_out_of_range")
        return

    if key_pair in bool_rules:
        if not isinstance(value, bool):
            raise ValueError("job_config_value_type_invalid")
        return

    if key_pair in str_rules:
        if not isinstance(value, str):
            raise ValueError("job_config_value_type_invalid")
        return


def _ensure_no_cycle(stages: list[dict[str, object]]) -> None:
    """使用 Kahn 算法检测环。"""

    graph: dict[str, list[str]] = {}
    indegree: dict[str, int] = {}
    for item in stages:
        stage = str(item["stage"])
        graph.setdefault(stage, [])
        indegree.setdefault(stage, 0)

    for item in stages:
        stage = str(item["stage"])
        deps = item["depends_on"]
        for dep in deps:
            graph.setdefault(dep, [])
            graph[dep].append(stage)
            indegree[stage] = indegree.get(stage, 0) + 1

    q = deque([n for n, d in indegree.items() if d == 0])
    visited = 0
    while q:
        cur = q.popleft()
        visited += 1
        for nxt in graph.get(cur, []):
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                q.append(nxt)
    if visited != len(indegree):
        raise ValueError("dag_cycle_detected")
