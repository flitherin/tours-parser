from __future__ import annotations

from bs4 import BeautifulSoup
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from errors import ScraperError, SourceBlockedError
from http_client import AsyncHttpClient


class BrowserClient:
    """Optional Playwright transport for future sources without a usable API."""

    def __init__(self, http: AsyncHttpClient) -> None:
        self.http = http

    async def fetch_html(self, url: str, wait_selector: str | None = None) -> str:
        await self.http.ensure_allowed(url)
        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                try:
                    context = await browser.new_context(user_agent=self.http.config.user_agent)
                    page = await context.new_page()
                    await page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=int(self.http.config.timeout * 1000),
                    )
                    if wait_selector:
                        await page.wait_for_selector(wait_selector, timeout=int(self.http.config.timeout * 1000))
                    title = (await page.title()).casefold()
                    if "captcha" in title or "access denied" in title or "доступ запрещ" in title:
                        raise SourceBlockedError(f"Страница сообщает о блокировке: {await page.title()}")
                    return await page.content()
                finally:
                    await browser.close()
        except PlaywrightTimeoutError as exc:
            raise ScraperError(f"Тайм-аут загрузки динамической страницы: {url}") from exc

    async def fetch_soup(self, url: str, wait_selector: str | None = None) -> BeautifulSoup:
        return BeautifulSoup(await self.fetch_html(url, wait_selector), "lxml")
