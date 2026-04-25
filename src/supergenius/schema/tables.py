"""飞书多维表格结构定义：岗位、简历、事件，及阶段三/四/五扩展表。

字段类型常量见飞书文档。
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


class PipelineStage(str, Enum):
    """简历在初筛之后的前进阶段（仅与 decision/status 配合使用）。"""

    EMPTY = ""
    INTERVIEW_QUEUED = "interview_queued"
    INTERVIEWS_IN_PROGRESS = "interviews_in_progress"
    DEBATE = "debate"
    HM_ARBITRATION = "hm_arbitration"
    OFFER_DRAFTING = "offer_drafting"
    OFFER_SENT = "offer_sent"
    CLOSED = "closed"
    HOLD_REVIEW = "hold_review"


class InterviewRole(str, Enum):
    TECH = "tech"
    BUSINESS = "business"
    CULTURE = "culture"


class InterviewRowStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class DebateStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


class OfferStatus(str, Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    SENT = "sent"
    NEGOTIATE = "negotiate"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    NO_SHOW = "no_show"


class ReportKind(str, Enum):
    WEEKLY = "weekly"
    JD_HEALTH = "jd_health"
    FUNNEL = "funnel"


class EventAction(str, Enum):
    CLAIM = "claim"
    UPDATE = "update"
    EMIT = "emit"
    ERROR = "error"


AGENT_NONE = ""
AGENT_HM = "hiring_manager"
AGENT_JD = "jd_strategist"
AGENT_SCREENER = "screener"
AGENT_INTERVIEW_FANOUT = "interview_fanout"
AGENT_TECH = "tech_interviewer"
AGENT_BUSINESS = "business_interviewer"
AGENT_CULTURE = "culture_interviewer"
AGENT_POST_INTERVIEW = "post_interview"
AGENT_DEBATE = "debate"
AGENT_HM_ARB = "hiring_manager_arbiter"
AGENT_OFFER = "offer_manager"
AGENT_CANDIDATE = "candidate"
AGENT_ANALYST = "analyst"


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
        # 分析师可写入的轻量回写
        FieldDef("jd_suggestion", FieldType.TEXT),
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
        FieldDef("pipeline_stage", FieldType.TEXT),
        FieldDef("interview_bundle_id", FieldType.TEXT),
        FieldDef("debate_round", FieldType.TEXT),
        FieldDef("hm_decision", FieldType.TEXT),
        FieldDef("hm_reason", FieldType.TEXT),
        FieldDef("analyst_note", FieldType.TEXT),
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

# ---------- interviews ----------
INTERVIEWS_TABLE = TableSpec(
    name="interviews",
    primary_field="interview_id",
    fields=[
        FieldDef("interview_id", FieldType.TEXT),
        FieldDef("resume_id", FieldType.TEXT),
        FieldDef("job_id", FieldType.TEXT),
        FieldDef("role", FieldType.TEXT),
        FieldDef("status", FieldType.TEXT),
        FieldDef("total_score", FieldType.NUMBER),
        FieldDef("dimension_json", FieldType.TEXT),
        FieldDef("notes", FieldType.TEXT),
        FieldDef("quote_snippet", FieldType.TEXT),
        FieldDef("owner_agent", FieldType.TEXT),
        FieldDef("updated_at", FieldType.TEXT),
    ],
)

# ---------- debates（一轮一行；speaker 为面试官 agent 名或 hiring_manager_arbiter）----------
DEBATES_TABLE = TableSpec(
    name="debates",
    primary_field="debate_id",
    fields=[
        FieldDef("debate_id", FieldType.TEXT),
        FieldDef("resume_id", FieldType.TEXT),
        FieldDef("round", FieldType.NUMBER),
        FieldDef("speaker_agent", FieldType.TEXT),
        FieldDef("statement", FieldType.TEXT),
        FieldDef("status", FieldType.TEXT),
        FieldDef("ts", FieldType.TEXT),
    ],
)

# ---------- offers ----------
OFFERS_TABLE = TableSpec(
    name="offers",
    primary_field="offer_id",
    fields=[
        FieldDef("offer_id", FieldType.TEXT),
        FieldDef("resume_id", FieldType.TEXT),
        FieldDef("job_id", FieldType.TEXT),
        FieldDef("salary_offer", FieldType.NUMBER),
        FieldDef("status", FieldType.TEXT),
        FieldDef("hm_notes", FieldType.TEXT),
        FieldDef("candidate_message", FieldType.TEXT),
        FieldDef("owner_agent", FieldType.TEXT),
        FieldDef("updated_at", FieldType.TEXT),
    ],
)

# ---------- reports ----------
REPORTS_TABLE = TableSpec(
    name="reports",
    primary_field="report_id",
    fields=[
        FieldDef("report_id", FieldType.TEXT),
        FieldDef("period", FieldType.TEXT),
        FieldDef("kind", FieldType.TEXT),
        FieldDef("content", FieldType.TEXT),
        FieldDef("target_job_id", FieldType.TEXT),
        FieldDef("ts", FieldType.TEXT),
    ],
)


ALL_TABLES: list[TableSpec] = [
    JOBS_TABLE,
    RESUMES_TABLE,
    EVENTS_TABLE,
    INTERVIEWS_TABLE,
    DEBATES_TABLE,
    OFFERS_TABLE,
    REPORTS_TABLE,
]
