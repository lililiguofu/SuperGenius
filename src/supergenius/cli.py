"""Console entry points (see pyproject [project.scripts]).

与 `scripts/*.py` 行为一致，便于 `sg-bootstrap` / `sg-seed` / `sg-emit` / `sg-run` 直接调用。
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1].parent


def bootstrap_tables() -> None:
    from loguru import logger

    from supergenius.config import load_settings, setup_logging
    from supergenius.feishu import BitableClient, get_lark_client
    from supergenius.schema.bootstrap import bootstrap_all

    settings = load_settings()
    setup_logging(settings.log_level)
    lark = get_lark_client(settings.feishu.app_id, settings.feishu.app_secret)
    bitable = BitableClient(lark, settings.feishu.bitable_app_token)
    mapping = bootstrap_all(bitable)
    logger.info("完成。table_id 映射：")
    for k, v in mapping.items():
        logger.info(f"  {k}: {v}")


def _run_script(relative: str) -> None:
    if str(ROOT / "src") not in sys.path:
        sys.path.insert(0, str(ROOT / "src"))
    path = ROOT / relative
    runpy.run_path(str(path), run_name="__main__")


def seed_jobs() -> None:
    _run_script("scripts/seed_jobs.py")


def candidate_emitter() -> None:
    _run_script("scripts/candidate_emitter.py")


def run_mvp() -> None:
    _run_script("scripts/run_mvp.py")


def generate_jd() -> None:
    _run_script("scripts/generate_jd.py")


def feishu_bot() -> None:
    _run_script("scripts/feishu_bot.py")


if __name__ == "__main__":
    print("Use: sg-run | sg-bootstrap | sg-seed | sg-emit | sg-jd | sg-bot", file=sys.stderr)
    sys.exit(1)
