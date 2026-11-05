from openpyxl import load_workbook

from models import SourceFailure
from report import export_csv, export_excel, write_html_report


def test_all_report_files(tmp_path, make_tour, params) -> None:
    tour = make_tour(score=91.25, previous_price=160000)
    html_path = write_html_report(
        [tour], params, tmp_path / "report.html", [SourceFailure(source="broken", error="HTTP 503")], 1
    )
    excel_path = export_excel([tour], tmp_path / "report.xlsx")
    csv_path = export_csv([tour], tmp_path / "report.csv")
    content = html_path.read_text(encoding="utf-8")
    assert "Sunrise Resort Hotel" in content
    assert "Цена упала" in content
    assert "HTTP 503" in content
    assert "<svg" in content
    assert "<script src=" not in content
    assert load_workbook(excel_path)["Туры"]["A2"].value == 91.25
    assert "Sunrise Resort Hotel" in csv_path.read_text(encoding="utf-8-sig")
