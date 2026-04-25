"""飞书多维表格 schema 定义（表、字段、状态枚举）。"""

from supergenius.schema.tables import (
    EVENTS_TABLE,
    JOBS_TABLE,
    RESUMES_TABLE,
    EventAction,
    FieldDef,
    FieldType,
    JobStatus,
    ResumeDecision,
    ResumeStatus,
    TableSpec,
)

__all__ = [
    "EventAction",
    "EVENTS_TABLE",
    "FieldDef",
    "FieldType",
    "JOBS_TABLE",
    "JobStatus",
    "RESUMES_TABLE",
    "ResumeDecision",
    "ResumeStatus",
    "TableSpec",
]
