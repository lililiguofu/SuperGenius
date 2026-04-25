"""飞书多维表格 (Bitable) 原子操作封装。

只暴露业务层需要的几个动作，屏蔽 lark-oapi 的 builder 样板：
- 表层：list_tables / create_table / list_fields / create_field
- 记录层：search_records / get_record / create_record / update_record /
          batch_create_records / batch_update_records / delete_record

限流/重试用 tenacity；飞书返回非 0 code 统一抛 BitableError。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import lark_oapi as lark
from lark_oapi.api.bitable.v1 import (
    AppTableField,
    AppTableRecord,
    BatchCreateAppTableRecordRequest,
    BatchCreateAppTableRecordRequestBody,
    BatchUpdateAppTableRecordRequest,
    BatchUpdateAppTableRecordRequestBody,
    Condition,
    CreateAppTableFieldRequest,
    CreateAppTableRecordRequest,
    CreateAppTableRequest,
    CreateAppTableRequestBody,
    DeleteAppTableRecordRequest,
    FilterInfo,
    GetAppTableRecordRequest,
    ListAppTableFieldRequest,
    ListAppTableRequest,
    ReqTable,
    SearchAppTableRecordRequest,
    SearchAppTableRecordRequestBody,
    Sort,
    UpdateAppTableRecordRequest,
)
from loguru import logger
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential


class BitableError(RuntimeError):
    """飞书多维表格 API 返回非 0 code 时抛出。"""

    def __init__(self, code: int, msg: str, method: str) -> None:
        super().__init__(f"[{method}] code={code} msg={msg}")
        self.code = code
        self.msg = msg
        self.method = method


def _check(resp: Any, method: str) -> None:
    if hasattr(resp, "success") and not resp.success():
        code = getattr(resp, "code", -1)
        msg = getattr(resp, "msg", "unknown")
        raise BitableError(code, msg, method)


@dataclass
class TableInfo:
    table_id: str
    name: str
    revision: int | None = None


@dataclass
class FieldInfo:
    field_id: str
    field_name: str
    type: int
    ui_type: str | None = None


@dataclass
class Record:
    record_id: str
    fields: dict[str, Any]


# 通用重试装饰器：飞书偶发 QPS/网络错误时指数退避 3 次
_retry_policy = retry(
    reraise=True,
    retry=retry_if_exception_type((BitableError, ConnectionError, TimeoutError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=8),
)


class BitableClient:
    """对业务层暴露扁平、Python 原生的多维表格操作。"""

    def __init__(self, client: lark.Client, app_token: str) -> None:
        self._client = client
        self._app_token = app_token

    # ---------- 表结构 ----------

    @_retry_policy
    def list_tables(self) -> list[TableInfo]:
        req = ListAppTableRequest.builder().app_token(self._app_token).page_size(100).build()
        resp = self._client.bitable.v1.app_table.list(req)
        _check(resp, "list_tables")
        items = resp.data.items or []
        return [
            TableInfo(table_id=t.table_id, name=t.name, revision=getattr(t, "revision", None))
            for t in items
        ]

    @_retry_policy
    def create_table(self, name: str, default_view_name: str | None = None) -> str:
        # 飞书规则：传了 default_view_name 则必须同时传 fields，否则会 1254001 WrongRequestBody。
        # 这里只传表名，得到「仅含索引列」的空表，再由 bootstrap 的 create_field 补全字段。
        rb = ReqTable.builder().name(name)
        if default_view_name is not None:
            rb = rb.default_view_name(default_view_name)
        body = CreateAppTableRequestBody.builder().table(rb.build()).build()
        req = CreateAppTableRequest.builder().app_token(self._app_token).request_body(body).build()
        resp = self._client.bitable.v1.app_table.create(req)
        _check(resp, "create_table")
        table_id = resp.data.table_id
        logger.info(f"[bitable] 创建表 name={name} table_id={table_id}")
        return table_id

    @_retry_policy
    def list_fields(self, table_id: str) -> list[FieldInfo]:
        req = (
            ListAppTableFieldRequest.builder()
            .app_token(self._app_token)
            .table_id(table_id)
            .page_size(100)
            .build()
        )
        resp = self._client.bitable.v1.app_table_field.list(req)
        _check(resp, "list_fields")
        items = resp.data.items or []
        return [
            FieldInfo(
                field_id=f.field_id,
                field_name=f.field_name,
                type=f.type,
                ui_type=getattr(f, "ui_type", None),
            )
            for f in items
        ]

    @_retry_policy
    def create_field(
        self,
        table_id: str,
        field_name: str,
        field_type: int,
        ui_type: str | None = None,
    ) -> str:
        """创建字段。常用 field_type:
        1=Text, 2=Number, 3=SingleSelect, 4=MultiSelect, 5=DateTime,
        7=Checkbox, 11=User, 13=Phone, 15=URL, 17=Attachment.
        """
        builder = AppTableField.builder().field_name(field_name).type(field_type)
        if ui_type:
            builder = builder.ui_type(ui_type)
        req = (
            CreateAppTableFieldRequest.builder()
            .app_token(self._app_token)
            .table_id(table_id)
            .request_body(builder.build())
            .build()
        )
        resp = self._client.bitable.v1.app_table_field.create(req)
        _check(resp, "create_field")
        return resp.data.field.field_id

    # ---------- 记录 ----------

    @_retry_policy
    def search_records(
        self,
        table_id: str,
        filter_conditions: list[dict[str, Any]] | None = None,
        conjunction: str = "and",
        sort: list[dict[str, Any]] | None = None,
        page_size: int = 100,
    ) -> list[Record]:
        """查询记录；filter_conditions 样例：
        [{"field_name": "status", "operator": "is", "value": ["draft"]}]
        """
        body_builder = SearchAppTableRecordRequestBody.builder().automatic_fields(False)
        if filter_conditions:
            conds = [
                Condition.builder()
                .field_name(c["field_name"])
                .operator(c["operator"])
                .value(c.get("value", []))
                .build()
                for c in filter_conditions
            ]
            filter_info = (
                FilterInfo.builder().conjunction(conjunction).conditions(conds).build()
            )
            body_builder = body_builder.filter(filter_info)
        if sort:
            sorts = [
                Sort.builder()
                .field_name(s["field_name"])
                .desc(bool(s.get("desc", False)))
                .build()
                for s in sort
            ]
            body_builder = body_builder.sort(sorts)
        body = body_builder.build()

        req = (
            SearchAppTableRecordRequest.builder()
            .app_token(self._app_token)
            .table_id(table_id)
            .page_size(page_size)
            .request_body(body)
            .build()
        )
        resp = self._client.bitable.v1.app_table_record.search(req)
        _check(resp, "search_records")
        items = resp.data.items or []
        return [Record(record_id=r.record_id, fields=dict(r.fields or {})) for r in items]

    @_retry_policy
    def get_record(self, table_id: str, record_id: str) -> Record:
        req = (
            GetAppTableRecordRequest.builder()
            .app_token(self._app_token)
            .table_id(table_id)
            .record_id(record_id)
            .build()
        )
        resp = self._client.bitable.v1.app_table_record.get(req)
        _check(resp, "get_record")
        r = resp.data.record
        return Record(record_id=r.record_id, fields=dict(r.fields or {}))

    @_retry_policy
    def create_record(self, table_id: str, fields: dict[str, Any]) -> str:
        rec = AppTableRecord.builder().fields(fields).build()
        req = (
            CreateAppTableRecordRequest.builder()
            .app_token(self._app_token)
            .table_id(table_id)
            .request_body(rec)
            .build()
        )
        resp = self._client.bitable.v1.app_table_record.create(req)
        _check(resp, "create_record")
        return resp.data.record.record_id

    @_retry_policy
    def update_record(self, table_id: str, record_id: str, fields: dict[str, Any]) -> None:
        rec = AppTableRecord.builder().fields(fields).build()
        req = (
            UpdateAppTableRecordRequest.builder()
            .app_token(self._app_token)
            .table_id(table_id)
            .record_id(record_id)
            .request_body(rec)
            .build()
        )
        resp = self._client.bitable.v1.app_table_record.update(req)
        _check(resp, "update_record")

    @_retry_policy
    def batch_create_records(self, table_id: str, records: list[dict[str, Any]]) -> list[str]:
        recs = [AppTableRecord.builder().fields(f).build() for f in records]
        body = BatchCreateAppTableRecordRequestBody.builder().records(recs).build()
        req = (
            BatchCreateAppTableRecordRequest.builder()
            .app_token(self._app_token)
            .table_id(table_id)
            .request_body(body)
            .build()
        )
        resp = self._client.bitable.v1.app_table_record.batch_create(req)
        _check(resp, "batch_create_records")
        return [r.record_id for r in (resp.data.records or [])]

    @_retry_policy
    def batch_update_records(
        self, table_id: str, updates: list[tuple[str, dict[str, Any]]]
    ) -> None:
        recs = [
            AppTableRecord.builder().record_id(rid).fields(f).build() for rid, f in updates
        ]
        body = BatchUpdateAppTableRecordRequestBody.builder().records(recs).build()
        req = (
            BatchUpdateAppTableRecordRequest.builder()
            .app_token(self._app_token)
            .table_id(table_id)
            .request_body(body)
            .build()
        )
        resp = self._client.bitable.v1.app_table_record.batch_update(req)
        _check(resp, "batch_update_records")

    @_retry_policy
    def delete_record(self, table_id: str, record_id: str) -> None:
        req = (
            DeleteAppTableRecordRequest.builder()
            .app_token(self._app_token)
            .table_id(table_id)
            .record_id(record_id)
            .build()
        )
        resp = self._client.bitable.v1.app_table_record.delete(req)
        _check(resp, "delete_record")


__all__ = ["BitableClient", "BitableError", "TableInfo", "FieldInfo", "Record"]
