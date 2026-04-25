"""一键在飞书多维表格里创建 jobs / resumes / events 三张表并登记 table_id。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from loguru import logger  # noqa: E402

from supergenius.config import load_settings, setup_logging  # noqa: E402
from supergenius.feishu import BitableClient, get_lark_client  # noqa: E402
from supergenius.schema.bootstrap import bootstrap_all  # noqa: E402


def main() -> None:
    settings = load_settings()
    setup_logging(settings.log_level)

    lark = get_lark_client(settings.feishu.app_id, settings.feishu.app_secret)
    bitable = BitableClient(lark, settings.feishu.bitable_app_token)

    mapping = bootstrap_all(bitable)
    logger.info("完成。table_id 映射：")
    for k, v in mapping.items():
        logger.info(f"  {k}: {v}")


if __name__ == "__main__":
    main()
