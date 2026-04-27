"""飞书机器人消息主处理器。

支持的交互：
  1. 文本：「招一名 Python 后端工程师，P5，预算 2-3 万/月，紧急」→ 创建岗位草稿
  2. 文件（.txt/.pdf/.docx）：直接解析成简历，投递到最新 open 岗位
  3. 文本：「查进度」/ 「状态」→ 当前流水线汇总
  4. 「帮助」→ 使用说明
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
import uuid
from typing import Any

from loguru import logger

from supergenius.agents.base import utc_now_iso
from supergenius.bot.file_ingest import download_and_parse
from supergenius.bot.intent import (
    heuristic_create_job_from_text,
    looks_like_create_job,
    parse_intent,
    strip_feishu_mention_noise,
)
from supergenius.bot.messenger import send_text
from supergenius.feishu.field_value import feishu_text_to_str
from supergenius.schema.tables import JobStatus, ResumeStatus

_HELP = """\
SuperGenius 招聘助手使用说明

【开岗位】
直接告诉我要招什么人，例如：
  「招一名 Java 后端工程师，P5，预算 2-3 万/月，比较紧急」
系统自动生成岗位草稿 → JD 策划官起草 JD → 经理审批后岗位开放。

【投递简历】
把简历文件（.txt / .pdf / .docx）直接发到这里。
可一次多份；先合并成一条「已收到」，再依次处理。
只会投到本对话**当前绑定**的 `open` 岗位（最后一次在此对话「开岗」或「绑定岗位 J-…」），
不会自动混到别的在招岗。要换目标发：「绑定岗位 J-你的岗位ID」。
岗位开放后，简历进入初筛 → 面试 → 辩论 → 仲裁 → Offer，有结果会通知。

【查看进度】
发送「查进度」或「最新状态」可看全局汇总。
详细记录请在飞书多维表格（Bitable）的各张表里查看。

【公平性说明】
仲裁环节有性别反事实双评；若结论不一致会自动转人工审核并通知你。\
"""

_QUERY_KEYWORDS = ("进度", "状态", "结果", "筛到哪", "最新", "overview", "status")
_HELP_KEYWORDS = ("帮助", "help", "怎么用", "使用说明", "指令")

_STAGE_ZH: dict[str, str] = {
    "": "新投递",
    "new": "新投递",
    "screening": "初筛中",
    "screened": "初筛完",
    "interview_queued": "等待面试",
    "interviews_in_progress": "面试中",
    "debate": "辩论中",
    "hm_arbitration": "经理仲裁",
    "offer_drafting": "起草 Offer",
    "offer_sent": "Offer 已发",
    "offer_negotiation": "谈薪中",
    "talent_pool": "人才池",
    "hold_review": "待人工复核",
    "closed": "已关闭",
}


class BotHandler:
    def __init__(
        self,
        ctx: Any,
        lark_client: Any,
        watcher: Any,
    ) -> None:
        self._ctx = ctx
        self._client = lark_client
        self._watcher = watcher
        self._lock = threading.Lock()
        # chat_id -> last job_id the chat is working with
        self._chat_job: dict[str, str] = {}
        # chat_id -> list of resume_ids submitted in this session
        self._chat_resumes: dict[str, list[str]] = {}
        # 飞书可能重复投递同一 message_id，或同一条以不同 id 再推，避免同一条回多条
        self._seen_message_ids: set[str] = set()
        # (chat+发送者+归一化正文) 的幂等：同一条在飞书里可能用不同 message_id 推多次
        self._recent_bubble_key_at: dict[str, float] = {}
        # 同一用户刚发过的长句，用于截断/子串的重复事件（少见平台拆包）
        self._last_speech_by_user: dict[str, tuple[str, float]] = {}
        # 连续发多份简历：先攒包，再统一发一条「已收到」
        self._file_batch_lock = threading.Lock()
        self._file_batch_pending: dict[str, list[tuple[str, str, str]]] = {}
        self._file_batch_timer: dict[str, threading.Timer] = {}
        self._file_batch_debounce_sec: float = 1.6

    # ---------- public entry point ----------

    def on_message(self, event_data: Any) -> None:
        """处理飞书 P2ImMessageReceiveV1 事件。"""
        try:
            msg = event_data.event.message
            # 只按 message_id 去重。同一轮「一句话 + 多附件」在飞书侧常**共用 root_id**，
            # 若把 root 也入集，会误杀第 2 条起的文件/后续事件，表现像「机器人没反应」。
            mid = (msg.message_id or "").strip()
            if mid:
                with self._lock:
                    if mid in self._seen_message_ids:
                        logger.debug(f"[bot] 跳过重复 message_id={mid!r}")
                        return
                    self._seen_message_ids.add(mid)
                    if len(self._seen_message_ids) > 8_000:
                        self._seen_message_ids.clear()

            ev = event_data.event
            sender_key = "unknown"
            if getattr(ev, "sender", None) and ev.sender and ev.sender.sender_id:
                u = ev.sender.sender_id
                sender_key = (u.open_id or u.user_id or u.union_id or sender_key)[:64]

            chat_id: str = msg.chat_id
            msg_type: str = msg.message_type
            chat_t = getattr(msg, "chat_type", None) or "?"
            logger.info(
                f"[bot] im.message 入站 chat_type={chat_t!r} msg_type={msg_type!r} "
                f"mid={mid!r} chat_id={chat_id!r}"
            )

            import json as _json

            content: dict = _json.loads(msg.content or "{}")

            if msg_type == "text":
                text = content.get("text", "").strip()
                # 群聊 @ 机器人：<at user_id=...>姓名</at> 与 @昵称，必须整段清洗
                text = strip_feishu_mention_noise(text)
                if not text:
                    logger.debug("[bot] 纯 @ 无正文，忽略（避免多事件里只有 at 的噪音）")
                    return
                if self._should_skip_spam_bubble(chat_id, sender_key, text):
                    logger.info(f"[bot] 幂等跳过重复/近重复气泡 chat={chat_id!r} sender={sender_key!r}")
                    return
                self._handle_text(chat_id, text)

            elif msg_type == "file":
                self._enqueue_file(
                    chat_id=chat_id,
                    sender_key=sender_key,
                    message_id=msg.message_id or "",
                    file_key=content.get("file_key", ""),
                    file_name=content.get("file_name", "resume.txt"),
                )

            else:
                send_text(
                    self._client,
                    chat_id,
                    f"(暂不支持 {msg_type} 类型，请发送文字或文件。)",
                )
        except Exception as exc:
            logger.exception(f"[bot] on_message 异常: {exc}")
            # 已开通单聊权限时事件能到、但飞书端仍无气泡，多半是：本机请求 open.feishu.cn 超时、
            # 或多维表格字段与代码不一致（FieldNameNotFound）。给用户一句可见回执，避免「完全静默」。
            try:
                em = event_data.event.message
                cid = (em.chat_id or "").strip() if em else ""
                if cid:
                    brief = str(exc)[:450]
                    ok = send_text(
                        self._client,
                        cid,
                        "处理本条消息时出错（机器人已收到事件）。请重试，或看运行窗口日志。\n"
                        f"摘要：{brief}",
                    )
                    if not ok:
                        logger.error(
                            "[bot] 异常回执也发送失败，请检查本机到 open.feishu.cn 的网络/代理（终端常见 ConnectTimeout）。"
                        )
            except Exception:
                logger.debug("[bot] 无法发送异常回执", exc_info=True)

    def _should_skip_spam_bubble(self, chat_id: str, sender_key: str, text: str) -> bool:
        """同一条群消息在飞书侧常产生多条事件（message_id 不同），用正文幂等 + 子串去抖。"""
        now = time.monotonic()
        norm = re.sub(r"\s+", " ", (text or "").strip())
        if not norm:
            return True
        sk = f"{chat_id}\0{sender_key}"
        hkey = hashlib.sha256(f"{sk}\0{norm}".encode()).hexdigest()[:32]
        with self._lock:
            t0 = self._recent_bubble_key_at.get(hkey)
            if t0 is not None and now - t0 < 16.0:
                return True
            self._recent_bubble_key_at[hkey] = now
            for k, t1 in list(self._recent_bubble_key_at.items()):
                if now - t1 > 50.0:
                    del self._recent_bubble_key_at[k]
            if sk in self._last_speech_by_user:
                prev, pt = self._last_speech_by_user[sk]
                if (
                    now - pt < 6.0
                    and len(prev) > 30
                    and 0 < len(norm) < 20
                    and norm != prev
                    and norm in prev
                ):
                    return True
            self._last_speech_by_user[sk] = (norm, now)
        return False

    # ---------- text ----------

    def _handle_text(self, chat_id: str, text: str) -> None:
        if not text:
            send_text(self._client, chat_id, _HELP)
            return

        if self._try_bind_job_command(chat_id, text):
            return

        lower = text.lower()
        if any(k in lower for k in _HELP_KEYWORDS):
            send_text(self._client, chat_id, _HELP)
            return
        if any(k in lower for k in _QUERY_KEYWORDS):
            self._query_status(chat_id)
            return

        result = parse_intent(self._ctx.llm, text)
        intent = result.get("intent", "other")
        if intent == "other" and looks_like_create_job(text):
            result = heuristic_create_job_from_text(text)
            intent = "create_job"
            logger.info(f"[bot] 意图用规则回退为 create_job: {text[:120]!r}")

        if intent == "create_job":
            self._create_job(chat_id, result)
        elif intent == "query_status":
            self._query_status(chat_id)
        elif intent == "help":
            send_text(self._client, chat_id, _HELP)
        else:
            send_text(
                self._client,
                chat_id,
                "没太明白你的意思，可以：\n"
                "· 直接描述岗位（如「招一名 XX 工程师…」）\n"
                "· 发简历文件（.txt / .pdf / .docx）\n"
                "· 发「帮助」查看完整说明",
            )

    # ---------- file ----------

    def _enqueue_file(
        self,
        chat_id: str,
        sender_key: str,
        message_id: str,
        file_key: str,
        file_name: str,
    ) -> None:
        """飞书连发多文件时各自一条事件；攒包约 1.6s 无新文件后统一发「已收到」再依次处理。"""
        key = f"{chat_id}\0{sender_key}"
        with self._file_batch_lock:
            self._file_batch_pending.setdefault(key, []).append(
                (message_id, file_key, file_name)
            )
            old = self._file_batch_timer.pop(key, None)
            if old is not None:
                old.cancel()
            t = threading.Timer(
                self._file_batch_debounce_sec,
                self._flush_file_batch,
                args=(key,),
            )
            t.daemon = True
            t.start()
            self._file_batch_timer[key] = t

    def _flush_file_batch(self, key: str) -> None:
        with self._file_batch_lock:
            items = self._file_batch_pending.pop(key, None)
            self._file_batch_timer.pop(key, None)
        if not items:
            return
        parts = key.split("\0", 1)
        chat_id = parts[0]
        if len(parts) < 2:
            return
        names = [x[2] for x in items]
        if len(names) == 1:
            send_text(
                self._client,
                chat_id,
                f"已收到 1 个文件「{names[0]}」，开始依次拉取与解析。",
            )
        else:
            blist = "\n".join(f"· {n}" for n in names)
            send_text(
                self._client,
                chat_id,
                f"已收到 {len(names)} 个文件：\n{blist}\n开始依次拉取与解析。",
            )
        # 多份简历时「无 open 岗位」的长说明只发一次，避免刷屏
        no_open_tutorial_shown: list[bool] = [False]
        for message_id, file_key, file_name in items:
            try:
                self._process_file_payload(
                    chat_id,
                    message_id,
                    file_key,
                    file_name,
                    no_open_tutorial_shown=no_open_tutorial_shown,
                )
            except Exception as exc:
                logger.exception(f"[bot] 处理文件 {file_name!r}: {exc}")
                send_text(
                    self._client,
                    chat_id,
                    f"「{file_name}」处理异常，请重发或看本机日志：\n{str(exc)[:400]}",
                )

    def _process_file_payload(
        self,
        chat_id: str,
        message_id: str,
        file_key: str,
        file_name: str,
        *,
        no_open_tutorial_shown: list[bool] | None = None,
    ) -> None:
        text = download_and_parse(self._client, message_id, file_key, file_name)
        if not text or text.strip().startswith("["):
            reason = (text or "").strip() or "未知（请看机器人所在终端的 [file_ingest] 日志）"
            send_text(
                self._client,
                chat_id,
                f"「{file_name}」拉取/解析失败：\n{reason}\n"
                f"可隔几秒**单独重发**该份。",
            )
            return

        nchars = len(text.strip())
        job_id = self._resolve_open_job(chat_id)
        if not job_id:
            if no_open_tutorial_shown is not None and no_open_tutorial_shown[0]:
                send_text(
                    self._client,
                    chat_id,
                    f"「{file_name}」{nchars} 字，仍无 `open` 投递目标，未入库（说明见上一条）。",
                )
                return
            send_text(
                self._client,
                chat_id,
                f"「{file_name}」解析成功（{nchars} 字），检查「开放中 open」岗位…",
            )
            lines = [
                "本对话**还没有「已绑定 + open」的投递目标**，简历先不入库（避免和别的在招岗混投）。",
                "",
                "做法：",
                "· 在这里**开新岗**并等 `jobs` 里变为 `open`；或",
                "· 表格里已开好岗、状态已是 `open` 时，发一句：**绑定岗位 J-你的ID** 指定投哪一岗。",
            ]
            with self._lock:
                jlast = self._chat_job.get(chat_id)
            if jlast:
                rows = self._ctx.bitable.search_records(
                    self._ctx.table_ids["jobs"],
                    filter_conditions=[
                        {"field_name": "job_id", "operator": "is", "value": [jlast]}
                    ],
                    page_size=1,
                )
                if rows:
                    st = feishu_text_to_str(rows[0].fields.get("status")) or "?"
                    lines += [
                        "",
                        f"当前绑定/最近开岗为 `{jlast}`，`status`={st!r}；"
                        f"为 `open` 后无需改绑，直接再发简历即可。",
                    ]
            else:
                lines += ["", "新对话没有默认岗位；若有多条在招，务必先「绑定岗位 J-…」。"]
            send_text(
                self._client,
                chat_id,
                "\n".join(lines),
            )
            if no_open_tutorial_shown is not None:
                no_open_tutorial_shown[0] = True
            return

        send_text(
            self._client,
            chat_id,
            f"「{file_name}」解析成功（{nchars} 字），检查「开放中 open」岗位…",
        )

        name = _extract_name(text) or file_name.rsplit(".", 1)[0]
        resume_id = f"R-{uuid.uuid4().hex[:10]}"

        self._ctx.bitable.create_record(
            self._ctx.table_ids["resumes"],
            {
                "resume_id": resume_id,
                "job_id": job_id,
                "candidate_name": name,
                "raw_text": text[:30000],
                "parsed_skills": "",
                "score": 0,
                "decision": "",
                "reason": "",
                "status": ResumeStatus.NEW.value,
                "owner_agent": "",
                "updated_at": utc_now_iso(),
                "pipeline_stage": "",
                "interview_bundle_id": "",
                "debate_round": "0",
                "hm_decision": "",
                "hm_reason": "",
                "analyst_note": "",
                "personality": "steady",
                "gender": "",
            },
        )
        logger.info(f"[bot] 创建简历 {resume_id} ({name}) -> job {job_id}")

        with self._lock:
            self._chat_resumes.setdefault(chat_id, []).append(resume_id)
            all_ids = list(self._chat_resumes[chat_id])

        self._watcher.register(f"chat_{chat_id}", chat_id, all_ids)

        send_text(
            self._client,
            chat_id,
            f"「{file_name}」已写入 `resumes`：「{name}」  resume_id=`{resume_id}`  岗位={job_id}\n"
            f"已登记进度追踪；请保持 `run_mvp` 运行。本批全部结束后会发**一份总报告**"
            f"（`FEISHU_BOT_REPORT_DIR` 可写本地 Markdown；`FEISHU_BOT_NOTIFY_STEPS=0` 可关阶段刷屏）。",
        )

    # ---------- create job ----------

    def _create_job(self, chat_id: str, params: dict) -> None:
        title: str = params.get("title") or "新岗位"
        level: str = params.get("level") or ""
        headcount: int = max(int(params.get("headcount") or 1), 1)
        b_min: int = int(params.get("budget_min") or 0)
        b_max: int = int(params.get("budget_max") or 0)
        urgency: str = params.get("urgency") or "normal"
        jd_brief: str = params.get("jd_brief") or title

        job_id = f"J-{uuid.uuid4().hex[:8]}"
        self._ctx.bitable.create_record(
            self._ctx.table_ids["jobs"],
            {
                "job_id": job_id,
                "title": title,
                "level": level,
                "headcount": headcount,
                "budget_min": b_min,
                "budget_max": b_max,
                "urgency": urgency,
                "jd_brief": jd_brief,
                "jd_text": "",
                "status": JobStatus.DRAFT.value,
                "owner_agent": "",
                "updated_at": utc_now_iso(),
                "jd_suggestion": "",
            },
        )
        logger.info(f"[bot] 创建岗位 {job_id} ({title})")

        with self._lock:
            self._chat_job[chat_id] = job_id

        budget_str = f"{b_min}-{b_max}" if (b_min or b_max) else "待定"
        send_text(
            self._client,
            chat_id,
            f"岗位已创建！\n\n"
            f"职位：{title}（{level}）\n"
            f"人数：{headcount}\n"
            f"预算：{budget_str}\n"
            f"紧急程度：{urgency}\n"
            f"岗位 ID：{job_id}\n\n"
            f"本对话的投递目标已指向上述 ID；**你之后再在此开新岗，以最后一次为准**，不会和旧岗混投。\n"
            f"若要从表格里改绑别的 `open` 岗，可发：绑定岗位 J-某ID\n"
            f"JD 策划官已起草，待岗位在表里变为 `open` 后即可发简历。",
        )

    # ---------- query ----------

    def _try_bind_job_command(self, chat_id: str, text: str) -> bool:
        """若消息为「绑定/使用/投递 岗位 J-xxx」则绑定本对话的投递目标。"""
        m = re.match(
            r"^\s*(?:绑定|使用|投递)岗位\s*[：:\s]*\s*([A-Za-z0-9-]+)\s*$",
            text.strip(),
            re.IGNORECASE,
        )
        if not m:
            return False
        raw = m.group(1).strip()
        m2 = re.match(r"(?i)^J-(.+)$", raw)
        if m2:
            job_n = f"J-{m2.group(1).strip().lstrip('-/ ')}"
        else:
            job_n = f"J-{raw.lstrip('jJ-/ ')}"
        self._bind_job_to_chat_by_id(chat_id, job_n)
        return True

    def _bind_job_to_chat_by_id(self, chat_id: str, job_id: str) -> None:
        jobs_tid = self._ctx.table_ids["jobs"]
        rows = self._ctx.bitable.search_records(
            jobs_tid,
            filter_conditions=[
                {"field_name": "job_id", "operator": "is", "value": [job_id]}
            ],
            page_size=2,
        )
        if not rows:
            send_text(
                self._client,
                chat_id,
                f"未找到 job_id={job_id} 的岗位。请检查多维表格 jobs 表或开岗时复制的 ID。",
            )
            return
        st = feishu_text_to_str(rows[0].fields.get("status"))
        if st != JobStatus.OPEN.value:
            send_text(
                self._client,
                chat_id,
                f"岗位 `{job_id}` 当前不是「开放中 open」（现为 {st!r}），无法作为投递目标。\n"
                f"请先在表里把 `status` 改为 `open`，或等流程推进。",
            )
            return
        with self._lock:
            self._chat_job[chat_id] = job_id
        send_text(
            self._client,
            chat_id,
            f"已绑定本对话的投递目标：`{job_id}`\n"
            f"之后发的简历**只**会记在这一岗下，不会投到历史其它岗。",
        )
        logger.info(f"[bot] 会话 {chat_id!r} 绑定岗位 {job_id}")

    def _query_status(self, chat_id: str) -> None:
        rtid = self._ctx.table_ids["resumes"]
        resumes = self._ctx.bitable.search_records(
            rtid, filter_conditions=None, page_size=200
        )
        if not resumes:
            send_text(self._client, chat_id, "暂无任何简历记录。")
            return

        counts: dict[str, int] = {}
        for r in resumes:
            stage = (
                feishu_text_to_str(r.fields.get("pipeline_stage"))
                or feishu_text_to_str(r.fields.get("status"))
                or "new"
            )
            counts[stage] = counts.get(stage, 0) + 1

        lines = [f"当前共 {len(resumes)} 份简历：\n"]
        for stage, cnt in sorted(counts.items(), key=lambda x: -x[1]):
            label = _STAGE_ZH.get(stage, stage)
            lines.append(f"  {label}：{cnt} 份")

        send_text(self._client, chat_id, "\n".join(lines))

    # ---------- utils ----------

    def _resolve_open_job(self, chat_id: str) -> str | None:
        """只认本对话绑定的 `job_id` 且为 open；不自动「捡」任意在招岗，避免新旧混投。

        绑定来源：① 本对话里最后一次「开岗」；② 用户发「绑定岗位 J-…」。
        """
        jobs_tid = self._ctx.table_ids["jobs"]
        with self._lock:
            cached = self._chat_job.get(chat_id)

        if not cached:
            return None
        rows = self._ctx.bitable.search_records(
            jobs_tid,
            filter_conditions=[
                {"field_name": "job_id", "operator": "is", "value": [cached]}
            ],
            page_size=2,
        )
        if not rows:
            return None
        if feishu_text_to_str(rows[0].fields.get("status")) == JobStatus.OPEN.value:
            return cached
        return None


def _extract_name(text: str) -> str | None:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("姓名"):
            parts = line.split("：", 1) if "：" in line else line.split(":", 1)
            if len(parts) == 2 and parts[1].strip():
                return parts[1].strip()
    return None
