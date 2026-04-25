"""写一条岗位 draft 到 jobs 表，触发 JD 策划官的链路。

默认读 fixtures/job_brief.json；如果岗位 job_id 已经在表里，就跳过（不重复插）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from loguru import logger  # noqa: E402

from supergenius.agents.base import utc_now_iso  # noqa: E402
from supergenius.runtime import boot  # noqa: E402
from supergenius.schema.tables import JobStatus  # noqa: E402

FIXTURE = ROOT / "fixtures" / "job_brief.json"


def main() -> None:
    settings, ctx = boot()

    brief = json.loads(FIXTURE.read_text(encoding="utf-8"))
    jobs_tid = ctx.table_ids["jobs"]
    job_id = brief["job_id"]

    existing = ctx.bitable.search_records(
        jobs_tid,
        filter_conditions=[{"field_name": "job_id", "operator": "is", "value": [job_id]}],
        page_size=1,
    )
    if existing:
        logger.info(f"job_id={job_id} 已存在，跳过")
        return

    record = {
        "job_id": job_id,
        "title": brief["title"],
        "level": brief["level"],
        "headcount": brief["headcount"],
        "budget_min": brief["budget_min"],
        "budget_max": brief["budget_max"],
        "urgency": brief["urgency"],
        "jd_brief": brief["jd_brief"],
        "jd_text": "",
        "status": JobStatus.DRAFT.value,
        "owner_agent": "",
        "updated_at": utc_now_iso(),
    }
    rid = ctx.bitable.create_record(jobs_tid, record)
    logger.info(f"写入岗位 draft record_id={rid} job_id={job_id}")


if __name__ == "__main__":
    main()
