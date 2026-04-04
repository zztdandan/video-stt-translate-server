"""任务依赖字段的兼容编解码。"""

from __future__ import annotations

import json


def decode_dependency_payload(raw: str | None) -> list[str]:
    """把兼容编码的依赖字段解析为 task_id 列表。"""

    if raw is None:
        return []
    text = raw.strip()
    if text == "":
        return []
    if text.startswith("["):
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            raise ValueError("invalid_dependency_payload")
        deps = [str(x).strip() for x in parsed if str(x).strip()]
        return deps
    return [text]


def encode_dependency_payload(deps: list[str]) -> str | None:
    """按无依赖/单依赖/多依赖规则编码依赖字段。"""

    compact = [d.strip() for d in deps if d.strip()]
    if len(compact) == 0:
        return None
    if len(compact) == 1:
        return compact[0]
    return json.dumps(compact, ensure_ascii=False)
