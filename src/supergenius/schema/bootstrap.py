"""程序化在飞书多维表格里创建 jobs / resumes / events 三张表。

幂等：已存在的表直接复用；已存在的字段跳过。只补不删。
结束后把 table_id 写回缓存文件（.supergenius/tables.json），运行时读取。
"""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from supergenius.feishu.bitable import BitableClient, BitableError
from supergenius.schema.tables import ALL_TABLES, TableSpec

CACHE_FILE = Path(".supergenius") / "tables.json"


def _cache_load() -> dict[str, str]:
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    return {}


def _cache_save(mapping: dict[str, str]) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def ensure_table(bitable: BitableClient, spec: TableSpec) -> str:
    existing = {t.name: t.table_id for t in bitable.list_tables()}
    if spec.name in existing:
        table_id = existing[spec.name]
        logger.info(f"[bootstrap] 表已存在 {spec.name} table_id={table_id}")
    else:
        try:
            table_id = bitable.create_table(spec.name)
        except BitableError as e:
            if e.code == 91403:
                raise RuntimeError(
                    "create_table 返回 91403 Forbidden：应用无「API 新建数据表」权限。\n"
                    "请给 Base 协作者/可管理，或手动建表 jobs、resumes、events 后重跑。"
                ) from e
            if e.code == 1254302:
                raise RuntimeError(
                    "create_table 返回 1254302：多为高级权限下应用无建表角色。\n"
                    "请「…」→「添加文档应用」给应用「可管理」；"
                    "或手动建三表 jobs、resumes、events 后重跑（只补字段）。"
                ) from e
            raise

    existing_fields = {f.field_name for f in bitable.list_fields(table_id)}
    created = 0
    for fdef in spec.fields:
        if fdef.name in existing_fields:
            continue
        bitable.create_field(table_id, fdef.name, int(fdef.type))
        created += 1
    logger.info(
        f"[bootstrap] 表 {spec.name} 字段同步完成 existing={len(existing_fields)} created={created}"
    )
    return table_id


def bootstrap_all(bitable: BitableClient) -> dict[str, str]:
    """为 ALL_TABLES 中每张表建表/补字段，返回 name -> table_id 映射并落盘。"""
    mapping: dict[str, str] = {}
    for spec in ALL_TABLES:
        mapping[spec.name] = ensure_table(bitable, spec)
    _cache_save(mapping)
    logger.info(f"[bootstrap] 全部完成 -> {CACHE_FILE}")
    return mapping


def load_table_ids() -> dict[str, str]:
    """业务层读取 table_id 的入口。"""
    mapping = _cache_load()
    missing = [spec.name for spec in ALL_TABLES if spec.name not in mapping]
    if missing:
        raise RuntimeError(
            f"缺少表 {missing}。请先运行 `python scripts/bootstrap_tables.py`"
        )
    return mapping


__all__ = ["bootstrap_all", "ensure_table", "load_table_ids", "CACHE_FILE"]
