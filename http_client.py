from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import time
import urllib.robotparser
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from config import NetworkConfig
from errors import RobotsDeniedError, ScraperError, SourceBlockedError, SourceConfigurationError
from storage import Storage

logger = logging.getLogger(__name__)
Params = Mapping[str, Any] | Sequence[tuple[str, Any]] | None


class AsyncHttpClient:
    """Rate-limited JSON client with retries, robots checks and SQLite cache."""

    RETRY_STATUSES = {429, 500, 502, 503, 504}

    def __init__(
        self,
        config: NetworkConfig,
        storage: Storage,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        self.storage = storage
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._semaphore = asyncio.Semaphore(config.max_concurrency)
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self._robots_locks: dict[str, asyncio.Lock] = {}
        self._crawl_delays: dict[str, float] = {}

    async def __aenter__(self) -> AsyncHttpClient:
        self._client = httpx.AsyncClient(
            headers={"User-Agent": self.config.user_agent, "Accept": "application/json"},
            timeout=httpx.Timeout(self.config.timeout),
            follow_redirects=True,
            transport=self._transport,
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def ensure_allowed(self, url: str) -> None:
        if not self.config.respect_robots_txt:
            return
        parts = urlsplit(url)
        origin = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
        parser = self._robots.get(origin)
        if parser is None:
            lock = self._robots_locks.setdefault(origin, asyncio.Lock())
            async with lock:
                parser = self._robots.get(origin)
                if parser is None:
                    parser = await self._load_robots(origin)
                    self._robots[origin] = parser
        delay = float(parser.crawl_delay(self.config.user_agent) or 0)
        rate = parser.request_rate(self.config.user_agent)
        if rate is not None and rate.requests > 0:
            delay = max(delay, rate.seconds / rate.requests)
        self._crawl_delays[parts.netloc] = delay
        if not parser.can_fetch(self.config.user_agent, url):
            raise RobotsDeniedError(f"robots.txt запрещает {parts.path or '/'}")

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        params: Params = None,
        json_data: Any = None,
        form_data: Mapping[str, Any] | None = None,
        auth: tuple[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        use_cache: bool = True,
        check_robots: bool = True,
    ) -> Any:
        body = await self.request_text(
            method,
            url,
            params=params,
            json_data=json_data,
            form_data=form_data,
            auth=auth,
            headers=headers,
            use_cache=use_cache,
            check_robots=check_robots,
        )
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise ScraperError(f"Неверный JSON от {self._safe_url(url)}") from exc

    async def request_text(
        self,
        method: str,
        url: str,
        *,
        params: Params = None,
        json_data: Any = None,
        form_data: Mapping[str, Any] | None = None,
        auth: tuple[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        use_cache: bool = True,
        check_robots: bool = True,
    ) -> str:
        if check_robots:
            request_url = str(httpx.Request(method, url, params=params).url)
            await self.ensure_allowed(request_url)
        cache_key = self._cache_key(method, url, params, json_data, form_data, auth, headers)
        cached = self.storage.get_cached(cache_key, self.config.cache_ttl_seconds) if use_cache else None
        if cached is not None:
            return cached
        body = await self._request_with_retries(
            method,
            url,
            params=params,
            json_data=json_data,
            form_data=form_data,
            auth=auth,
            headers=headers,
        )
        if use_cache:
            self.storage.set_cached(cache_key, body)
        return body

    async def get_json(self, url: str, **kwargs: Any) -> Any:
        return await self.request_json("GET", url, **kwargs)

    async def get_text(self, url: str, **kwargs: Any) -> str:
        return await self.request_text("GET", url, **kwargs)

    async def post_json(self, url: str, **kwargs: Any) -> Any:
        return await self.request_json("POST", url, **kwargs)

    async def _request_with_retries(
        self,
        method: str,
        url: str,
        *,
        params: Params,
        json_data: Any,
        form_data: Mapping[str, Any] | None,
        auth: tuple[str, str] | None,
        headers: Mapping[str, str] | None,
    ) -> str:
        client = self._require_client()
        last_error: Exception | None = None
        for attempt in range(self.config.retries + 1):
            try:
                async with self._semaphore:
                    await self._throttle(url)
                    response = await client.request(
                        method,
                        url,
                        params=params,
                        json=json_data,
                        data=form_data,
                        auth=auth,
                        headers=headers,
                    )
                if response.status_code in self.RETRY_STATUSES:
                    if attempt < self.config.retries:
                        retry_after = response.headers.get("Retry-After")
                        delay = float(retry_after) if retry_after and retry_after.isdigit() else None
                        await self._backoff(attempt, delay)
                        continue
                    if response.status_code == 429:
                        raise SourceBlockedError(f"Лимит запросов {self._safe_url(url)} (429)")
                if response.status_code in {401, 403}:
                    raise SourceBlockedError(
                        f"Доступ отклонён {self._safe_url(url)} (HTTP {response.status_code}); "
                        "проверьте учётные данные и условия API"
                    )
                response.raise_for_status()
                return response.text
            except SourceBlockedError:
                raise
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                last_error = exc
                if attempt < self.config.retries:
                    await self._backoff(attempt)
                    continue
        raise ScraperError(f"Сетевой сбой {self._safe_url(url)}: {last_error}") from last_error

    async def _load_robots(self, origin: str) -> urllib.robotparser.RobotFileParser:
        client = self._require_client()
        url = f"{origin}/robots.txt"
        parser = urllib.robotparser.RobotFileParser(url)
        try:
            async with self._semaphore:
                await self._throttle(url)
                response = await client.get(url, headers={"Accept": "text/plain"})
        except httpx.HTTPError as exc:
            raise ScraperError(f"Не удалось проверить robots.txt для {origin}: {exc}") from exc
        if response.status_code in {401, 403}:
            parser.disallow_all = True
        elif 400 <= response.status_code < 500:
            parser.allow_all = True
        elif response.status_code >= 500:
            raise ScraperError(f"robots.txt для {origin} недоступен (HTTP {response.status_code})")
        else:
            parser.parse(response.text.splitlines())
        return parser

    async def _throttle(self, url: str) -> None:
        host = urlsplit(url).netloc
        lock = self._host_locks.setdefault(host, asyncio.Lock())
        async with lock:
            previous = self._last_request.get(host)
            if previous is not None:
                required = max(
                    random.uniform(self.config.delay_min, self.config.delay_max),
                    self._crawl_delays.get(host, 0),
                )
                remaining = required - (time.monotonic() - previous)
                if remaining > 0:
                    await asyncio.sleep(remaining)
            self._last_request[host] = time.monotonic()

    async def _backoff(self, attempt: int, explicit_delay: float | None = None) -> None:
        delay = explicit_delay if explicit_delay is not None else min(60, 2**attempt + random.random())
        await asyncio.sleep(min(delay, 60))

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("AsyncHttpClient must be used as an async context manager")
        return self._client

    @staticmethod
    def _cache_key(
        method: str,
        url: str,
        params: Params,
        json_data: Any,
        form_data: Mapping[str, Any] | None,
        auth: tuple[str, str] | None,
        headers: Mapping[str, str] | None,
    ) -> str:
        serializable_params = list(params) if isinstance(params, Sequence) and not isinstance(params, str) else params
        material = json.dumps(
            [method.upper(), url, serializable_params, json_data, form_data, auth, headers],
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def _safe_url(url: str) -> str:
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
