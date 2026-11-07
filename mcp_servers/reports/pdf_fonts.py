"""Embedded fonts shared by both PDF tools; no OS font installation required."""
from pathlib import Path
from threading import Lock


REGULAR = "PlannerSans"
BOLD = "PlannerSans-Bold"
_LOCK = Lock()


def pdf_styles():
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    font_dir = Path(__file__).parent / "fonts"
    with _LOCK:
        for name, filename in ((REGULAR, "DejaVuSans.ttf"), (BOLD, "DejaVuSans-Bold.ttf")):
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, str(font_dir / filename)))
    styles = getSampleStyleSheet()
    styles["Normal"].fontName = REGULAR
    styles["Title"].fontName = BOLD
    return styles
