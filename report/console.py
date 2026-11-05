from __future__ import annotations

import statistics
from pathlib import Path
from typing import Sequence

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from models import SearchParams, SourceFailure, Tour


def print_console_report(
    tours: Sequence[Tour],
    params: SearchParams,
    failures: Sequence[SourceFailure],
    raw_count: int,
    top: int,
    files: Sequence[Path] = (),
) -> None:
    console = Console()
    if tours:
        prices = [tour.price_total for tour in tours]
        summary = (
            f"Найдено после фильтров: {raw_count} · уникальных: {len(tours)}\n"
            f"Цена за всех: мин. {min(prices):,.0f} ₽ · средняя {statistics.mean(prices):,.0f} ₽ · "
            f"медиана {statistics.median(prices):,.0f} ₽"
        )
    else:
        summary = "Подходящих предложений не найдено"
    console.print(
        Panel(
            summary,
            title=f"{params.departure_city} → {params.country} · {params.date_from}—{params.date_to}",
            border_style="cyan",
        )
    )
    if failures:
        for failure in failures:
            console.print(f"[yellow]Источник {failure.source} недоступен:[/yellow] {failure.error}")
    if tours:
        table = Table(title=f"Топ-{min(top, len(tours))}", box=box.ROUNDED, show_lines=False)
        for column in ("#", "Скор", "Отель", "Курорт", "Вылет", "Ночи", "Пит.", "Цена", "₽/чел./ночь", "Источники"):
            table.add_column(column)
        for index, tour in enumerate(tours[:top], 1):
            hotel = tour.hotel_name
            if tour.stars:
                hotel += f" {tour.stars}★"
            if tour.rating is not None:
                hotel += f" · {tour.rating:.1f}/10"
            if tour.price_dropped and tour.price_drop_percent is not None:
                hotel += f"\n[green]↓ {tour.price_drop_percent:.1f}% с прошлого запуска[/green]"
            table.add_row(
                str(index),
                f"{tour.score:.1f}",
                hotel,
                tour.resort or "—",
                tour.departure_date.isoformat(),
                str(tour.nights),
                tour.meal or "—",
                f"{tour.price_total:,.0f} ₽",
                f"{tour.price_per_person_per_night:,.0f}",
                ", ".join(tour.available_on),
            )
        console.print(table)
    for path in files:
        console.print(f"Отчёт: [link={path.as_uri()}]{path}[/link]")
