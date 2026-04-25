"""启动 SuperGenius MVP：三个 Agent 按节拍扫表，一直跑直到 Ctrl+C。"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from supergenius.orchestrator import run_scheduler  # noqa: E402
from supergenius.runtime import boot  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticks", type=int, default=None, help="跑 N 次后退出（默认一直跑）")
    parser.add_argument("--tick-seconds", type=float, default=None, help="覆盖 .env 里的节拍")
    args = parser.parse_args()

    settings, ctx = boot()
    tick = args.tick_seconds if args.tick_seconds is not None else settings.scheduler.tick_seconds
    asyncio.run(run_scheduler(ctx, tick_seconds=tick, stop_after=args.ticks))


if __name__ == "__main__":
    main()
