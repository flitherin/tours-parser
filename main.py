from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from pydantic import ValidationError
from rich.console import Console
from rich.logging import RichHandler

from config import AppConfig, load_config
from models import SearchParams, SourceFailure, Tour
from normalizer import deduplicate_tours, filter_tours
from report import export_csv, export_excel, print_console_report, write_html_report
from scoring import score_tours, sort_tours
from scrapers import discover_scrapers
from scrapers.base import BaseScraper
from http_client import AsyncHttpClient
from storage import Storage

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Поиск и сравнение туров из российских городов")
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.yaml"))
    parser.add_argument("--from", dest="departure_city", help="Город вылета")
    parser.add_argument("--to", dest="country", help="Страна назначения")
    parser.add_argument("--date-from", help="Первая дата вылета, YYYY-MM-DD")
    parser.add_argument("--date-to", help="Последняя дата вылета, YYYY-MM-DD")
    parser.add_argument("--nights", help="Длительность, например 7-10")
    parser.add_argument("--adults", type=int, help="Количество взрослых")
    parser.add_argument("--children-ages", help="Возрасты детей через запятую, например 4,9")
    parser.add_argument("--stars", help="Категория отеля, например 4-5")
    parser.add_argument("--meal", type=str.upper, choices=("RO", "BB", "HB", "FB", "AI", "UAI"))
    parser.add_argument("--budget", "--max-budget", dest="max_budget", type=float, help="Максимум RUB за всех")
    parser.add_argument("--min-rating", type=float, help="Минимальный рейтинг отеля, 0-10")
    parser.add_argument("--sources", help="Источники через запятую (имена из config.yaml)")
    parser.add_argument("--sort", choices=("value", "price"), help="value — выгодность, price — чистая цена")
    parser.add_argument("--top", type=int, help="Сколько строк показать в консоли")
    parser.add_argument("--output-dir", type=Path, help="Каталог отчётов")
    return parser


def parse_range(value: str, label: str) -> tuple[int, int]:
    match = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+)\s*)?", value)
    if not match:
        raise ValueError(f"{label}: ожидается N или N-M")
    start = int(match.group(1))
    end = int(match.group(2) or start)
    if end < start:
        raise ValueError(f"{label}: верхняя граница меньше нижней")
    return start, end


def search_params(args: argparse.Namespace, config: AppConfig) -> SearchParams:
    raw: dict[str, Any] = dict(config.search)
    for name in (
        "departure_city",
        "country",
        "date_from",
        "date_to",
        "adults",
        "meal",
        "max_budget",
        "min_rating",
    ):
        value = getattr(args, name)
        if value is not None:
            raw[name] = value
    if args.nights:
        raw["nights_min"], raw["nights_max"] = parse_range(args.nights, "nights")
    if args.stars:
        raw["min_stars"], raw["max_stars"] = parse_range(args.stars, "stars")
    if args.children_ages is not None:
        raw["children_ages"] = [int(value.strip()) for value in args.children_ages.split(",") if value.strip()]
    return SearchParams.model_validate(raw)


async def _run_source(scraper: BaseScraper, params: SearchParams) -> tuple[list[Tour], SourceFailure | None]:
    try:
        tours = await scraper.search(params)
        logger.info("%s: получено %s предложений", scraper.name, len(tours))
        return tours, None
    except Exception as exc:
        logger.exception("Ошибка источника %s", scraper.name)
        return [], SourceFailure(source=scraper.name, error=str(exc))


async def collect_tours(
    params: SearchParams,
    config: AppConfig,
    selected_sources: set[str] | None = None,
) -> tuple[list[Tour], list[SourceFailure], int, Storage, datetime]:
    run_started = datetime.now(timezone.utc)
    storage = Storage(config.storage.database)
    source_settings = {
        name: {**values, "rub_rates": config.currency.rub_rates}
        for name, values in config.sources.items()
    }
    async with AsyncHttpClient(config.network, storage) as client:
        scrapers = discover_scrapers(client, source_settings)
        if selected_sources:
            known = {scraper.name for scraper in scrapers}
            unknown = selected_sources - known
            if unknown:
                raise ValueError(f"Неизвестные или отключённые источники: {', '.join(sorted(unknown))}")
            scrapers = [scraper for scraper in scrapers if scraper.name in selected_sources]
        if not scrapers:
            raise ValueError("Нет включённых источников")
        results = await asyncio.gather(*(_run_source(scraper, params) for scraper in scrapers))
    all_tours = [tour for source_tours, _ in results for tour in source_tours]
    failures = [failure for _, failure in results if failure is not None]
    filtered = filter_tours(all_tours, params)
    unique = deduplicate_tours(filtered, config.scoring.fuzzy_threshold)
    previous = storage.previous_prices((tour.dedup_key for tour in unique), run_started)
    for tour in unique:
        tour.previous_price = previous.get(tour.dedup_key)
    storage.save_price_history(unique, run_started.isoformat(), run_started)
    storage.cleanup_cache(config.network.cache_ttl_seconds)
    scored = score_tours(unique, config.scoring.weights)
    return scored, failures, len(filtered), storage, run_started


def setup_logging(config: AppConfig) -> None:
    config.logging.file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, config.logging.level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(config.logging.file, encoding="utf-8"),
            RichHandler(level=logging.WARNING, show_path=False),
        ],
        force=True,
    )


async def execute(args: argparse.Namespace, config: AppConfig) -> int:
    params = search_params(args, config)
    selected = {item.strip() for item in args.sources.split(",") if item.strip()} if args.sources else None
    tours, failures, raw_count, _, started = await collect_tours(params, config, selected)
    mode = args.sort or config.report.sort
    tours = sort_tours(tours, mode)
    output_dir = args.output_dir or config.report.output_dir
    stem = f"tours_{started:%Y%m%d_%H%M%S}"
    html_path = write_html_report(tours, params, output_dir / f"{stem}.html", failures, raw_count)
    excel_path = export_excel(tours, output_dir / f"{stem}.xlsx")
    csv_path = export_csv(tours, output_dir / f"{stem}.csv")
    print_console_report(
        tours,
        params,
        failures,
        raw_count,
        args.top or config.report.top,
        (html_path, excel_path, csv_path),
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        setup_logging(config)
        search_params(args, config)
        return asyncio.run(execute(args, config))
    except (ValidationError, ValueError) as exc:
        parser.error(str(exc))
    except (OSError, RuntimeError) as exc:
        Console().print(f"[red]Ошибка:[/red] {exc}")
        logger.exception("Необработанная ошибка")
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
