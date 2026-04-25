"""模拟候选人投递：把 fixtures/resumes/*.txt 批量写入 resumes 表。

每份简历绑定到 job_brief.json 里声明的 job_id。Screener 会自动接手打分。
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from loguru import logger  # noqa: E402

from supergenius.agents.base import utc_now_iso  # noqa: E402
from supergenius.runtime import boot  # noqa: E402
from supergenius.schema.tables import ResumeStatus  # noqa: E402

FIXTURE_DIR = ROOT / "fixtures" / "resumes"
BRIEF = ROOT / "fixtures" / "job_brief.json"


def main() -> None:
    settings, ctx = boot()

    brief = json.loads(BRIEF.read_text(encoding="utf-8"))
    job_id = brief["job_id"]
    resumes_tid = ctx.table_ids["resumes"]

    files = sorted(FIXTURE_DIR.glob("*.txt"))
    if not files:
        logger.warning(f"{FIXTURE_DIR} 下没有 .txt 简历")
        return

    records = []
    for f in files:
        raw = f.read_text(encoding="utf-8")
        name = _extract_name(raw) or f.stem
        records.append(
            {
                "resume_id": f"R-{uuid.uuid4().hex[:8]}",
                "job_id": job_id,
                "candidate_name": name,
                "raw_text": raw,
                "parsed_skills": "",
                "score": 0,
                "decision": "",
                "reason": "",
                "status": ResumeStatus.NEW.value,
                "owner_agent": "",
                "updated_at": utc_now_iso(),
            }
        )

    ids = ctx.bitable.batch_create_records(resumes_tid, records)
    logger.info(f"投递 {len(ids)} 份简历到 job_id={job_id}")


def _extract_name(text: str) -> str | None:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("姓名"):
            parts = line.split("：", 1) if "：" in line else line.split(":", 1)
            if len(parts) == 2:
                return parts[1].strip()
    return None


if __name__ == "__main__":
    main()
