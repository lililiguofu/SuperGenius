"""只生成 JD：运行 JD 策划官，把 jobs 里 status=draft 的行写出 jd_text，并变为 jd_pending_approval。

用法（项目根目录，已配置 .env 且已 bootstrap）：
  uv run python scripts/seed_jobs.py          # 若无 draft 岗位，先插一条
  uv run python scripts/generate_jd.py        # 仅调 LLM 写 jd_text，不跑初筛/其它 Agent

查看结果：打开飞书多维表格 jobs 表，看对应 job_id 的 jd_text 与 status。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from loguru import logger  # noqa: E402

from supergenius.agents.jd_strategist import JDStrategistAgent  # noqa: E402
from supergenius.feishu.field_value import feishu_text_to_str  # noqa: E402
from supergenius.runtime import boot  # noqa: E402
from supergenius.schema.tables import JobStatus  # noqa: E402


def main() -> None:
    _, ctx = boot()
    jobs_tid = ctx.table_ids["jobs"]
    agent = JDStrategistAgent(ctx)
    n = agent.tick()
    if n == 0:
        logger.warning(
            "没有可处理的 draft 岗位。请先运行: uv run python scripts/seed_jobs.py"
        )
        return
    rows = ctx.bitable.search_records(
        jobs_tid,
        filter_conditions=[
            {
                "field_name": "status",
                "operator": "is",
                "value": [JobStatus.JD_PENDING_APPROVAL.value],
            }
        ],
        page_size=20,
    )
    for r in rows:
        jid = feishu_text_to_str(r.fields.get("job_id"))
        jt = feishu_text_to_str(r.fields.get("jd_text"))
        preview = (jt[:300] + "…") if len(jt) > 300 else jt
        logger.info(f"已生成 JD | job_id={jid} | 长度={len(jt)} 字符")
        logger.info(f"预览:\n{preview}")


if __name__ == "__main__":
    main()
