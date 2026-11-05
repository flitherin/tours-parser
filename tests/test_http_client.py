import httpx
import pytest

from config import NetworkConfig
from errors import RobotsDeniedError
from http_client import AsyncHttpClient
from storage import Storage


@pytest.mark.asyncio
async def test_json_cache_and_robots(tmp_path) -> None:
    api_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal api_calls
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /blocked")
        if request.url.path == "/api":
            api_calls += 1
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404)

    config = NetworkConfig(delay_min=0, delay_max=0, cache_ttl_seconds=60)
    storage = Storage(tmp_path / "cache.db")
    async with AsyncHttpClient(config, storage, httpx.MockTransport(handler)) as client:
        assert await client.get_json("https://example.com/api") == {"ok": True}
        assert await client.get_json("https://example.com/api") == {"ok": True}
        with pytest.raises(RobotsDeniedError):
            await client.get_json("https://example.com/blocked")
    assert api_calls == 1


@pytest.mark.asyncio
async def test_robots_crawl_delay_overrides_default_delay(tmp_path, monkeypatch) -> None:
    from types import SimpleNamespace

    clock = [0.0]
    requested_at = []

    async def advance(seconds: float) -> None:
        clock[0] += seconds

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nCrawl-delay: 10\nDisallow: /restart.php")
        requested_at.append(clock[0])
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr("http_client.time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr("http_client.asyncio.sleep", advance)
    config = NetworkConfig(delay_min=1, delay_max=3)
    async with AsyncHttpClient(config, Storage(tmp_path / "c.db"), httpx.MockTransport(handler)) as client:
        await client.get_json("https://example.com/api", use_cache=False)
        await client.get_json("https://example.com/api", use_cache=False)
    assert requested_at == [10.0, 20.0]


@pytest.mark.asyncio
async def test_query_string_and_cached_data_respect_robots(tmp_path) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /api?private=1")
        return httpx.Response(200, json={"ok": True})

    config = NetworkConfig(delay_min=0, delay_max=0)
    async with AsyncHttpClient(config, Storage(tmp_path / "c.db"), httpx.MockTransport(handler)) as client:
        await client.get_json("https://example.com/api", params={"private": 1}, check_robots=False)
        with pytest.raises(RobotsDeniedError):
            await client.get_json("https://example.com/api", params={"private": 1})
    assert len(calls) == 2
