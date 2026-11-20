"""联网补充各数据库的运行状态与 OpenAlex 免费额度（managers 层）。

为何存在：数据流「联网补充」节点要显示每个数据库最近是否成功、最近的错误，以及 OpenAlex 今天还剩多少
免费额度。用户决定（2026-11-20）：OpenAlex 默认开、只用免费额度、「用完拉倒」——额度用完后当天
自动跳过 OpenAlex，其余数据库照常查，直到 OpenAlex 告诉我们的重置时间。

额度只读 OpenAlex 响应头（2026-11-20 实测）：X-RateLimit-Limit-USD、X-RateLimit-Remaining-USD、
X-RateLimit-Cost-USD、X-RateLimit-Reset（距重置的秒数）。只在内存里，重启后从下一次请求重新得知。
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

OPENALEX = "openalex"
_H_LIMIT = "x-ratelimit-limit-usd"
_H_REMAINING = "x-ratelimit-remaining-usd"
_H_COST = "x-ratelimit-cost-usd"
_H_RESET = "x-ratelimit-reset"


@dataclass
class EngineState:
    last_ok_at: str = ""
    last_error: str = ""
    last_error_at: str = ""
    quota_limit_usd: float | None = None
    quota_remaining_usd: float | None = None
    exhausted_until: float = 0.0          # epoch 秒；0 = 没有用完


class EngineMonitor:
    """线程安全（适配器在线程里发请求）。clock 可注入（测试用）。"""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._states: dict[str, EngineState] = {}

    def observe(self, engine: str, status: int, headers: Mapping[str, str]) -> None:
        """每次 HTTP 响应（含错误响应）后调用。"""
        now = self._clock()
        lowered = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
        with self._lock:
            state = self._states.setdefault(engine, EngineState())
            if 200 <= status < 300:
                state.last_ok_at = _iso(now)
            elif status != 404:                 # 404 = 按编号没查到，不是故障
                state.last_error, state.last_error_at = f"HTTP {status}", _iso(now)
            if engine == OPENALEX:
                self._update_quota(state, status, lowered, now)

    def error(self, engine: str, message: str) -> None:
        """网络层错误（没有 HTTP 状态）。"""
        with self._lock:
            state = self._states.setdefault(engine, EngineState())
            state.last_error, state.last_error_at = message[:200], _iso(self._clock())

    def available(self, engine: str) -> bool:
        with self._lock:
            state = self._states.get(engine)
            return state is None or self._clock() >= state.exhausted_until

    def snapshot(self) -> dict[str, dict[str, object]]:
        now = self._clock()
        with self._lock:
            out: dict[str, dict[str, object]] = {}
            for engine, state in self._states.items():
                data = asdict(state)
                data["exhausted"] = now < state.exhausted_until
                data["exhausted_until"] = _iso(state.exhausted_until) if data["exhausted"] else ""
                out[engine] = data
            return out

    def _update_quota(self, state: EngineState, status: int, headers: dict[str, str],
                      now: float) -> None:
        limit = _num(headers.get(_H_LIMIT))
        remaining = _num(headers.get(_H_REMAINING))
        cost = _num(headers.get(_H_COST)) or 0.0
        if limit is not None:
            state.quota_limit_usd = limit
        if remaining is not None:
            state.quota_remaining_usd = remaining
        out_of_budget = remaining is not None and remaining < max(cost, 1e-9)
        refused = status in (409, 429) and (remaining is None or remaining <= cost)
        if out_of_budget or refused:
            reset = _num(headers.get(_H_RESET))
            state.exhausted_until = now + reset if reset and reset > 0 else _next_utc_midnight(now)


def _num(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat() if epoch else ""


def _next_utc_midnight(now: float) -> float:
    day = datetime.fromtimestamp(now, timezone.utc).date() + timedelta(days=1)
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp()


__all__ = ["EngineMonitor", "EngineState", "OPENALEX"]
