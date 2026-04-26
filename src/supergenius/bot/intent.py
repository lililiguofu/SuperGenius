"""意图识别：从用户自然语言解析 intent + 招聘职位参数。

飞书群聊里 @ 机器人时，正文常带 `<at user_id="...">...</at>`，不能简单按首字符 `@` 截断。
"""

from __future__ import annotations

import re
from typing import Any

_INTENT_SCHEMA = {
    "name": "bot_intent",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "intent": {
                "type": "string",
                "enum": ["create_job", "query_status", "help", "other"],
            },
            "title": {"type": "string"},
            "level": {"type": "string"},
            "headcount": {"type": "integer"},
            "budget_min": {"type": "integer"},
            "budget_max": {"type": "integer"},
            "urgency": {"type": "string"},
            "jd_brief": {"type": "string"},
        },
        "required": [
            "intent",
            "title",
            "level",
            "headcount",
            "budget_min",
            "budget_max",
            "urgency",
            "jd_brief",
        ],
        "additionalProperties": False,
    },
}

_SYSTEM = """\
你是 SuperGenius 招聘系统的消息解析助手。
根据用户消息判断 intent，并尽量提取职位参数：

intent 枚举：
- create_job   用户要开一个新岗位/职位（包含如「招一名…」「我们需要…工程师」等）
- query_status 用户询问进度、结果、状态
- help         用户要帮助/使用说明
- other        无法归入以上三类

字段规则：
- 数字字段无法确定时填 0
- urgency 未提及时填 "normal"，明确紧急填 "urgent"，不急填 "low"
- title / level / jd_brief 不确定时填空串
"""

_EMPTY: dict[str, Any] = {
    "intent": "other",
    "title": "",
    "level": "",
    "headcount": 1,
    "budget_min": 0,
    "budget_max": 0,
    "urgency": "normal",
    "jd_brief": "",
}


def parse_intent(llm: Any, text: str) -> dict[str, Any]:
    """调用 LLM 解析用户意图与职位参数；失败时返回 intent=other。"""
    try:
        out = llm.chat(
            system=_SYSTEM,
            user=text[:2000],
            json_schema=_INTENT_SCHEMA,
            temperature=0.0,
        )
        if isinstance(out, dict) and out.get("intent") in (
            "create_job",
            "query_status",
            "help",
            "other",
        ):
            return out
    except Exception:
        pass
    return dict(_EMPTY)


# --- 飞书 @ / <at> 清洗 ---

_RE_AT_TAG = re.compile(r"<at[^>]*>.*?</at>", re.IGNORECASE | re.DOTALL)
_RE_AT_PLAIN = re.compile(r"@[^\s@]{1,64}\s*")


def strip_feishu_mention_noise(text: str) -> str:
    """去掉群聊里 @ 机器人产生的 `<at>...</at>` 和 `@昵称 ` 残留，只保留人话。"""
    t = (text or "").strip()
    t = _RE_AT_TAG.sub(" ", t)
    t = _RE_AT_PLAIN.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


# --- 规则回退：LLM 未识别或判成 other 时，常见「开岗」仍要走创建 ---

_RE_P_LEVEL = re.compile(r"[Pp]([1-8])")
_RE_WAN_RANGE = re.compile(
    r"(\d+(?:\.\d+)?)\s*[到至\-~～]\s*(\d+(?:\.\d+)?)\s*万",
)
_RE_WAN_ONE = re.compile(r"(\d+(?:\.\d+)?)\s*万")
_RE_PERSON = re.compile(r"(\d+)\s*人")


def looks_like_create_job(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 3:
        return False
    hot = ("招一名", "招聘", "诚聘", "急聘", "社招", "在招", "开岗", "开新岗", "急招", "招个", "招位", "HC", "hc")
    if any(h in t for h in hot):
        return True
    if "招" in t and re.search(r"(工程|开发|产品|设计|运营|程序|经理|研发|测试|数据)", t):
        return True
    if re.search(r"需要.*(人|位|名|个)", t) and re.search(r"(工程|开发|产品|设计|运营)", t):
        return True
    return False


def heuristic_create_job_from_text(text: str) -> dict[str, Any]:
    """从自然语言里抠岗位字段；用于 LLM 失败或判成 other 时的回退。"""
    t = (text or "").strip()
    base = dict(_EMPTY)
    base["intent"] = "create_job"
    base["headcount"] = 1
    m = _RE_P_LEVEL.search(t)
    base["level"] = f"P{m.group(1)}" if m else ""

    pm = _RE_PERSON.search(t)
    if pm:
        try:
            base["headcount"] = max(1, int(pm.group(1)))
        except ValueError:
            pass

    bmin, bmax = 0, 0
    wr = _RE_WAN_RANGE.search(t)
    if wr:
        bmin = int(float(wr.group(1)) * 10_000)
        bmax = int(float(wr.group(2)) * 10_000)
    else:
        wo = list(_RE_WAN_ONE.finditer(t))
        if len(wo) >= 2:
            bmin = int(float(wo[0].group(1)) * 10_000)
            bmax = int(float(wo[1].group(1)) * 10_000)
        elif len(wo) == 1:
            v = int(float(wo[0].group(1)) * 10_000)
            bmin, bmax = v, v
    base["budget_min"] = bmin
    base["budget_max"] = bmax

    if any(x in t for x in ("急", "紧", "urgent", "ASAP", "asap")):
        base["urgency"] = "urgent"
    else:
        base["urgency"] = "normal"

    # 职位标题：取「招」后面一小段 或 第一个典型岗位词
    title = "新岗位"
    m_title = re.search(r"招(?:聘|一名|个|位)?\s*([^\d，,。；;！!]{2,32})", t)
    if m_title:
        title = m_title.group(1).strip()
    for junk in ("一名", "一个", "位", "名"):
        if title.startswith(junk):
            title = title[len(junk) :].strip()
    title = title.strip()
    if not title or len(title) < 2:
        m_eng = re.search(r"([\u4e00-\u9fa5A-Za-z·]{2,20}(?:工程|开发|产品|设计|运营|经理|员))", t)
        title = m_eng.group(1) if m_eng else "招聘岗位"
    base["title"] = title[:64]
    base["jd_brief"] = t[:500]
    return base
