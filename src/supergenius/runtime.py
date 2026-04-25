"""运行时装配：把 settings / bitable / llm / agents 拼起来。"""

from __future__ import annotations

from supergenius.agents import AgentContext
from supergenius.config import Settings, load_settings, setup_logging
from supergenius.feishu import BitableClient, get_lark_client
from supergenius.llm import LLMClient
from supergenius.schema.bootstrap import load_table_ids


def boot(dry_run: bool = False) -> tuple[Settings, AgentContext]:
    settings = load_settings()
    setup_logging(settings.log_level)

    lark_client = get_lark_client(settings.feishu.app_id, settings.feishu.app_secret)
    bitable = BitableClient(lark_client, settings.feishu.bitable_app_token)
    table_ids = load_table_ids()
    llm = LLMClient(settings.llm)

    ctx = AgentContext(
        bitable=bitable,
        table_ids=table_ids,
        llm=llm,
        config=settings,
        dry_run=dry_run,
    )
    return settings, ctx


__all__ = ["boot"]
