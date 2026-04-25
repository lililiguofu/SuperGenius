# 大模型 API 配置（仅 `.env`，不依赖 IDE）

SuperGenius 通过环境变量连接 **OpenAI 兼容** 的 Chat Completions 接口。默认按 **火山引擎方舟**（北京区）填写；你只需在本机维护根目录的 `.env`。

## 你需要从火山控制台准备的三项

| 环境变量 | 含义 | 典型值 / 说明 |
|----------|------|----------------|
| `LLM_BASE_URL` | 推理网关 `.../api/v3` 的基地址 | 默认已写在 `.env.example`：`https://ark.cn-beijing.volces.com/api/v3`；若账号在其它地域，以控制台为准 |
| `LLM_API_KEY` | 方舟 **API Key** | 控制台创建，常含 `ark-` 前缀，仅填在 `.env` |
| `LLM_MODEL` | **端点 / 接入点 ID** | 在方舟「在线推理」里**自助认领的 EP**，整串以 **`ep-` 开头**，不是 `gpt-4o` 这类名字 |

`LLM_TEMPERATURE` 可选，默认 `0.3`。

## 操作步骤

1. 复制模板：`cp .env.example .env`（Windows：`copy .env.example .env`）
2. 在 `.env` 中填写 `LLM_API_KEY` 和 `LLM_MODEL`（`ep-...`），确认 `LLM_BASE_URL` 与控制台一致
3. 同文件内继续填写飞书 `FEISHU_*` 与 `BITABLE_APP_TOKEN`
4. 运行 `python scripts/bootstrap_tables.py` 等脚本

程序启动时从 [`src/supergenius/config.py`](../src/supergenius/config.py) 读取上述变量，并交给 `openai` SDK（仅作兼容客户端）访问 `LLM_BASE_URL`。

## 与飞书字段区分

- `FEISHU_APP_ID` 为飞书应用 **`cli_` 开头**
- 火山 `ep-` **只**出现在 `LLM_MODEL`，勿写入 `FEISHU_*`
