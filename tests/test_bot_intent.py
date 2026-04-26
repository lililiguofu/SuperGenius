"""bot/intent：飞书 @ 清洗与开岗规则回退。"""

from supergenius.bot.intent import (
    heuristic_create_job_from_text,
    looks_like_create_job,
    strip_feishu_mention_noise,
)


def test_strip_at_tags_and_plain_at() -> None:
    raw = (
        '<at user_id="ou_123">@SuperGenius 虚拟</at> 招一名 Python 后端工程师，P5，'
        "预算 2 万到 3 万每月，比较急"
    )
    t = strip_feishu_mention_noise(raw)
    assert "at user_id" not in t
    assert "招一名" in t
    assert "Python" in t


def test_looks_like_create_job_user_example() -> None:
    t = "招一名 Python 后端工程师，P5，预算 2 万到 3 万每月，比较急"
    assert looks_like_create_job(t) is True


def test_heuristic_parses_budget_wan() -> None:
    t = "招一名 Python 后端工程师，P5，预算 2 万到 3 万每月，比较急"
    h = heuristic_create_job_from_text(t)
    assert h["intent"] == "create_job"
    assert h["level"] == "P5"
    assert h["budget_min"] == 20_000
    assert h["budget_max"] == 30_000
    assert h["urgency"] == "urgent"
