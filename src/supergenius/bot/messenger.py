"""向飞书发送消息的封装。"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger


def send_text(lark_client: Any, chat_id: str, text: str) -> bool:
    """向指定 chat_id 发送纯文本消息（最多 4096 字，超出自动截断）。"""
    try:
        from lark_oapi.api.im.v1 import (  # type: ignore[import-untyped]
            CreateMessageRequest,
            CreateMessageRequestBody,
        )

        content = json.dumps({"text": text[:4000]}, ensure_ascii=False)
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("text")
                .content(content)
                .build()
            )
            .build()
        )
        resp = lark_client.im.v1.message.create(request)
        if not resp.success():
            logger.error(f"[messenger] 发消息失败: {resp.code} {resp.msg}")
            return False
        return True
    except Exception as exc:
        logger.exception(f"[messenger] send_text 异常: {exc}")
        return False
