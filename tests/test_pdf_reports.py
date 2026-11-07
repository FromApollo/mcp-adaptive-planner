from pypdf import PdfReader
import pytest

from mcp_servers.reports.server import create_pdf, create_pdf_with_table


@pytest.mark.parametrize("table", [False, True])
def test_pdf_round_trips_cyrillic_and_embeds_fonts(tmp_path, table):
    target = tmp_path / "report.pdf"
    title = "Отчёт о состоянии компьютера Ёё Ъъ Ыы Ээ"
    if table:
        create_pdf_with_table(title, "Показатель,Значение", "Память,Восемь гигабайт;Диск,Свободное место", str(target))
        expected = [title, "Показатель", "Значение", "Память", "Восемь гигабайт", "Диск", "Свободное место"]
    else:
        content = "Проверка русского текста: Ёжик, съёмка, объём.\nПамять: 16 ГБ; CPU: 25%."
        create_pdf(title, content, str(target))
        expected = [title, *content.splitlines()]
    reader = PdfReader(target)
    extracted = "\n".join(page.extract_text() for page in reader.pages)
    for text in expected:
        assert text in extracted
    embedded = []
    for page in reader.pages:
        for ref in page["/Resources"]["/Font"].values():
            font = ref.get_object()
            if "DejaVuSans" in str(font.get("/BaseFont", "")):
                descriptor = font["/FontDescriptor"]
                assert "/FontFile2" in descriptor
                assert "/ToUnicode" in font
                embedded.append(str(font["/BaseFont"]))
    assert any("Bold" in name for name in embedded)
    assert any("Bold" not in name for name in embedded)


def test_pdf_fonts_cover_entire_russian_alphabet():
    from reportlab.pdfbase import pdfmetrics
    from mcp_servers.reports.pdf_fonts import pdf_styles, REGULAR, BOLD
    pdf_styles()
    for name in (REGULAR, BOLD):
        font = pdfmetrics.getFont(name)
        assert all(font.face.charToGlyph.get(code) for code in [*range(0x410, 0x450), 0x401, 0x451])


def test_pdf_can_be_created_outside_project_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    create_pdf("Проверка шрифтов", "Русский текст остаётся читаемым.", "report.pdf")
    assert "Русский текст остаётся читаемым." in PdfReader("report.pdf").pages[0].extract_text()
