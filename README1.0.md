# SuperGenius README 1.0 — 代码结构 · 当前能力

本文描述 **仓库里现有代码** 的目录与职责边界，以及 **MVP 已能完成的工作**（与尚未实现的部分）。产品愿景与流程叙事见根目录 `README.md`。

---

## 一、项目一句话

用 **飞书多维表格** 当共享状态机，配合 **国内 OpenAI 兼容 LLM**，由 **三个 Agent** 在表里协作完成：**岗位草稿 → 生成 JD → 经理审批 → 岗位开放 → 简历初筛打分**。

---

## 二、目录与模块结构

```
SuperGenius/
├── pyproject.toml          # 包元数据与依赖；可编辑安装 supergenius
├── src/supergenius/
│   ├── config.py           # .env 加载：飞书、LLM、调度节拍、日志级别等
│   ├── runtime.py         # boot()：装配 Settings + AgentContext
│   ├── agents/            # 三个业务 Agent + 基类
│   │   ├── base.py        # 扫表、乐观领取(owner+status)、handle、写回、events 审计
│   │   ├── jd_strategist.py
│   │   ├── hiring_manager.py
│   │   └── screener.py
│   ├── feishu/            # Lark OpenAPI 封装
│   │   ├── client.py      # 应用级 client
│   │   └── bitable.py     # 表/字段/记录的 CRUD、search、batch、delete
│   ├── feishu/field_value.py   # 飞书单元格与 Python 字符串互转
│   ├── llm/client.py      # OpenAI 兼容 chat；提示词模板；结构化 JSON 多档回退
│   ├── prompts/*.md       # 各 Agent 的 Markdown 提示（占位符替换）
│   ├── schema/
│   │   ├── tables.py      # jobs / resumes / events 字段与枚举常量
│   │   └── bootstrap.py   # 在多维表格中 ensure 表与字段
│   └── orchestrator/
│       ├── graph.py       # LangGraph：单次 tick 内顺序执行三 Agent
│       └── scheduler.py   # asyncio 周期触发 tick，可选 Ctrl+C / stop_after
├── scripts/               # 实际常用入口（见下文「如何跑」）
├── fixtures/              # 示例岗位 JSON、示例简历 txt
├── tests/                 # pytest：bitable 内存替身、Agent 行为
└── .supergenius/tables.json   # bootstrap 写入的 table_id 映射（勿提交敏感信息时留意）
```

**依赖关系（简图）**：`scripts/*` 或 `run_mvp` → `runtime.boot()` → `BitableClient` + `LLMClient` + `load_table_ids()` → `AgentContext` → `build_graph` / `run_scheduler`。

---

## 三、数据模型（飞书三张表）

| 表 | 主键字段 | 用途 |
|----|-----------|------|
| **jobs** | `job_id` | 岗位：`jd_brief` / `jd_text`、`status`、`owner_agent`、`updated_at` 等 |
| **resumes** | `resume_id` | 投递：`job_id` 关联岗位、`raw_text`、筛选结果 `score`/`decision`/`reason`、`status` |
| **events** | `event_id` | 审计：claim / update / error 等，带 `actor_agent` 与 `payload` |

**岗位状态（`JobStatus`）**：`draft` → `jd_drafting`（处理中）→ `jd_pending_approval` → `open` / `closed`。  
**简历状态（`ResumeStatus`）**：`new` → `screening` → `screened`（及决策字段 `pass` / `hold` / `reject` 等）。

代码里 `schema/tables.py` 末尾 **TODO** 已写明：面试、辩论、Offer、报表等表 **尚未** 建。

---

## 四、编排与 Agent 行为（当前代码真会做的事）

### 4.1 调度

- `orchestrator/scheduler.py`：每隔 `SCHEDULER_TICK_SECONDS`（默认 5s）在线程池中执行 **一次** `run_tick`。
- `orchestrator/graph.py`：**每个 tick 固定顺序**，无分支边：`JDStrategist` → `HiringManager` → `Screener`。

### 4.2 JD 策划官（`jd_strategist`）

- 查询 `jobs` 中 `status == draft` 的行。
- 领取时把 `status` 置为 `jd_drafting`，`owner_agent` 为自己。
- 用 LLM 根据 `title/level/headcount/budget/urgency/jd_brief` 生成 **`jd_text`**，并把 `status` 改为 `jd_pending_approval`。
- 若 LLM 失败或返回无法写回：基类会 **`rollback_status_on_error`**，本 Agent 配置为回到 **`draft`**，避免永久卡在 `jd_drafting`。

### 4.3 招聘经理（`hiring_manager`，MVP 仅 JD 审批）

- 查询 `status == jd_pending_approval`。
- 若 `jd_text` 为空：打回 **`draft`**。
- 否则 LLM 输出结构化决策：`approve` / `approve_with_notes` → **`open`**；`request_revision` → **`draft`**。

### 4.4 简历筛选官（`screener`）

- 查询 `resumes` 中 `status == new`。
- 按 `job_id` 在 `jobs` 中解析 **`jd_text`**；若无 JD：写回说明字段并保持 **`new`**，下轮再试。
- 有 JD 时：对同一份简历 **调用 LLM 两次** 打分，比较方差；超阈值则 `hold`，否则按模型给出的决策写 `pass`/`reject` 等并置 `screened`。
- LLM 失败时同样可 **回滚到 `new`**，避免卡在 `screening`。

### 4.5 LLM 与提示词

- `llm/client.py`：统一 `chat(system, user, json_schema=...)`。  
  对不支持 `response_format` 的端点（如部分火山方舟 `ep-`）：依次尝试 **json_schema → json_object → 纯文本 + 正文 JSON 解析**；成功探测后 **缓存为仅走第三档**，减少无效请求与日志噪音。
- `prompts/*.md`：`render_prompt(name, **vars)` 做 `{{var}}` 替换。

### 4.6 飞书层

- `feishu/bitable.py`：创建表、补字段、按条件 `search_records`、`create`/`update`/`batch`/`delete`。
- `schema/bootstrap.py`：按 `ALL_TABLES` 定义对齐多维表格结构，并把 `table_id` 写入 `.supergenius/tables.json`。

---

## 五、脚本与「当前能演示的整条链路」

| 脚本 | 作用 |
|------|------|
| `scripts/bootstrap_tables.py` | 在指定多维表格中创建/同步 **jobs、resumes、events** 及字段 |
| `scripts/seed_jobs.py` | 读 `fixtures/job_brief.json`，若 `job_id` 不存在则插入一条 **`draft` 岗位** |
| `scripts/candidate_emitter.py` | 把 `fixtures/resumes/*.txt` 批量写入 **resumes**（`status=new`） |
| `scripts/run_mvp.py` | 启动调度器，循环执行上述三 Agent |
| `scripts/reset_mvp_data.py` | 清空三张表中的 **全部记录**（不删表），便于从零再跑 |

**推荐顺序（从零演示）**：`bootstrap_tables`（首启或改表后）→ 可选 `reset_mvp_data` → `seed_jobs` → `run_mvp`（保持运行）→ `candidate_emitter`（可多次，会追加简历）。

运行方式示例：`uv run python scripts/run_mvp.py`（需项目根目录 `.env` 配置飞书与 LLM，参见 `.env.example` / `docs/llm.md`）。

> 说明：`pyproject.toml` 里 `sg-bootstrap` 等入口指向 `supergenius.cli`，**当前仓库无对应 `cli` 模块**；日常以 **`scripts/` 下 Python 文件** 为准。

---

## 六、当前能力边界（明确「还不能做啥」）

**已实现（与代码一致）**

- 多维表格上的 **阶段一 + 阶段二入口**：立项数据、JD 生成与审批、简历写入与 **LLM 初筛**（含双次打分一致性逻辑）。
- **events** 表上的简单审计（领取、更新、错误）。

**未实现（README 愿景或其它文件中的规划，此处无落地代码）**

- 三位面试官、候选人模拟、辩论、Offer、分析师等 Agent。
- `schema/tables.py` 中 TODO 的 **interviews / debates / offers / reports** 等表。
- LangGraph 在 MVP 中 **无条件边**；流程分支完全依赖 **表内状态** 与各 Agent 的 `claim_filter`。

---

## 七、测试与质量

- `tests/`：对 **Bitable 内存替身**、JD/HM 链路、筛选逻辑等有单元测试；`pytest` 默认 `pythonpath=src`。
- 安装开发依赖：`pip install -e ".[dev]"` 或 `uv sync --extra dev`（视本地工具链而定）。

---

## 八、与本文件版本的关系

- **README 1.0**：以 **代码与脚本为准** 的结构与能力说明，便于接手或答辩时对照仓库。  
- **README.md**：产品故事与长期蓝图；与本文技术范围可并存、互补。
