"""后台线程：轮询简历流水线，向飞书汇报。

- 阶段推送：`FEISHU_BOT_NOTIFY_STEPS=1` 时每阶段变化推一条短讯（可关，避免刷屏）。
- 终态：默认不每人一条，**攒到本批全部终态**后发送 **一份报告**（含「一眼看板」分栏 + 详单），写入 `reports/` 时同时生成 **HTML 看板**（浏览器打开，比多维表更直观）。
- 若需旧行为：设 `FEISHU_BOT_NOTIFY_EACH_TERMINAL=1`。
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from html import escape
from typing import Any

from loguru import logger

from supergenius.bot.messenger import send_text
from supergenius.config import ROOT
from supergenius.feishu.field_value import feishu_text_to_str

_TERMINAL_STAGES = frozenset(
    {"closed", "offer_sent", "offer_negotiation", "talent_pool", "hold_review"}
)

_STAGE_ZH: dict[str, str] = {
    "": "刚入库/待初筛",
    "new": "新投递",
    "screening": "初筛中",
    "screened": "初筛完成",
    "interview_queued": "排队面试",
    "interviews_in_progress": "面试中",
    "debate": "辩论中",
    "hm_arbitration": "经理仲裁中",
    "offer_drafting": "起草 Offer",
    "offer_sent": "Offer 已发",
    "offer_negotiation": "谈薪中",
    "talent_pool": "入人才库",
    "hold_review": "待人工复核",
    "closed": "已关闭",
}

_SUMMARY_STAGES: dict[str, str] = {
    "offer_sent": "[录用] 已发 Offer",
    "offer_negotiation": "[谈薪] 候选人在谈薪，等待进一步沟通",
    "talent_pool": "🧡【人才库】已入人才库（可在此行查看/后续新岗可能再次匹配）",
    "hold_review": "[人工审核] 流程触发人工复核（如公平性告警）",
    "closed": "[结束] 流程已关闭",
}

# 飞书 msg_type=text 不渲染 Markdown，** 会原样显示；用框线+重复 emoji 做「高亮」
_TALENT_BADGE = "🧡🧡🧡【人才库·本批重点】🧡🧡🧡"
_TALENT_RULE = "════════════════════════════════════"

# 看板分栏：按终态聚类（比多维表「扫网格」好读）
_KANBAN_GROUPS: list[tuple[str, frozenset[str], str]] = [
    ("talent_pool", frozenset({"talent_pool"}), "🧡 人才库（淘汰入库 / 可复用）"),
    (
        "offer",
        frozenset({"offer_sent", "offer_negotiation"}),
        "💼 录用与谈薪",
    ),
    ("hold_review", frozenset({"hold_review"}), "⚠️ 待人工 / 需复核"),
    ("closed", frozenset({"closed"}), "⬛ 已关闭 / 其他终局"),
]

_FEISHU_TEXT_CHUNK = 3500


@dataclass
class _Watch:
    batch_id: str
    chat_id: str
    resume_ids: set[str]
    notified_ids: set[str] = field(default_factory=set)  # 已计入终态汇总的 resume_id
    last_stage_by_resume: dict[str, str] = field(default_factory=dict)
    accumulated_results: list[dict[str, Any]] = field(default_factory=list)
    done: bool = False
    start_ts: float = field(default_factory=time.time)


def _bitable_row_open_url(ctx: Any, bitable_record_id: str) -> str | None:
    """浏览器打开「多维表格 → resumes 表 → 具体行」的链接（可点击）。"""
    if not bitable_record_id or not str(bitable_record_id).strip().startswith("rec"):
        return None
    try:
        fe = ctx.config.feishu
        app = str(fe.bitable_app_token)
        base = str(getattr(fe, "link_base", "https://www.feishu.cn")).strip().rstrip("/")
        tbl = str(ctx.table_ids.get("resumes", ""))
        if not app or not tbl:
            return None
        return f"{base}/base/{app}?table={tbl}&record={bitable_record_id}"
    except Exception:
        return None


def _build_talent_pool_pin_message(watch: _Watch, ctx: Any) -> str | None:
    """单独一条短消息：飞书里独立气泡，比长文里的标记更显眼（text 不渲染 Markdown）。"""
    acc = [r for r in watch.accumulated_results if (r.get("stage") or "") == "talent_pool"]
    if not acc:
        return None
    lines = [
        _TALENT_RULE,
        "  " + _TALENT_BADGE,
        f"  本批共 {len(acc)} 人入人才库（可复用池，新岗可再匹配）",
        _TALENT_RULE,
        "",
    ]
    for r in acc:
        name = str(r.get("name") or "")
        rid = str(r.get("resume_id") or "")
        u = _bitable_row_open_url(ctx, str(r.get("bitable_record_id") or ""))
        lines.append(f"  · {name}  {rid}")
        if u:
            lines.append(f"    {u}")
        else:
            lines.append("    （无直链，请查 FEISHU_LINK_BASE）")
        lines.append("")
    lines.append("↓ 完整本批报告见下一条 ↓")
    return "\n".join(lines)[:3950]


def _group_acc_by_kanban(
    acc: list[dict[str, Any]],
) -> list[tuple[str, str, list[dict[str, Any]]]]:
    g: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in acc:
        g[str(r.get("stage") or "")].append(r)
    out: list[tuple[str, str, list[dict[str, Any]]]] = []
    for _key, stages, title in _KANBAN_GROUPS:
        items: list[dict[str, Any]] = []
        for st in stages:
            items.extend(g.get(st, []))
        if items:
            out.append((_key, title, items))
    return out


def _format_pipeline_kanban_md(acc: list[dict[str, Any]], ctx: Any) -> str:
    """分栏看板：纯文本（飞书 text 不解析 Markdown / 加粗）。"""
    groups = _group_acc_by_kanban(acc)
    if not groups:
        return ""
    lines: list[str] = [
        "【本批一眼看板】按终态分栏（不依赖多维表筛选）",
        "────────────────────────",
        "",
    ]
    for _key, title, items in groups:
        if _key == "talent_pool":
            lines.append(_TALENT_RULE)
            lines.append(f"  {title}  共 {len(items)} 人  {_TALENT_BADGE}")
            lines.append(_TALENT_RULE)
        else:
            lines.append(f"【{title}】共 {len(items)} 人")
        lines.append("")
        for r in items:
            name = str(r.get("name") or "")
            rid = str(r.get("resume_id") or "")
            st = str(r.get("stage") or "")
            sc = r.get("score")
            scs = f" 初筛{sc}" if sc not in (None, "", 0) else ""
            st_zh = _SUMMARY_STAGES.get(st) or _STAGE_ZH.get(st, st)
            u = _bitable_row_open_url(ctx, str(r.get("bitable_record_id") or ""))
            uline = f"\n    {u}" if u else ""
            lines.append(f"  · {name}  {rid}{scs}  ｜  {st_zh}{uline}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _build_batch_report_html(
    watch: _Watch, elapsed_s: int, ctx: Any, ts: int
) -> str:
    """本机浏览器打开：分块配色，比表格式更像「看板」。"""
    acc = list(watch.accumulated_results)
    groups = _group_acc_by_kanban(acc)
    bucket_css: dict[str, str] = {
        "talent_pool": "bucket-talent",
        "offer": "bucket-offer",
        "hold_review": "bucket-hold",
        "closed": "bucket-closed",
    }
    parts: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="zh-CN">',
        "<head>",
        '<meta charset="utf-8"/>',
        '<meta name="viewport" content="width=device-width, initial-scale=1"/>',
        f"<title>本批看板 {escape(watch.batch_id)}</title>",
        "<style>",
        "body{font-family:system-ui,Segoe UI,Roboto,Helvetica,Arial,sans-serif;margin:0;padding:16px;"
        "background:#f0f2f5;color:#1a1a1a;}",
        "h1{font-size:1.35rem;margin:0 0 4px;}",
        ".sub{color:#666;font-size:0.9rem;margin-bottom:20px;}",
        ".grid{display:grid;gap:14px;}",
        "@media(min-width:720px){.grid{grid-template-columns:repeat(auto-fit,minmax(240px,1fr));}}",
        ".bucket{border-radius:10px;padding:12px 14px 14px;"
        "background:#fff;box-shadow:0 1px 3px rgba(0,0,0,0.08);}",
        ".bucket h2{margin:0 0 8px;font-size:1.05rem;}",
        ".bucket-talent{background:#fff4e6;border:2px solid #e67e22;box-shadow:0 2px 8px rgba(230,126,34,0.25);}",
        ".bucket-talent h2::before{content:'🧡 ';}",
        ".bucket-offer{border-top:4px solid #27ae60;}",
        ".bucket-hold{border-top:4px solid #f1c40f;}",
        ".bucket-closed{border-top:4px solid #7f8c8d;}",
        ".card{border:1px solid #e6e6e6;border-radius:8px;padding:8px 10px;margin:8px 0;"
        "font-size:0.95rem;}",
        ".name{font-weight:600;}",
        ".meta{color:#555;font-size:0.85rem;}",
        "a{color:#1677ff;word-break:break-all;}",
        "</style>",
        "</head>",
        "<body>",
        "<h1>SuperGenius 本批一眼看板</h1>",
        f'<p class="sub">批次 {escape(watch.batch_id)} · 生成 {escape(datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S"))} · 耗时约 {elapsed_s} 秒。建议全屏，比多维表扫网格更「一眼」。</p>',
    ]
    if not groups:
        parts.append(
            "<p>本批无已汇总的终态行（本批在报告中尚未聚齐终态时也会为空；权威数据以多维表为准）。</p>"
        )
        parts.append("</body></html>")
        return "\n".join(parts)
    parts.append('<div class="grid">')
    for key, title, items in groups:
        cls = bucket_css.get(key, "bucket")
        parts.append(
            f'<section class="bucket {cls}"><h2>{escape(title)} <small>({len(items)} 人)</small></h2>'
        )
        for r in items:
            name = escape(str(r.get("name") or ""))
            rid = escape(str(r.get("resume_id") or ""))
            st = str(r.get("stage") or "")
            st_zh = escape(_SUMMARY_STAGES.get(st) or _STAGE_ZH.get(st, st))
            sc = r.get("score")
            scs = f" · 初筛 {sc}" if sc not in (None, "", 0) else ""
            u = _bitable_row_open_url(ctx, str(r.get("bitable_record_id") or ""))
            alink: str
            if u:
                alink = f'<a href="{escape(u, quote=True)}">打开多维表本行</a>'
            else:
                alink = "（无直链，请查配置）"
            parts.append(
                f'<div class="card"><div class="name">{name} <code>{rid}</code></div>'
                f'<div class="meta">{st_zh}{escape(scs)}<br/>{alink}</div></div>'
            )
        parts.append("</section>")
    parts.append("</div></body></html>")
    return "\n".join(parts)


def _build_markdown_report(
    watch: _Watch,
    elapsed_s: int,
    written_path: str | None,
    ctx: Any,
    html_path: str | None = None,
) -> str:
    """本批总报告：含看板、本批快览、人才库高亮、文末直达链接。"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    n = len(watch.resume_ids)
    lines: list[str] = [
        "SuperGenius 本批处理报告",
        "────────────────────────",
        f"生成时间：{ts}",
        f"批次 ID：{watch.batch_id}",
        f"本批简历数：{n} 份",
        f"自投递起约耗时：{elapsed_s} 秒",
    ]
    if written_path:
        lines.append(f"本地 Markdown 全文：{written_path}")
    if html_path:
        lines.append(
            f"本地 HTML 一眼看板（浏览器打开最直观，彩色分栏）：{html_path}"
        )
    acc = list(watch.accumulated_results)
    c = Counter((x.get("stage") or "") for x in acc)
    if acc:
        tp_n = c.get("talent_pool", 0)
        hr_n = c.get("hold_review", 0)
        offerish = c.get("offer_sent", 0) + c.get("offer_negotiation", 0)
        closed_n = c.get("closed", 0)
        lines.extend(
            [
                "",
                "────────────────────────",
                "",
                "【本批快览】",
                "",
                f"  {_TALENT_BADGE}  共 {tp_n} 人",
                "  → 可点击直链在文末「本批终局 · 人才库直达」；建议选中整段复制",
                "  新岗开放后，人才库候选人可能再次被匹配。",
                f"  待人工/复核等：{hr_n} 人  |  录用&谈薪：{offerish} 人  |  关闭等：{closed_n} 人",
                "",
            ]
        )
    else:
        lines.extend(["", "---", ""])

    if acc:
        lines.append("")
        lines.extend(_format_pipeline_kanban_md(acc, ctx).splitlines())
        lines.append("────────────────────────")
    lines.extend(["", "【结果明细】", ""])

    if not acc:
        lines.append("（本批无已汇总终态记录；详情以多维表为准。）\n")
    else:
        for i, r in enumerate(acc, 1):
            rid = r.get("resume_id", "")
            name = r.get("name", "")
            st = r.get("stage", "") or ""
            label = _SUMMARY_STAGES.get(st, _STAGE_ZH.get(st, st))
            score = r.get("score")
            score_s = f"  初筛{score}" if score not in (None, "", 0) else ""
            br = r.get("bitable_record_id")
            link = _bitable_row_open_url(ctx, str(br or "")) if br else None

            if st == "talent_pool":
                lines.append(_TALENT_RULE)
                lines.append(
                    f"  第 {i} 人  {_TALENT_BADGE}  {name}  ({rid}){score_s}"
                )
                lines.append(_TALENT_RULE)
                lines.append(f"  终态说明：{label}")
            else:
                lines.append(f"第 {i} 人  {name}  ({rid}){score_s}")
                lines.append(f"  终态/节点：{label}")

            if link:
                if st == "talent_pool":
                    lines.append("  多维表本行（点整行蓝链即开）：")
                else:
                    lines.append("  多维表本行：")
                lines.append(f"  {link}")
            elif br:
                lines.append(
                    f"  内部行ID {br}（无直链时请查 FEISHU_LINK_BASE 与表权限）"
                )

            if r.get("decision"):
                lines.append(f"  经理决策：{r['decision']}")
            if r.get("reason"):
                reason = (r.get("reason") or "")[:800]
                lines.append(f"  原因/说明：\n{reason}")
            if r.get("fairness_flag"):
                lines.append("  公平性：已走检测；详见表 reports 与 analyst_note")
            lines.append("")

    lines.extend(
        [
            "────────────────────────",
            "",
            "发「查进度」可看对话内总览。详单与原始字段以多维表为准。",
            "（人才库在表中 pipeline_stage = talent_pool）",
            "（HTML 报告文件有彩色分栏，比聊天纯文本更醒目）",
            "",
        ]
    )

    pool_links: list[str] = []
    for r in acc:
        if (r.get("stage") or "") == "talent_pool":
            u = _bitable_row_open_url(ctx, str(r.get("bitable_record_id") or ""))
            if u:
                pool_links.append(
                    f"  {_TALENT_BADGE}  {r.get('name', '')}  {r.get('resume_id', '')}\n  {u}"
                )
    if pool_links:
        lines.extend(
            [
                _TALENT_RULE,
                "",
                "本批终局 · 人才库直达（可整段收藏）",
                f"  共 {len(pool_links)} 人 · resumes 表对应行，点每行下蓝字链接打开：",
                "",
            ]
        )
        lines.extend(pool_links)
        lines.append(_TALENT_RULE)
        lines.append("")
    elif acc and c.get("talent_pool", 0) > 0:
        lines.extend(
            [
                _TALENT_RULE,
                "本批终局 · 人才库直达",
                f"  有 {c.get('talent_pool', 0)} 人入人才库，但未能生成直链；请检查 FEISHU_LINK_BASE、BITABLE_APP_TOKEN 与表权限。",
                _TALENT_RULE,
                "",
            ]
        )
    return "\n".join(lines)


def _write_report_artifacts(
    report_dir: str, batch_id: str, md_content: str, html_content: str, ts: int
) -> tuple[str | None, str | None]:
    """同一次时间戳落盘 .md 全文 + .html 一眼看板，返回相对项目根路径。"""
    if not report_dir.strip():
        return None, None
    safe = re.sub(r"[^\w\-.]+", "_", batch_id)[:64] or "batch"
    base = f"supergenius_batch_{safe}_{ts}"
    d = ROOT / report_dir.strip()
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning(f"[watcher] 建报告目录失败(忽略): {e}")
        return None, None
    md_path = d / f"{base}.md"
    html_path = d / f"{base}.html"

    def _to_rel(p: object) -> str:
        try:
            return str(p.relative_to(ROOT))  # type: ignore[union-attr]
        except ValueError:
            return str(p)

    out_md: str | None = None
    out_h: str | None = None
    try:
        md_path.write_text(md_content, encoding="utf-8")
        out_md = _to_rel(md_path)
    except OSError as e:
        logger.warning(f"[watcher] 写 Markdown 报告失败(忽略): {e}")
    try:
        html_path.write_text(html_content, encoding="utf-8")
        out_h = _to_rel(html_path)
    except OSError as e:
        logger.warning(f"[watcher] 写 HTML 看板失败(忽略): {e}")
    return out_md, out_h


def _send_text_chunks(
    lark_client: Any, chat_id: str, text: str, label: str = "报告"
) -> None:
    t = text.strip()
    if len(t) <= _FEISHU_TEXT_CHUNK:
        send_text(lark_client, chat_id, t)
        return
    total = (len(t) + _FEISHU_TEXT_CHUNK - 1) // _FEISHU_TEXT_CHUNK
    for i in range(0, len(t), _FEISHU_TEXT_CHUNK):
        part = t[i : i + _FEISHU_TEXT_CHUNK]
        n = i // _FEISHU_TEXT_CHUNK + 1
        header = f"【{label} {n}/{total}】\n\n" if total > 1 else ""
        send_text(lark_client, chat_id, header + part)


class ResultWatcher:
    """线程安全的简历进度监控器。"""

    def __init__(self, ctx: Any, lark_client: Any, interval: float = 8.0) -> None:
        self._ctx = ctx
        self._client = lark_client
        self._interval = interval
        self._lock = threading.Lock()
        self._watches: dict[str, _Watch] = {}
        self._thread = threading.Thread(target=self._loop, daemon=True, name="watcher")
        self._thread.start()

    def register(self, batch_id: str, chat_id: str, resume_ids: list[str]) -> None:
        with self._lock:
            if batch_id in self._watches:
                w = self._watches[batch_id]
                w.resume_ids.update(resume_ids)
            else:
                self._watches[batch_id] = _Watch(
                    batch_id=batch_id,
                    chat_id=chat_id,
                    resume_ids=set(resume_ids),
                )

    def _loop(self) -> None:
        while True:
            time.sleep(self._interval)
            try:
                self._tick()
            except Exception as exc:
                logger.exception(f"[watcher] 轮询异常: {exc}")

    def _tick(self) -> None:
        with self._lock:
            pending = [(bid, w) for bid, w in self._watches.items() if not w.done]
        for bid, watch in pending:
            try:
                self._check(bid, watch)
            except Exception as exc:
                logger.exception(f"[watcher] 检查批次 {bid}: {exc}")

    def _check(self, bid: str, watch: _Watch) -> None:
        notify_steps = bool(getattr(self._ctx.config, "bot_notify_pipeline_steps", True))
        each_terminal = bool(getattr(self._ctx.config, "bot_notify_each_terminal", False))
        rtid = self._ctx.table_ids["resumes"]
        newly_done: list[dict[str, Any]] = []

        for rid in list(watch.resume_ids - watch.notified_ids):
            rows = self._ctx.bitable.search_records(
                rtid,
                filter_conditions=[
                    {"field_name": "resume_id", "operator": "is", "value": [rid]}
                ],
                page_size=3,
            )
            if not rows:
                continue
            r = rows[0]
            stage = feishu_text_to_str(r.fields.get("pipeline_stage")) or feishu_text_to_str(
                r.fields.get("status")
            )
            name = feishu_text_to_str(r.fields.get("candidate_name")) or rid
            score = r.fields.get("score")
            prev = watch.last_stage_by_resume.get(rid)

            if stage != prev:
                if notify_steps and stage and stage not in _TERMINAL_STAGES:
                    self._notify_step(watch, name, stage, score, rid)
                watch.last_stage_by_resume[rid] = stage

            if stage not in _TERMINAL_STAGES:
                continue

            watch.notified_ids.add(rid)
            item = {
                "resume_id": rid,
                "name": name,
                "stage": stage,
                "bitable_record_id": getattr(r, "record_id", "") or "",
                "score": score,
                "decision": feishu_text_to_str(r.fields.get("hm_decision")),
                "reason": feishu_text_to_str(
                    r.fields.get("hm_reason") or r.fields.get("reason") or ""
                )[:2000],
                "fairness_flag": self._parse_fairness(r),
            }
            newly_done.append(item)
            watch.accumulated_results.append(item)

        if newly_done and each_terminal:
            self._notify(watch, newly_done)

        if watch.resume_ids and watch.notified_ids >= watch.resume_ids:
            with self._lock:
                watch.done = True
            self._notify_batch_complete(watch)

    def _parse_fairness(self, r: Any) -> bool:
        try:
            note = feishu_text_to_str(r.fields.get("analyst_note") or "{}")
            d = json.loads(note) if note.strip().startswith("{") else {}
            return bool((d or {}).get("fairness_check"))
        except json.JSONDecodeError:
            return False

    def _notify_step(
        self,
        watch: _Watch,
        name: str,
        stage: str,
        score: Any,
        rid: str,
    ) -> None:
        label = _STAGE_ZH.get(stage, stage)
        sc = f" 初筛分 {score}" if score not in (None, "", 0) else ""
        send_text(
            self._client,
            watch.chat_id,
            f"进度 · `{rid}` {name} → {label}{sc}\n"
            f"（`FEISHU_BOT_NOTIFY_STEPS=0` 可关阶段通知；终态出一份总报告。）",
        )

    def _notify(self, watch: _Watch, results: list[dict[str, Any]]) -> None:
        lines = ["--- 终态/节点结果（逐条） ---"]
        for r in results:
            label = _SUMMARY_STAGES.get(r["stage"], _STAGE_ZH.get(r["stage"], r["stage"]))
            score_str = f" (初筛得分 {r['score']})" if r.get("score") else ""
            lines.append(f"\n{r['name']}{score_str}\n  {label}")
            if r.get("reason"):
                lines.append(f"  原因：{r['reason'][:100]}")
            if r.get("fairness_flag"):
                lines.append("  [!] 已触发公平性检测，请在多维表格 reports 表查看详细告警")
        send_text(self._client, watch.chat_id, "\n".join(lines))

    def _notify_batch_complete(self, watch: _Watch) -> None:
        elapsed = int(time.time() - watch.start_ts)
        report_dir = str(getattr(self._ctx.config, "bot_batch_report_dir", "") or "").strip()
        body = _build_markdown_report(watch, elapsed, None, self._ctx, None)
        written_md: str | None = None
        written_html: str | None = None
        if report_dir:
            ts = int(time.time())
            html = _build_batch_report_html(watch, elapsed, self._ctx, ts)
            w_md, w_h = _write_report_artifacts(
                report_dir, watch.batch_id, body, html, ts
            )
            if w_md:
                written_md, written_html = w_md, w_h
                body = _build_markdown_report(
                    watch, elapsed, written_md, self._ctx, written_html
                )
                try:
                    (ROOT / written_md).write_text(body, encoding="utf-8")
                except OSError as e:
                    logger.warning(f"[watcher] 回写 Markdown 报告失败(忽略): {e}")

        pin = _build_talent_pool_pin_message(watch, self._ctx)
        if pin:
            send_text(self._client, watch.chat_id, pin)

        intro = (
            f"本批 {watch.batch_id} 已全部达终态"
            f"（共 {len(watch.resume_ids)} 份，约 {elapsed} 秒）。\n\n"
        )
        _send_text_chunks(
            self._client,
            watch.chat_id,
            intro + body,
            label="本批报告",
        )
        logger.info(
            f"[watcher] 本批终态报告已发 chat={watch.chat_id!r} md={written_md!r} html={written_html!r} n={len(watch.resume_ids)}"
        )
