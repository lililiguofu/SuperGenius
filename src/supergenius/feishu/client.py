"""lark-oapi Client 单例。"""

from __future__ import annotations

from functools import lru_cache

import lark_oapi as lark

from supergenius.config import FeishuConfig


@lru_cache(maxsize=1)
def get_lark_client(app_id: str, app_secret: str) -> lark.Client:
    """返回线程安全、可复用的 lark Client；同一对 (app_id, app_secret) 只建一次。"""
    return (
        lark.Client.builder()
        .app_id(app_id)
        .app_secret(app_secret)
        .log_level(lark.LogLevel.WARNING)
        .build()
    )


def client_from_config(cfg: FeishuConfig) -> lark.Client:
    return get_lark_client(cfg.app_id, cfg.app_secret)
