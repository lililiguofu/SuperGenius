"""MVP 阶段三张飞书多维表格的结构定义。

- jobs：岗位，从 draft → jd_drafting → jd_pending_approval → open
- resumes：简历，从 new → screening → screened
- events：全局审计（所有 Agent 的读写事件）

字段类型常量参考飞书多维表格文档：
https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/server-docs/docs/bitable-v1/app-table-field/field-description
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FieldType(int, Enum):
    TEXT = 1
    NUMBER = 2
    SINGLE_SELECT = 3
    MULTI_SELECT = 4
    DATETIME = 5
    CHECKBOX = 7
    USER = 11
    PHONE = 13
    URL = 15
    ATTACHMENT = 17


class JobStatus(str, Enum):
    DRAFT = "draft"
    JD_DRAFTING = "jd_drafting"
    JD_PENDING_APPROVAL = "jd_pending_approval"
    OPEN = "open"
    CLOSED = "closed"


class ResumeStatus(str, Enum):
    NEW = "new"
    SCREENING = "screening"
    SCREENED = "screened"


class ResumeDecision(str, Enum):
    PASS_ = "pass"
    HOLD = "hold"
    REJECT = "reject"


class EventAction(str, Enum):
    CLAIM = "claim"
    UPDATE = "update"
    EMIT = "emit"
    ERROR = "error"


AGENT_NONE = ""
AGENT_HM = "hiring_manager"
AGENT_JD = "jd_strategist"
AGENT_SCREENER = "screener"


@dataclass
class FieldDef:
    name: str
    type: FieldType
    ui_type: str | None = None

    def is_primary(self) -> bool:
        return False


@dataclass
class TableSpec:
    name: str
    fields: list[FieldDef]
    primary_field: str

    def field_names(self) -> list[str]:
        return [f.name for f in self.fields]


# ---------- jobs ----------
# 注：飞书多维表格每张表必须有一个"主字段"；创建表时默认首字段为文本，
# 名字沿用 ReqTable 默认即可；我们把 job_id 作为主字段（Text）。

JOBS_TABLE = TableSpec(
    name="jobs",
    primary_field="job_id",
    fields=[
        FieldDef("job_id", FieldType.TEXT),
        FieldDef("title", FieldType.TEXT),
        FieldDef("level", FieldType.TEXT),
        FieldDef("headcount", FieldType.NUMBER),
        FieldDef("budget_min", FieldType.NUMBER),
        FieldDef("budget_max", FieldType.NUMBER),
        FieldDef("urgency", FieldType.TEXT),
        FieldDef("jd_brief", FieldType.TEXT),
        FieldDef("jd_text", FieldType.TEXT),
        FieldDef("status", FieldType.TEXT),
        FieldDef("owner_agent", FieldType.TEXT),
        FieldDef("updated_at", FieldType.TEXT),
    ],
)

# ---------- resumes ----------
RESUMES_TABLE = TableSpec(
    name="resumes",
    primary_field="resume_id",
    fields=[
        FieldDef("resume_id", FieldType.TEXT),
        FieldDef("job_id", FieldType.TEXT),
        FieldDef("candidate_name", FieldType.TEXT),
        FieldDef("raw_text", FieldType.TEXT),
        FieldDef("parsed_skills", FieldType.TEXT),
        FieldDef("score", FieldType.NUMBER),
        FieldDef("decision", FieldType.TEXT),
        FieldDef("reason", FieldType.TEXT),
        FieldDef("status", FieldType.TEXT),
        FieldDef("owner_agent", FieldType.TEXT),
        FieldDef("updated_at", FieldType.TEXT),
    ],
)

# ---------- events ----------
EVENTS_TABLE = TableSpec(
    name="events",
    primary_field="event_id",
    fields=[
        FieldDef("event_id", FieldType.TEXT),
        FieldDef("ts", FieldType.TEXT),
        FieldDef("actor_agent", FieldType.TEXT),
        FieldDef("action", FieldType.TEXT),
        FieldDef("target_table", FieldType.TEXT),
        FieldDef("target_id", FieldType.TEXT),
        FieldDef("payload", FieldType.TEXT),
    ],
)


ALL_TABLES: list[TableSpec] = [JOBS_TABLE, RESUMES_TABLE, EVENTS_TABLE]

# TODO(v2): 面试/辩论/Offer 表在进入阶段三/四时补：
# - interviews(interview_id, resume_id, interviewer_agent, dimension_scores, notes, status)
# - debates(debate_id, resume_id, round, speaker_agent, statement, ts)
# - offers(offer_id, resume_id, salary, status, response_at, ...)
# - reports(report_id, period, kind, content, ts)
