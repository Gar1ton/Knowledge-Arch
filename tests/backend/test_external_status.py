"""联网补充运行状态：OpenAlex 免费额度用完后当天跳过（用户 11-20：「走免费，用完拉倒」）。"""
from __future__ import annotations

from kacore.managers.external_status import EngineMonitor

_QUOTA = {"X-RateLimit-Limit-USD": "0.1", "X-RateLimit-Cost-USD": "0.001",
          "X-RateLimit-Reset": "3600"}


def test_openalex_quota_is_tracked_and_exhaustion_skips_until_reset() -> None:
    now = [1_000_000.0]
    monitor = EngineMonitor(clock=lambda: now[0])
    monitor.observe("openalex", 200, {**_QUOTA, "X-RateLimit-Remaining-USD": "0.05"})
    state = monitor.snapshot()["openalex"]
    assert state["quota_remaining_usd"] == 0.05 and state["exhausted"] is False
    assert monitor.available("openalex")

    monitor.observe("openalex", 429, {**_QUOTA, "X-RateLimit-Remaining-USD": "0"})
    assert not monitor.available("openalex")
    assert monitor.snapshot()["openalex"]["exhausted"] is True
    now[0] += 3601                                     # 过了重置时间
    assert monitor.available("openalex")


def test_rate_limit_with_budget_left_is_not_exhaustion() -> None:
    monitor = EngineMonitor(clock=lambda: 1.0)
    monitor.observe("openalex", 429, {**_QUOTA, "X-RateLimit-Remaining-USD": "0.08"})
    assert monitor.available("openalex")


def test_errors_and_not_found_are_recorded_differently() -> None:
    monitor = EngineMonitor(clock=lambda: 1.0)
    monitor.observe("crossref", 404, {})
    assert monitor.snapshot()["crossref"]["last_error"] == ""   # 按编号没查到不是故障
    monitor.observe("crossref", 503, {})
    monitor.error("arxiv", "timed out")
    snap = monitor.snapshot()
    assert snap["crossref"]["last_error"] == "HTTP 503"
    assert snap["arxiv"]["last_error"] == "timed out"
