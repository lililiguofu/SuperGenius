"""集中配置：从 .env 读取所有外部依赖的参数。"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


def _apply_proxy_policy() -> None:
    """环境变量里若残留 HTTP_PROXY→127.0.0.1:7890 等，但代理软件未开，requests 会 ProxyError。
    在 .env 中设置 DISABLE_HTTP_PROXY=1 时清除代理变量，使飞书/方舟请求直连。"""
    v = (os.getenv("DISABLE_HTTP_PROXY") or "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        for k in (
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
        ):
            os.environ.pop(k, None)


_apply_proxy_policy()


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"环境变量 {name} 未设置。请参照 .env.example 创建 .env 后再运行。"
        )
    return value


def _opt(name: str, default: str) -> str:
    return os.getenv(name) or default


@dataclass(frozen=True)
class FeishuConfig:
    app_id: str
    app_secret: str
    bitable_app_token: str
    # 生成「打开多维表格某行」链接用，国内默认 www.feishu.cn；飞书国际版可改为 https://www.larksuite.com
    link_base: str


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    api_key: str
    model: str
    temperature: float


@dataclass(frozen=True)
class SchedulerConfig:
    tick_seconds: float
    screener_var_threshold: float
    interview_spread_threshold: float
    debate_max_rounds: int
    reactivation_max_per_tick: int


@dataclass(frozen=True)
class Settings:
    feishu: FeishuConfig
    llm: LLMConfig
    scheduler: SchedulerConfig
    log_level: str
    # 非必填：若设则 Analyst 在生成周报后尝试 POST 摘要到飞书群机器人等
    report_webhook_url: str
    # 经理仲裁是否做「仅性别反事实」双次评判；默认开，设 0 可关闭
    fairness_counterfactual_enabled: bool
    # 飞书结果监听器：每阶段变化是否推一条群消息；终态再发详单
    bot_notify_pipeline_steps: bool
    # 有简历到终态时是否立即各推一条；默认关，攒到本批全部结束后一份报告
    bot_notify_each_terminal: bool
    # 本批终态报告写入的目录，相对项目根，空则只发飞书不写文件
    bot_batch_report_dir: str
    # 上项轮询间隔（秒），越短越「实时」、请求略多
    feishu_bot_watcher_interval: float


def load_settings() -> Settings:
    return Settings(
        feishu=FeishuConfig(
            app_id=_require("FEISHU_APP_ID"),
            app_secret=_require("FEISHU_APP_SECRET"),
            bitable_app_token=_require("BITABLE_APP_TOKEN"),
            link_base=(_opt("FEISHU_LINK_BASE", "https://www.feishu.cn") or "https://www.feishu.cn")
            .strip()
            .rstrip("/"),
        ),
        llm=LLMConfig(
            # 默认对接火山引擎方舟（OpenAI 兼容 /v1）；与竞赛「国内模型」一致，勿默认境外地址
            base_url=_opt(
                "LLM_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"
            ),
            api_key=_require("LLM_API_KEY"),
            # 须填方舟自助认领的端点 ID（ep-...），无境外模型名作为默认值
            model=_require("LLM_MODEL"),
            temperature=float(_opt("LLM_TEMPERATURE", "0.3")),
        ),
        scheduler=SchedulerConfig(
            tick_seconds=float(_opt("SCHEDULER_TICK_SECONDS", "5")),
            screener_var_threshold=float(_opt("SCREENER_CONSISTENCY_VAR_THRESHOLD", "100")),
            interview_spread_threshold=float(_opt("INTERVIEW_SPREAD_THRESHOLD", "3")),
            debate_max_rounds=int(_opt("DEBATE_MAX_ROUNDS", "3")),
            reactivation_max_per_tick=int(_opt("REACTIVATION_MAX_PER_TICK", "2")),
        ),
        log_level=_opt("LOG_LEVEL", "INFO"),
        report_webhook_url=(_opt("FEISHU_REPORT_WEBHOOK", "") or "").strip(),
        fairness_counterfactual_enabled=(_opt("FAIRNESS_COUNTERFACTUAL_ENABLED", "1") or "")
        .strip()
        .lower()
        in ("1", "true", "yes", "on"),
        bot_notify_pipeline_steps=(_opt("FEISHU_BOT_NOTIFY_STEPS", "1") or "1")
        .strip()
        .lower()
        in ("1", "true", "yes", "on"),
        bot_notify_each_terminal=(_opt("FEISHU_BOT_NOTIFY_EACH_TERMINAL", "0") or "0")
        .strip()
        .lower()
        in ("1", "true", "yes", "on"),
        bot_batch_report_dir=(_opt("FEISHU_BOT_REPORT_DIR", "reports") or "reports").strip(),
        feishu_bot_watcher_interval=float(_opt("FEISHU_BOT_POLL_SECONDS", "8")),
    )


def setup_logging(level: str = "INFO") -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        format=(
            "<green>{time:HH:mm:ss}</green> "
            "<level>{level: <7}</level> "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>"
        ),
    )
