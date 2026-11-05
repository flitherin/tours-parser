from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
from typing import Any

from http_client import AsyncHttpClient
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


def discover_scrapers(client: AsyncHttpClient, settings: dict[str, dict[str, Any]]) -> list[BaseScraper]:
    """Instantiate every BaseScraper subclass placed in this package."""

    result: list[BaseScraper] = []
    for module_info in pkgutil.iter_modules(__path__):
        if module_info.name.startswith("_") or module_info.name in {"base", "browser"}:
            continue
        module = importlib.import_module(f"{__name__}.{module_info.name}")
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if cls is BaseScraper or not issubclass(cls, BaseScraper) or cls.__module__ != module.__name__:
                continue
            source_settings = settings.get(cls.name, {})
            if not cls.enabled or not source_settings.get("enabled", True):
                continue
            result.append(cls(client, source_settings))
    return sorted(result, key=lambda scraper: scraper.name)
