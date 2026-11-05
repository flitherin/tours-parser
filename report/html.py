from __future__ import annotations

import html
import statistics
from datetime import datetime
from pathlib import Path
from typing import Sequence

from models import SearchParams, SourceFailure, Tour


def write_html_report(
    tours: Sequence[Tour],
    params: SearchParams,
    path: Path,
    failures: Sequence[SourceFailure],
    raw_count: int,
) -> Path:
    """Write a self-contained report with inline CSS, JS and SVG."""

    path.parent.mkdir(parents=True, exist_ok=True)
    prices = [tour.price_total for tour in tours]
    summary = _summary(prices, raw_count)
    cards = "\n".join(_card(tour, index) for index, tour in enumerate(tours[:10], 1))
    rows = "\n".join(_row(tour) for tour in tours)
    errors = "".join(
        f"<li><strong>{html.escape(item.source)}</strong>: {html.escape(item.error)}</li>" for item in failures
    )
    error_block = f'<section class="errors"><h2>Недоступные источники</h2><ul>{errors}</ul></section>' if errors else ""
    chart = _price_histogram(prices)
    document = f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Туры: {html.escape(params.departure_city)} → {html.escape(params.country)}</title>
<style>
:root{{--bg:#f4f7f8;--card:#fff;--ink:#17323b;--muted:#65777e;--accent:#087f8c;--accent2:#12a594;--line:#dce6e9;--good:#18794e}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:1440px;margin:auto;padding:28px}}h1{{margin:0 0 6px;font-size:30px}}h2{{margin-top:32px}}.muted{{color:var(--muted)}}
.summary{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:22px 0}}.metric,.card,section{{background:var(--card);border:1px solid var(--line);border-radius:14px}}
.metric{{padding:16px}}.metric b{{display:block;font-size:23px}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px}}
.card{{padding:18px;position:relative}}.card h3{{margin:4px 0 8px;font-size:19px}}.rank{{color:var(--accent);font-weight:700}}.price{{font-size:24px;font-weight:750;margin:12px 0}}
.badge{{display:inline-block;background:#e3f4ef;color:var(--good);padding:3px 8px;border-radius:20px;font-size:12px}}a.button{{display:inline-block;background:var(--accent);color:white;text-decoration:none;padding:9px 13px;border-radius:8px;margin-top:12px}}
section{{padding:18px;margin-top:20px;overflow:auto}}.filters{{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:14px}}input,select{{padding:9px;border:1px solid var(--line);border-radius:7px;background:white}}
table{{border-collapse:collapse;width:100%;white-space:nowrap}}th,td{{padding:9px;border-bottom:1px solid var(--line);text-align:left}}th{{position:sticky;top:0;background:#eaf2f4;cursor:pointer;user-select:none}}tr:hover td{{background:#f5fbfb}}td.num{{text-align:right}}a{{color:var(--accent)}}svg{{max-width:100%;height:auto}}.errors{{border-color:#e5b8b8}}.empty{{padding:24px;text-align:center}}
@media(max-width:650px){{main{{padding:14px}}h1{{font-size:24px}}}}
</style>
</head>
<body><main>
<h1>{html.escape(params.departure_city)} → {html.escape(params.country)}</h1>
<div class="muted">{params.date_from} — {params.date_to} · {params.nights_min}–{params.nights_max} ночей · {params.traveler_count} туристов · отчёт создан {datetime.now().astimezone():%Y-%m-%d %H:%M}</div>
<div class="summary">{summary}</div>
{error_block}
<h2>Топ-10 по выгодности</h2>
<div class="cards">{cards or '<div class="empty">Нет предложений по заданным фильтрам</div>'}</div>
<section><h2>Распределение цен за всех</h2>{chart}</section>
<section><h2>Все предложения</h2>
<div class="filters"><input id="query" type="search" placeholder="Отель или курорт" aria-label="Поиск по отелю"><select id="stars" aria-label="Звёзды"><option value="">Любые звёзды</option><option>3</option><option>4</option><option>5</option></select><select id="meal" aria-label="Питание"><option value="">Любое питание</option><option>RO</option><option>BB</option><option>HB</option><option>FB</option><option>AI</option><option>UAI</option></select><input id="maxPrice" type="number" min="0" placeholder="Макс. цена, ₽" aria-label="Максимальная цена"></div>
<table id="tours"><thead><tr>
<th data-type="num">Скор</th><th>Отель</th><th>Курорт</th><th data-type="num">Звёзды</th><th data-type="num">Рейтинг</th><th>Питание</th><th>Дата</th><th data-type="num">Ночи</th><th data-type="num">Цена, ₽</th><th data-type="num">₽/чел./ночь</th><th>Источники</th><th>Ссылка</th>
</tr></thead><tbody>{rows}</tbody></table></section>
</main>
<script>
const table=document.getElementById('tours'),body=table.querySelector('tbody');
const inputs=['query','stars','meal','maxPrice'].map(id=>document.getElementById(id));
function filterRows(){{const q=inputs[0].value.toLowerCase(),stars=inputs[1].value,meal=inputs[2].value,max=Number(inputs[3].value||Infinity);body.querySelectorAll('tr').forEach(row=>{{row.hidden=!(row.dataset.search.includes(q)&&(!stars||row.dataset.stars===stars)&&(!meal||row.dataset.meal===meal)&&Number(row.dataset.price)<=max)}})}}
inputs.forEach(input=>input.addEventListener('input',filterRows));
table.querySelectorAll('th').forEach((th,index)=>th.addEventListener('click',()=>{{const asc=th.dataset.asc!=='true';th.dataset.asc=String(asc);const numeric=th.dataset.type==='num';[...body.rows].sort((a,b)=>{{let x=a.cells[index].dataset.value??a.cells[index].textContent.trim(),y=b.cells[index].dataset.value??b.cells[index].textContent.trim();const result=numeric?Number(x||0)-Number(y||0):x.localeCompare(y,'ru');return asc?result:-result}}).forEach(row=>body.appendChild(row))}}));
</script>
</body></html>"""
    path.write_text(document, encoding="utf-8")
    return path


def _summary(prices: list[float], raw_count: int) -> str:
    metrics = [("Найдено после фильтров", str(raw_count)), ("Уникальных предложений", str(len(prices)))]
    if prices:
        metrics.extend(
            (
                ("Минимальная цена", f"{min(prices):,.0f} ₽"),
                ("Средняя цена", f"{statistics.mean(prices):,.0f} ₽"),
                ("Медианная цена", f"{statistics.median(prices):,.0f} ₽"),
            )
        )
    return "".join(f'<div class="metric"><span>{label}</span><b>{value}</b></div>' for label, value in metrics)


def _card(tour: Tour, rank: int) -> str:
    drop = f'<span class="badge">Цена упала на {tour.price_drop_percent:.1f}%</span>' if tour.price_dropped else ""
    rating = f" · рейтинг {tour.rating:.1f}/10" if tour.rating is not None else ""
    reasons = html.escape("; ".join(tour.score_reasons)) or "Сбалансированное предложение"
    return f"""<article class="card"><span class="rank">#{rank} · {tour.score:.1f}/100</span><h3>{html.escape(tour.hotel_name)}</h3>
<div>{tour.stars or '—'}★{rating} · {html.escape(tour.resort or tour.country)}</div><div>{tour.departure_date} · {tour.nights} ночей · {html.escape(tour.meal or '—')}</div>
<div class="price">{tour.price_total:,.0f} ₽</div><div>{tour.price_per_person_per_night:,.0f} ₽/чел./ночь</div><p class="muted">{reasons}</p>{drop}<br><a class="button" href="{html.escape(tour.url, quote=True)}" target="_blank" rel="noopener noreferrer">Перейти к предложению</a><div class="muted">{html.escape(', '.join(tour.available_on))}</div></article>"""


def _row(tour: Tour) -> str:
    search = html.escape(f"{tour.hotel_name} {tour.resort}".casefold(), quote=True)
    cells = [
        (f"{tour.score:.2f}", str(tour.score), "num"),
        (html.escape(tour.hotel_name), tour.hotel_name, ""),
        (html.escape(tour.resort or "—"), tour.resort, ""),
        (str(tour.stars or "—"), str(tour.stars or 0), "num"),
        (f"{tour.rating:.1f}" if tour.rating is not None else "—", str(tour.rating or 0), "num"),
        (html.escape(tour.meal or "—"), tour.meal or "", ""),
        (tour.departure_date.isoformat(), tour.departure_date.isoformat(), ""),
        (str(tour.nights), str(tour.nights), "num"),
        (f"{tour.price_total:,.0f}", str(tour.price_total), "num"),
        (f"{tour.price_per_person_per_night:,.0f}", str(tour.price_per_person_per_night), "num"),
        (html.escape(", ".join(tour.available_on)), "", ""),
        (f'<a href="{html.escape(tour.url, quote=True)}" target="_blank" rel="noopener noreferrer">Открыть</a>', "", ""),
    ]
    content = "".join(f'<td class="{css}" data-value="{html.escape(value, quote=True)}">{label}</td>' for label, value, css in cells)
    return f'<tr data-search="{search}" data-stars="{tour.stars or ""}" data-meal="{html.escape(tour.meal or "", quote=True)}" data-price="{tour.price_total}">{content}</tr>'


def _price_histogram(prices: list[float]) -> str:
    if not prices:
        return '<div class="empty">Недостаточно данных для графика</div>'
    width, height, padding = 900, 280, 45
    bins = min(10, max(1, len(prices)))
    low, high = min(prices), max(prices)
    span = high - low or 1
    counts = [0] * bins
    for price in prices:
        index = min(bins - 1, int((price - low) / span * bins))
        counts[index] += 1
    maximum = max(counts)
    chart_width, chart_height = width - 2 * padding, height - 2 * padding
    bar_width = chart_width / bins
    rectangles = []
    for index, count in enumerate(counts):
        bar_height = chart_height * count / maximum
        x = padding + index * bar_width
        y = height - padding - bar_height
        label = low + (index + 0.5) * span / bins
        rectangles.append(f'<rect x="{x + 2:.1f}" y="{y:.1f}" width="{bar_width - 4:.1f}" height="{bar_height:.1f}" rx="3" fill="#12a594"><title>{count} предложений</title></rect><text x="{x + bar_width / 2:.1f}" y="{height - 23}" text-anchor="middle" font-size="11">{label / 1000:.0f}k</text><text x="{x + bar_width / 2:.1f}" y="{y - 5:.1f}" text-anchor="middle" font-size="12">{count}</text>')
    return f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Гистограмма цен"><line x1="{padding}" y1="{height - padding}" x2="{width - padding}" y2="{height - padding}" stroke="#65777e"/>{"".join(rectangles)}</svg>'
