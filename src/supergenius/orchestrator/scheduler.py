"""asyncio 轮询调度器：每 N 秒触发一次 tick。"""

from __future__ import annotations

import asyncio
import signal
from typing import Any

from loguru import logger

from supergenius.agents import AgentContext
from supergenius.orchestrator.graph import build_graph, run_tick


async def run_scheduler(ctx: AgentContext, tick_seconds: float, stop_after: int | None = None) -> None:
    """
    tick_seconds: 每次 tick 之间的间隔
    stop_after: 跑够 N 次就退出（None = 一直跑直到 Ctrl+C）
    """
    graph: Any = build_graph(ctx)
    ticks = 0
    stop_event = asyncio.Event()

    def _handle_signal(*_: Any) -> None:
        logger.info("收到停机信号，等本轮 tick 结束后退出...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    # Windows 的 asyncio 不支持 add_signal_handler，直接忽略异常
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except (NotImplementedError, AttributeError):
            pass

    logger.info(
        f"[scheduler] 启动，每 {tick_seconds}s 一次 tick；无待办时也会打一行「空闲」表示在跑，按 Ctrl+C 停止"
    )
    while not stop_event.is_set():
        try:
            result = await asyncio.to_thread(run_tick, graph)
        except Exception as e:
            logger.exception(f"[scheduler] tick 异常: {e}")
            result = None
        else:
            tp = int((result or {}).get("total") or 0)
            proc = (result or {}).get("processed") or {}
            if tp:
                logger.info(f"[scheduler] 第 {ticks + 1} 次 tick 处理 {tp} 条: {proc}")
            else:
                logger.info(
                    f"[scheduler] 第 {ticks + 1} 次 tick 结束（本回合无待办），{tick_seconds}s 后下一轮"
                )
        ticks += 1
        if stop_after is not None and ticks >= stop_after:
            logger.info(f"[scheduler] 达到 stop_after={stop_after}，退出")
            break
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=tick_seconds)
        except asyncio.TimeoutError:
            pass
    logger.info(f"[scheduler] 已退出，共 {ticks} 次 tick")


__all__ = ["run_scheduler"]
