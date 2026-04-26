# SuperGenius README 1.0 — 代码结构 · 当前能力

本文描述 **仓库里现有代码** 的目录与职责边界，以及 **当前已实现能力**（与根目录 `README.md` 产品愿景的对照）。  

---

## 一、项目一句话

用 **飞书多维表格** 当共享状态机，配合 **国内 OpenAI 兼容 LLM**，多 Agent 按 tick 顺序扫表协作，覆盖（与 `README.md` 愿景对齐方向）：**岗位草稿 → JD（含分析师诊断回写后改版）→ 经理审批 → 初筛（淘汰进人才池）→ 人才池反向激活新投递 → 三面面试（按 README 维度打分行）→（可选）辩论（可分歧收敛提前交经理）→ 经理仲裁 → Offer → 候选人（人格/接受·谈薪·比价·拒·鸽）与招聘方还价 → 多维度周报/漏斗/JD 健康/可选群推送**。

未做「多轮面聊问答机」：面试官仍为 **单次 LLM 综合评分**；候选人投递默认走 `candidate_emitter` 写表，而非表驱 Agent（与 vision 中「独立投递 Agent」等价由脚本完成）。

---

## 二、目录与模块结构

```
SuperGenius/
├── pyproject.toml          # 包元数据；console_scripts: sg-bootstrap / sg-seed / sg-emit / sg-run
├── src/supergenius/
│   ├── cli.py              # 与 scripts/*.py 等价的入口（见下）
│   ├── config.py           # .env：飞书、LLM、调度、初筛方差、面试分差、辩论轮数等
│   ├── runtime.py          # boot()
│   ├── agents/             # 各业务 Agent + base
│   ├── feishu/             # Bitable 封装
│   ├── llm/client.py
│   ├── prompts/*.md
│   ├── schema/tables.py    # ALL_TABLES 定义
│   └── orchestrator/       # graph + scheduler
├── scripts/                # 与 cli 同源，便于直接 python 运行
├── tests/
└── .supergenius/tables.json
```

**依赖关系**：`scripts/run_mvp.py` 或 `sg-run` → `boot()` → `BitableClient` + `LLMClient` + `load_table_ids()` → `AgentContext` → `build_graph` / `run_scheduler`。

---

## 三、数据模型（飞书表）

| 表 | 主键 | 用途 |
|----|------|------|
| **jobs** | `job_id` | 岗位 + `jd_suggestion`（分析师回写） |
| **resumes** | `resume_id` | 初筛与 `pipeline_stage`、`hm_decision`、`hm_reason` 等 |
| **events** | `event_id` | 审计 |
| **interviews** | `interview_id` | 三面评分行（tech/business/culture） |
| **debates** | `debate_id` | 辩论发言 |
| **offers** | `offer_id` | Offer 与候选人回复 |
| **reports** | `report_id` | 周报、漏斗、JD 健康、**公平性告警**（`kind=fairness`）等 |

**简历 `pipeline_stage`（节选）**：`interview_queued` → `interviews_in_progress` → `debate` 或 `hm_arbitration` → `offer_drafting` → `offer_sent` →（可选）`offer_negotiation` → `closed`；初筛 **reject** 为 **`talent_pool`**（人才池，可被动岗位再次匹配）。另有 `hold_review` 等见代码枚举。

**公平性（经理仲裁）**：默认在 `hiring_manager_arbiter` 内做 **性别反事实双评**（仅将「假设社会性别」在男/女间互换，其余材料不变）。若两次 `hire/reject` **不一致**，则 **不进入 Offer**，`pipeline_stage` 置为 **`hold_review`**，`hm_decision` 为 **`manual_review`**，并写 `reports.kind=fairness`。若无法从 `resumes.gender` 或简历正文推断性别（`unknown`），同样转 **人工审核** 并写公平性报告。可在 `.env` 中关闭（见下）。

---

## 四、编排（每个 tick 的固定顺序）

`build_graph` 中顺序为：

`jd_strategist` → `hiring_manager`（JD 审批）→ `screener` → `pool_reactivator`（人才池）→ `interview_fanout` → 三位面试官 → `post_interview` → `debate`（可收敛提前交经理）→ `hiring_manager_arbiter` → `offer_manager` → `candidate` → `offer_negotiation`（招聘方对谈薪/比价还价）→ `analyst`。

分支由**表内状态**表达，LangGraph 仍为**线序无条件边**。

---

## 五、与环境变量

见 `.env.example`。除原项外：

- `INTERVIEW_SPREAD_THRESHOLD`：三面 `total_score` 最大–最小 ≥ 该值则进入 **debate**（默认 3）。
- `DEBATE_MAX_ROUNDS`：辩论轮数上限（默认 3），每轮三条发言（技术/业务/文化）。
- `SCREENER_CONSISTENCY_VAR_THRESHOLD`：初筛双次打分 **总体** 方差阈值；评分量表为 0–10 时，两样本 **pvariance** 最大约 25，若希望「几乎不 hold」可保持较大值；若需方差敏感可调低（如个位数）。
- `REACTIVATION_MAX_PER_TICK`：每 tick 从人才池**最多**生成多少条「新岗位二次投递」记录（默认 2）。
- `FEISHU_REPORT_WEBHOOK`：可选。若填**飞书群机器人 Webhook 地址**（`https://open.feishu.cn/...`），Analyst 在生成周报后尝试向该地址 POST 一段纯文本（`msg_type: text`），失败仅打日志、不中断流程。
- `FAIRNESS_COUNTERFACTUAL_ENABLED`：是否启用经理仲裁 **性别反事实** 检测。默认 `1`（开）；设 `0` 则恢复为**单次**仲裁、不做双评。补字段后请 `bootstrap`：`resumes.gender` 可选，填 `male`/`female` 或简写 男/女；留空时由一次 LLM 从 `raw_text` 推断。

---

## 六、脚本与命令

| 入口 | 作用 |
|------|------|
| `uv run python scripts/bootstrap_tables.py` 或 `uv run sg-bootstrap` | 按 `ALL_TABLES` 建表/补字段 |
| `seed_jobs` / `sg-seed` | 写入 `draft` 岗位 |
| `candidate_emitter` / `sg-emit` | 批量投递简历 |
| `run_mvp` / `sg-run` | 启动调度器 |

`reset_mvp_data.py` 按 `ALL_TABLES` 清空**所有**已登记表中的记录（不删表）。

**推荐顺序**：改表后 `bootstrap_tables` → 可选 `reset_mvp_data` → `seed_jobs` → 启动 `run_mvp` → `candidate_emitter`。

**从仅三表旧 Base 升级**：拉取新代码后务必再执行一次 `bootstrap_tables`，在 `.supergenius/tables.json` 中补齐 `interviews` / `debates` / `offers` / `reports` 的 `table_id`；否则 `load_table_ids()` 会报错。

---

## 七、测试

`uv run pytest tests`；含初筛、JD/HM、面试扇出、面后分岔、**经理仲裁公平性**等用例。

---

## 八、与 README.md 的关系

- **README.md**：产品故事与流程愿景。  
- **README1.0**：以本仓库代码为准的技术说明；随实现更新。
