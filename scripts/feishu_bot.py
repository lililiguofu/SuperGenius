"""飞书机器人入口：通过 WebSocket 长连接监听消息，驱动招聘流程，推送结果。

用法（项目根目录）：
    uv run python scripts/feishu_bot.py

与 run_mvp.py 独立运行：二者都要跑，可以开两个终端窗口。
  - 终端 1：uv run python scripts/run_mvp.py      # 调度器（真正处理数据）
  - 终端 2：uv run python scripts/feishu_bot.py   # 机器人（收消息 + 推结果）

飞书开放平台一次性设置（4 步）：
  1. 应用管理 → 功能 → 机器人 → 启用
  2. 事件订阅 → 选择「使用长连接接收事件（SDK）」（无需公网 IP）
     → 添加事件：im.message.receive_v1（接收消息）
  3. 权限管理 → 开通（缺一不可按场景选）：
     · im:message:send_as_bot        （以机器人身份发消息）
     · im:message.p2p_msg:readonly   （私信/单聊：读用户发给机器人的消息）← 私聊无反应多半是缺这条
     · im:message.group_at_msg:readonly （群聊：仅当用户 @ 机器人时收到该条）
     · drive:drive                   （下载消息里附带的文件）
     说明：接收消息事件会按你开通的权限过滤推送。只有群@权限时，单聊(p2p)不会收到任何事件。
  4. 应用版本 → 发布上线

设置完后，直接在飞书里找到应用 / 私信机器人即可交互。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import lark_oapi as lark  # noqa: E402
from loguru import logger  # noqa: E402

from supergenius.bot.handler import BotHandler  # noqa: E402
from supergenius.bot.watcher import ResultWatcher  # noqa: E402
from supergenius.runtime import boot  # noqa: E402


def main() -> None:
    settings, ctx = boot()

    # 独立的 lark Client（与 BitableClient 共用同一个 app_id，但可以有不同配置）
    lark_client = (
        lark.Client.builder()
        .app_id(settings.feishu.app_id)
        .app_secret(settings.feishu.app_secret)
        .log_level(lark.LogLevel.INFO)
        .build()
    )

    watcher = ResultWatcher(
        ctx,
        lark_client,
        interval=settings.feishu_bot_watcher_interval,
    )
    bot = BotHandler(ctx, lark_client, watcher)

    # 注册「接收消息」事件（P2 事件名：p2.im.message.receive_v1）
    # 注意：lark-oapi 使用链式方法 register_p2_im_message_receive_v1，没有通用的 register()
    event_handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(bot.on_message)
        .build()
    )

    # WebSocket 长连接客户端（不需要公网 IP）
    try:
        from lark_oapi.ws import Client as WsClient  # type: ignore[import-untyped]
    except ImportError:
        # 兼容旧版路径
        from lark_oapi.ws.client import Client as WsClient  # type: ignore[import-untyped]

    ws = WsClient(
        settings.feishu.app_id,
        settings.feishu.app_secret,
        event_handler=event_handler,
        log_level=lark.LogLevel.INFO,
    )

    logger.info("SuperGenius 飞书机器人已启动（WebSocket 长连接）")
    logger.info("在飞书里私信机器人，或在群里 @机器人，即可开始交互。")
    logger.info(
        "若私聊无反应：请检查开放平台已开通「读取用户发给机器人的单聊消息」"
        " (im:message.p2p_msg:readonly)，保存并重新发布应用版本后生效。"
    )
    ws.start()  # 阻塞，直到进程被中断


if __name__ == "__main__":
    main()
