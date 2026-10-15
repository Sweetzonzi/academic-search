"""极薄的标准库 HTTP 客户端；可注入以便契约测试不联网。"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from . import SourceError

DEFAULT_USER_AGENT = "academic-search/0.0.0 (+local-first literature CLI)"


@dataclass
class HttpResponse:
    url: str
    status: int
    text: str
    headers: dict = field(default_factory=dict)

    def json(self) -> object:
        return json.loads(self.text)


@dataclass
class HttpBytesResponse:
    """原始字节响应；二进制下载与状态码探测用它（HttpResponse 装的是解码后的文本）。"""

    url: str
    status: int
    content: bytes
    headers: dict = field(default_factory=dict)


class HttpClient:
    """带最小间隔的同步客户端；每次请求记录花费头。"""

    def __init__(self, *, user_agent: str = DEFAULT_USER_AGENT, timeout: float = 30.0,
                 min_interval: dict[str, float] | None = None):
        self.user_agent = user_agent
        self.timeout = timeout
        self.min_interval = min_interval or {}
        self._last: dict[str, float] = {}
        self.requests: int = 0
        self.cost_usd: float = 0.0
        self.cost_basis: str = ""
        self.rate_remaining_usd: float | None = None

    def get(self, url: str, *, headers: dict | None = None, source: str = "") -> HttpResponse:
        raw = self.get_bytes(url, headers=headers, source=source)
        return HttpResponse(
            raw.url, raw.status, raw.content.decode("utf-8", errors="replace"), raw.headers
        )

    def get_bytes(self, url: str, *, headers: dict | None = None, source: str = "",
                  raise_on_error: bool = True,
                  max_bytes: int | None = None) -> HttpBytesResponse:
        """取原始字节；e-print 二进制下载与 HTML 可用性探测共用这一条通道。

        raise_on_error=False 时把 4xx/5xx 交回调用方判断（探测要读状态码）；
        max_bytes 是防呆上限，超出即报错而不是截断（截断的 tar.gz 只会变成更贵的错）。
        """
        self._throttle(source)
        request = urllib.request.Request(url, headers=dict(headers or {}))
        request.add_header("User-Agent", self.user_agent)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                content = response.read() if max_bytes is None else response.read(max_bytes + 1)
                if max_bytes is not None and len(content) > max_bytes:
                    raise SourceError(
                        source or "http", "响应超过上限 " + str(max_bytes) + " 字节：" + url, False
                    )
                result = HttpBytesResponse(url, response.status, content, dict(response.headers))
        except urllib.error.HTTPError as exc:
            if raise_on_error:
                raise self._classify(source, exc) from exc
            result = HttpBytesResponse(url, exc.code, exc.read() or b"", dict(exc.headers or {}))
        except urllib.error.URLError as exc:
            raise SourceError(source or "http", "网络不可达：" + str(exc.reason), True) from exc
        self.requests += 1
        self._record_cost(result.headers)
        return result

    def _throttle(self, source: str) -> None:
        interval = self.min_interval.get(source, 0.0)
        if interval <= 0 or not source:
            return
        last = self._last.get(source)
        now = time.monotonic()
        if last is not None and now - last < interval:
            time.sleep(interval - (now - last))
        self._last[source] = time.monotonic()

    def _record_cost(self, raw_headers: dict) -> None:
        headers = {k.lower(): v for k, v in raw_headers.items()}
        raw = headers.get("x-ratelimit-cost-usd")
        if raw:
            try:
                self.cost_usd += float(raw)
                self.cost_basis = "按 OpenAlex 计费档估算"
            except ValueError:
                pass
        remaining = headers.get("x-ratelimit-remaining-usd")
        if remaining:
            try:
                self.rate_remaining_usd = float(remaining)
            except ValueError:
                pass

    @staticmethod
    def _classify(source: str, exc: urllib.error.HTTPError) -> SourceError:
        if exc.code == 429:
            return SourceError(source or "http", "429 限流或额度耗尽", True)
        if exc.code in (401, 402, 403):
            return SourceError(source or "http", "HTTP " + str(exc.code) + " 授权/额度", True)
        if exc.code == 404:
            return SourceError(source or "http", "404 未找到", False)
        return SourceError(source or "http", "HTTP " + str(exc.code), True)


def build_url(base: str, params: dict) -> str:
    clean = {k: v for k, v in params.items() if v not in (None, "")}
    if not clean:
        return base
    return base + "?" + urllib.parse.urlencode(clean, doseq=False)
