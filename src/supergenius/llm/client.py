"""LLM 客户端抽象。

只暴露一个同步方法 `chat(system, user, json_schema=None)`：
- 不传 json_schema：返回纯文本
- 传了 json_schema：优先 response_format=json_schema，失败则尝试 json_object，
  仍失败则不用 response_format，从正文解析 JSON（兼容部分火山 ep 等端点）

走 OpenAI 兼容协议，因此可以直接切换到 DeepSeek、通义千问、Kimi、智谱 bigmodel 等
提供 OpenAI 兼容端点的服务。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from loguru import logger
from openai import OpenAI
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from supergenius.config import LLMConfig

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"

# 部分推理端点（如火山方舟部分 ep）既不支持 response_format 的 json_schema，也不支持 json_object
_PLAIN_JSON_SUFFIX = "\n\n【重要】只输出一个合法 JSON 对象，不要输出 Markdown 代码块或其它说明。"


def _is_unsupported_response_format_error(e: Exception) -> bool:
    s = str(e).lower()
    if "response_format" in s:
        return True
    if "json_object" in s and "not supported" in s:
        return True
    if "json_schema" in s and "not supported" in s:
        return True
    return False


def _parse_json_fenced(content: str) -> dict[str, Any]:
    """从模型返回中解析 JSON（纯 JSON、```json 围栏、或前后夹杂说明）。"""
    t = (content or "").strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", t)
    if m:
        return json.loads(m.group(1).strip())
    i, j = t.find("{"), t.rfind("}")
    if i >= 0 and j > i:
        return json.loads(t[i : j + 1])
    raise json.JSONDecodeError("无法从模型输出中解析 JSON", t, 0)


def render_prompt(name: str, **vars: Any) -> str:
    """加载 prompts/<name>.md 并做 {{key}} 变量替换。"""
    text = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    for k, v in vars.items():
        text = text.replace(f"{{{{{k}}}}}", str(v))
    return text


class LLMClient:
    def __init__(self, cfg: LLMConfig) -> None:
        self._cfg = cfg
        self._client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url)
        # 某次 json_schema + json_object 均报不支持后置 True，后续直走文内 JSON，避免每条请求多打两次失败、刷 WARNING
        self._struct_plain_json_only: bool = False

    @retry(
        reraise=True,
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=8),
    )
    def chat(
        self,
        system: str,
        user: str,
        *,
        json_schema: dict[str, Any] | None = None,
        temperature: float | None = None,
    ) -> str | dict[str, Any]:
        temp = temperature if temperature is not None else self._cfg.temperature
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        kwargs: dict[str, Any] = {
            "model": self._cfg.model,
            "messages": list(messages),
            "temperature": temp,
        }
        if json_schema is not None:
            return self._chat_with_schema(kwargs, json_schema)
        return self._complete_text(kwargs)

    def _complete_text(self, kwargs: dict[str, Any]) -> str:
        resp = self._client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""

    def _plain_json_request(self, base_kwargs: dict[str, Any], messages: list[dict[str, Any]]) -> dict[str, Any]:
        """无 response_format，在 user 末尾要求只输出 JSON。"""
        k = {
            **base_kwargs,
            "messages": [
                *messages[:-1],
                {
                    "role": "user",
                    "content": str(messages[-1].get("content", "")) + _PLAIN_JSON_SUFFIX,
                },
            ],
        }
        k.pop("response_format", None)
        resp = self._client.chat.completions.create(**k)
        return _parse_json_fenced(resp.choices[0].message.content or "")

    def _chat_with_schema(
        self, base_kwargs: dict[str, Any], json_schema: dict[str, Any]
    ) -> dict[str, Any]:
        """先 json_schema → json_object → 无 response_format + 文内 JSON（兼容火山等端点）。"""
        messages = list(base_kwargs["messages"])
        if self._struct_plain_json_only:
            return self._plain_json_request(base_kwargs, messages)

        # 1) OpenAI 风格 json_schema
        k1 = {
            **base_kwargs,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": json_schema.get("name", "output"),
                    "schema": json_schema["schema"],
                    "strict": json_schema.get("strict", True),
                },
            },
        }
        try:
            resp = self._client.chat.completions.create(**k1)
            return _parse_json_fenced(resp.choices[0].message.content or "")
        except Exception as e:
            if not _is_unsupported_response_format_error(e):
                raise
            logger.debug("本端点不支持 json_schema，尝试 json_object")

        # 2) json_object
        k2 = {**base_kwargs, "response_format": {"type": "json_object"}}
        try:
            resp = self._client.chat.completions.create(**k2)
            return _parse_json_fenced(resp.choices[0].message.content or "")
        except Exception as e:
            if not _is_unsupported_response_format_error(e):
                raise
            self._struct_plain_json_only = True
            logger.info(
                "当前 API 端点不支持 response_format（json_schema / json_object）；"
                "已切换为仅用正文 JSON，后续同类调用不再尝试前两档。"
            )

        return self._plain_json_request(base_kwargs, messages)


__all__ = ["LLMClient", "render_prompt", "PROMPTS_DIR"]
