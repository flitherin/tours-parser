from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from models import Tour


def tours_dataframe(tours: Iterable[Tour]) -> pd.DataFrame:
    rows = [
        {
            "Скоринг": tour.score,
            "Источник": tour.source,
            "Другие источники": ", ".join(tour.available_on),
            "Ссылка": tour.url,
            "Город вылета": tour.departure_city,
            "Страна": tour.country,
            "Курорт": tour.resort,
            "Отель": tour.hotel_name,
            "Звёзды": tour.stars,
            "Рейтинг": tour.rating,
            "Питание": tour.meal,
            "Дата вылета": tour.departure_date.isoformat(),
            "Ночей": tour.nights,
            "Номер": tour.room_type,
            "Цена за всех, RUB": tour.price_total,
            "Цена за человека, RUB": tour.price_per_person,
            "RUB/чел./ночь": round(tour.price_per_person_per_night, 2),
            "Перелёт": tour.includes_flight,
            "Трансфер": tour.includes_transfer,
            "Страховка": tour.includes_insurance,
            "Багаж": tour.luggage,
            "Прямой перелёт": tour.direct_flight,
            "Время вылета": tour.departure_time.isoformat() if tour.departure_time else None,
            "Цена упала": tour.price_dropped,
            "Прошлая цена, RUB": tour.previous_price,
            "Собрано": tour.collected_at.isoformat(),
            "Почему выгодно": "; ".join(tour.score_reasons),
        }
        for tour in tours
    ]
    return pd.DataFrame(rows)


def export_csv(tours: Iterable[Tour], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tours_dataframe(tours).to_csv(path, index=False, encoding="utf-8-sig")
    return path


def export_excel(tours: Iterable[Tour], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = tours_dataframe(tours)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        frame.to_excel(writer, sheet_name="Туры", index=False)
        worksheet = writer.sheets["Туры"]
        worksheet.freeze_panes = "A2"
        worksheet.auto_filter.ref = worksheet.dimensions
        for cell in worksheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="176B87")
        for column in range(1, worksheet.max_column + 1):
            values = [str(worksheet.cell(row, column).value or "") for row in range(1, worksheet.max_row + 1)]
            worksheet.column_dimensions[get_column_letter(column)].width = min(max(map(len, values), default=8) + 2, 55)
        for row in range(2, worksheet.max_row + 1):
            worksheet.cell(row, 1).number_format = "0.00"
    return path
