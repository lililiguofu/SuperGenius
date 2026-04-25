"""飞书多维表格读回字段值的解析。

TEXT 等字段读接口常返回 `{"type":"text","text":"..."}` 或片段列表，而不是 Python str。
写 API 时仍用普通 dict/str 即可；读侧需归一成纯字符串再参与比较与筛选。
"""

from __future__ import annotations

from typing import Any


def feishu_text_to_str(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if isinstance(item, dict) and "text" in item:
                out.append(str(item["text"]))
            elif isinstance(item, str):
                out.append(item)
        return "".join(out)
    if isinstance(value, dict) and "text" in value:
        return str(value["text"])
    return str(value)
