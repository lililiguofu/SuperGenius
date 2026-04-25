"""清空 jobs / resumes / events 全表记录，便于整条 MVP 从头上演示。

不删表结构；与 bootstrap_tables 配合：先本脚本，再 seed_jobs + candidate_emitter，最后 run_mvp。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from loguru import logger  # noqa: E402

from supergenius.runtime import boot  # noqa: E402


def _wipe_table(ctx, table_key: str, page_size: int = 500) -> int:
    tid = ctx.table_ids[table_key]
    total = 0
    for _ in range(200):
        rows = ctx.bitable.search_records(tid, filter_conditions=None, page_size=page_size)
        if not rows:
            break
        for r in rows:
            ctx.bitable.delete_record(tid, r.record_id)
            total += 1
    return total


def main() -> None:
    _, ctx = boot()
    for name in ("resumes", "jobs", "events"):
        n = _wipe_table(ctx, name)
        logger.info(f"[reset] 已删除 {name} 表 {n} 行")
    logger.info("[reset] 完成。接下来可执行 seed_jobs -> candidate_emitter -> run_mvp.py")


if __name__ == "__main__":
    main()
