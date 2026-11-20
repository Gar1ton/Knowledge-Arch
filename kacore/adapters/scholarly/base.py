"""学术索引适配器的接口与共用 HTTP 工具（adapters 层）。

为何存在：联网补充要查多个学术索引（Crossref / arXiv / OpenAlex / Semantic Scholar），
核验时还要按编号去别的索引查（含 DataCite 与 doi.org）。每家的 HTTP 形态不同，
这里规定统一契约，把各家结果翻译成 `kacore.domain.web_sources.IndexHit`，
业务层（managers）只认这个契约。

契约（`ScholarlyIndex`）：
- `search(query, limit)`：按检索词查，返回按该索引自己的排序排好的命中（rank 从 1 起）；
  失败抛 `ScholarlyIndexError`（带 engine 名与 HTTP 状态），不返回半截结果。
- `lookup(pid)`：按持久标识（domain 的 `Pid`）直接查一条；查不到返回 None，网络错误抛异常。
  不支持该标识种类时返回 None（如 arXiv 不认 DOI）。
- 只翻译数据：不排序、不过滤、不判断相关性或真伪（那是 managers 与 domain 的事）。

书目字段（`IndexHit.fields` 的键）统一为：title、creators（只放结构化的 "Family, Given"，
见 `kacore.domain.citation.normalize_creators` 不按空格拆名的规则）、creator_names（整名，仅显示）、
year、venue、item_type、abstract、oa_pdf_url、url、cited_by。

HTTP 一律同步 urllib（与 Zotero 适配器一致），由调用方放进线程执行；请求函数可注入，便于离线测试。
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from kacore.domain.web_sources import IndexHit, Pid

USER_AGENT = "KnowledgeArch/3.0 (desktop literature assistant)"
DEFAULT_TIMEOUT = 20.0

# 引擎名（存进出处账本，跨层比较用常量）
ENGINE_CROSSREF = "crossref"
ENGINE_ARXIV = "arxiv"
ENGINE_OPENALEX = "openalex"
ENGINE_SEMANTIC_SCHOLAR = "semantic_scholar"
ENGINE_DATACITE = "datacite"
ENGINE_OPENLIBRARY = "openlibrary"


class ScholarlyIndexError(RuntimeError):
    """某个索引请求失败。status 为 HTTP 状态码（网络层错误时为 None）。"""

    def __init__(self, engine: str, message: str, *, status: int | None = None) -> None:
        super().__init__(f"{engine}: {message}")
        self.engine = engine
        self.status = status


class ScholarlyIndex(ABC):
    """一个学术索引。name 是引擎名（见本模块常量）。"""

    name: str = ""

    @abstractmethod
    def search(self, query: str, limit: int) -> list[IndexHit]:
        """按检索词查；返回 rank 从 1 起的命中列表。"""

    @abstractmethod
    def lookup(self, pid: Pid) -> IndexHit | None:
        """按编号直接查一条；查不到或不支持该编号种类返回 None。"""

    def search_url(self, query: str, limit: int) -> str:
        """该检索对应的请求地址（不含 key、邮箱），记进出处账本；不检索的索引返回空串。"""
        return ""

    def attach_observer(self, observer: Observer | None) -> None:
        """每次 HTTP 响应后回调 observer(engine, status, headers)（运行状态与 OpenAlex 额度用）。"""
        http = getattr(self, "_http", None)
        if isinstance(http, HttpClient):
            http.observer = observer


# ── HTTP ────────────────────────────────────────────────────

Opener = Callable[..., Any]
Observer = Callable[[str, int, dict[str, str]], None]


class RateGate:
    """同一索引两次请求之间的最小间隔（线程安全）。arXiv 要求 3 秒一次，其余按各自限速设置。"""

    def __init__(self, min_interval: float) -> None:
        self._min = min_interval
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        if self._min <= 0:
            return
        with self._lock:
            delay = self._last + self._min - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self._last = time.monotonic()


class HttpClient:
    """带 User-Agent、超时、限速的 GET。

    opener 可注入（测试传假函数，签名同 urllib.request.urlopen）。

    request_url() 返回去掉 key 类参数后的地址，供出处账本记录（key 不落盘）。
    """

    SECRET_PARAMS = frozenset({"api_key", "key", "mailto"})

    def __init__(
        self,
        engine: str,
        *,
        opener: Opener | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        min_interval: float = 0.0,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.engine = engine
        self._opener = opener or urllib.request.urlopen
        self._timeout = timeout
        self._gate = RateGate(min_interval)
        self._one_at_a_time = threading.Lock()   # 同一索引一次只发一个请求（Crossref 限并发）
        self._headers = {"User-Agent": USER_AGENT, **(headers or {})}
        self.observer: Observer | None = None

    def _notify(self, status: int, headers: Any) -> None:
        if self.observer is not None:
            try:
                self.observer(self.engine, status, dict(headers or {}))
            except Exception:  # noqa: BLE001 - 状态记录失败不影响请求
                pass

    def get_bytes(self, url: str, params: dict[str, Any] | None = None) -> bytes:
        """GET；遇到 429 按 Retry-After（最多等 MAX_RETRY_WAIT 秒）重试一次。"""
        full = f"{url}?{urllib.parse.urlencode(params)}" if params else url
        with self._one_at_a_time:
            for attempt in (1, 2):
                request = urllib.request.Request(full, headers=self._headers,  # noqa: S310
                                                 method="GET")
                self._gate.wait()
                try:
                    with self._opener(request, timeout=self._timeout) as resp:
                        data: bytes = resp.read()
                        self._notify(int(getattr(resp, "status", 200) or 200),
                                     getattr(resp, "headers", {}))
                        return data
                except urllib.error.HTTPError as exc:
                    self._notify(exc.code, exc.headers)
                    if exc.code == 429 and attempt == 1 and not _quota_spent(exc):
                        time.sleep(_retry_after(exc))
                        continue
                    raise ScholarlyIndexError(self.engine, f"HTTP {exc.code}",
                                              status=exc.code) from exc
                except Exception as exc:  # noqa: BLE001 - 网络层错误统一归类
                    raise ScholarlyIndexError(self.engine, str(exc)) from exc
        raise ScholarlyIndexError(self.engine, "HTTP 429", status=429)

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        raw = self.get_bytes(url, params)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ScholarlyIndexError(self.engine, f"invalid JSON: {exc}") from exc

    @classmethod
    def request_url(cls, url: str, params: dict[str, Any] | None = None) -> str:
        if not params:
            return url
        public = {k: v for k, v in params.items() if k not in cls.SECRET_PARAMS}
        return f"{url}?{urllib.parse.urlencode(public)}" if public else url


MAX_RETRY_WAIT = 5.0


def _quota_spent(exc: urllib.error.HTTPError) -> bool:
    """OpenAlex 的 429 若是当日额度用完（剩余为 0），重试没有意义。"""
    remaining = (exc.headers or {}).get("X-RateLimit-Remaining-USD")
    try:
        return remaining is not None and float(remaining) <= 0
    except ValueError:
        return False


def _retry_after(exc: urllib.error.HTTPError) -> float:
    """Retry-After 秒数（缺省 1 秒，上限 MAX_RETRY_WAIT）。"""
    try:
        value = float((exc.headers or {}).get("Retry-After") or 1)
    except (TypeError, ValueError):
        value = 1.0
    return min(max(value, 0.0), MAX_RETRY_WAIT)


def year_of(value: object) -> str:
    """从 '2026'、2026、'2026-03-01' 取四位年份；取不到返回空串。"""
    text = str(value or "").strip()
    return text[:4] if len(text) >= 4 and text[:4].isdigit() else ""


__all__ = [
    "DEFAULT_TIMEOUT",
    "ENGINE_ARXIV",
    "ENGINE_CROSSREF",
    "ENGINE_DATACITE",
    "ENGINE_OPENALEX",
    "ENGINE_OPENLIBRARY",
    "ENGINE_SEMANTIC_SCHOLAR",
    "USER_AGENT",
    "HttpClient",
    "Observer",
    "Opener",
    "RateGate",
    "ScholarlyIndex",
    "ScholarlyIndexError",
    "year_of",
]
