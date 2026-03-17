import subprocess
import tempfile
import textwrap
from pathlib import Path
from typing import Optional

from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


PDF_WIDTH_MM = 72
PDF_HEIGHT_MM = 200

PDF_LEFT_MARGIN_MM = 3
PDF_RIGHT_MARGIN_MM = 3
PDF_TOP_MARGIN_MM = 4
PDF_BOTTOM_MARGIN_MM = 4

PDF_FONT_SIZE = 8

# Standardvärden, kan skrivas över från huvudfilen
PDF_LINE_SPACING = 1.2
PDF_FONT_PATH: Optional[str] = None


def register_pdf_font_if_needed(font_name: str, font_path: Optional[str] = None) -> None:
    """
    Registrerar en custom TTF/OTF-font om font_path anges.
    Om ingen font_path anges används bara ReportLabs standardfonter.
    """
    if font_path:
        pdfmetrics.registerFont(TTFont(font_name, font_path))


def create_receipt_pdf(
    lines: list[str],
    font_size: int = 12,
    font_name: str = "Helvetica",
    font_path: Optional[str] = None,
    line_spacing: float = PDF_LINE_SPACING,
) -> str:
    """
    Skapar en tillfällig PDF-fil med kvittolayout och returnerar sökvägen.
    """
    register_pdf_font_if_needed(font_name, font_path)

    width = PDF_WIDTH_MM * mm
    height = PDF_HEIGHT_MM * mm

    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    pdf_path = temp_file.name
    temp_file.close()

    c = canvas.Canvas(pdf_path, pagesize=(width, height))
    c.setFont(font_name, font_size)

    x = PDF_LEFT_MARGIN_MM * mm
    y = height - (PDF_TOP_MARGIN_MM * mm)

    line_height = font_size * line_spacing

    for line in lines:
        if y < PDF_BOTTOM_MARGIN_MM * mm:
            c.showPage()
            c.setFont(font_name, font_size)
            y = height - (PDF_TOP_MARGIN_MM * mm)

        c.drawString(x, y, line)
        y -= line_height

    c.save()
    return pdf_path


def _wrap_paragraph_centered(
    text: str,
    font_name: str,
    font_size: int,
    max_width_points: float,
) -> list[str]:
    """
    Wrappar ett stycke utifrån faktisk textbredd i PDF.
    """
    words = text.split()
    if not words:
        return [""]

    lines: list[str] = []
    current = words[0]

    for word in words[1:]:
        candidate = f"{current} {word}"
        candidate_width = pdfmetrics.stringWidth(candidate, font_name, font_size)

        if candidate_width <= max_width_points:
            current = candidate
        else:
            lines.append(current)
            current = word

    lines.append(current)
    return lines


def create_intro_pdf(
    intro_logo: str,
    intro_text: str,
    countdown_text: str,
    printer_name: Optional[str] = None,
    logo_font_name: str = "LogoFont",
    logo_font_path: Optional[str] = None,
    logo_font_size: int = 28,
    body_font_name: str = "Helvetica",
    body_font_path: Optional[str] = None,
    body_font_size: int = 11,
    countdown_font_name: str = "Helvetica",
    countdown_font_path: Optional[str] = None,
    countdown_font_size: int = 10,
    logo_top_margin_mm: float = 30,
    body_top_gap_mm: float = 22,
    paragraph_gap_mm: float = 10,
    countdown_gap_mm: float = 16,
    bottom_wave_gap_mm: float = 8,
    intro_bottom_whitespace_mm: float = 18,
    intro_page_height_mm: float = 260,
    wave_text: str = "~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~",
) -> str:
    """
    Skapar en speciallayoutad intro-PDF med:
    - centrerad logga
    - centrerad brödtext i stycken
    - countdown-rad
    - våglinje längst ner
    """
    register_pdf_font_if_needed(logo_font_name, logo_font_path)
    register_pdf_font_if_needed(body_font_name, body_font_path)
    register_pdf_font_if_needed(countdown_font_name, countdown_font_path)

    width = PDF_WIDTH_MM * mm
    #height = PDF_HEIGHT_MM * mm
    height = intro_page_height_mm * mm
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    pdf_path = temp_file.name
    temp_file.close()

    c = canvas.Canvas(pdf_path, pagesize=(width, height))
    page_center_x = width / 2

    usable_width = width - ((PDF_LEFT_MARGIN_MM + PDF_RIGHT_MARGIN_MM) * mm)

    # Startposition
    y = height - (logo_top_margin_mm * mm)

    # Logga
    c.setFont(logo_font_name, logo_font_size)
    c.drawCentredString(page_center_x, y, intro_logo)

    # Brödtext
    y -= body_top_gap_mm * mm
    paragraphs = intro_text.split("\n\n")

    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            y -= paragraph_gap_mm * mm * 0.6
            continue

        wrapped_lines = _wrap_paragraph_centered(
            paragraph,
            font_name=body_font_name,
            font_size=body_font_size,
            max_width_points=usable_width * 0.86,
        )

        c.setFont(body_font_name, body_font_size)
        line_height = body_font_size * 1.2  # line spacing-multiplikator

        for line in wrapped_lines:
            c.drawCentredString(page_center_x, y, line)
            y -= line_height

        y -= paragraph_gap_mm * mm

    # Countdown
    y -= countdown_gap_mm * mm
    c.setFont(countdown_font_name, countdown_font_size)
    c.drawCentredString(page_center_x, y, countdown_text)

    # Våglinje
    y -= bottom_wave_gap_mm * mm
    c.drawCentredString(page_center_x, y, wave_text)

    # Extra luft nederst
    y -= intro_bottom_whitespace_mm * mm
    _ = y

    c.save()
    return pdf_path


def send_pdf_to_printer(pdf_path: str, printer_name: Optional[str] = None) -> None:
    """
    Skickar PDF-filen till skrivaren via lp.
    """
    cmd = ["lp"]

    if printer_name:
        cmd.extend(["-d", printer_name])

    cmd.append(pdf_path)

    subprocess.run(cmd, check=True)


def print_lines_as_pdf(
    lines: list[str],
    printer_name: Optional[str] = None,
    font_size: int = 12,
    font_name: str = "Helvetica",
    font_path: Optional[str] = None,
    line_spacing: float = PDF_LINE_SPACING,
) -> None:
    """
    Skapar PDF av raderna och skickar den till skrivaren.
    """
    pdf_path = create_receipt_pdf(
        lines=lines,
        font_size=font_size,
        font_name=font_name,
        font_path=font_path,
        line_spacing=line_spacing,
    )
    send_pdf_to_printer(pdf_path, printer_name)


def print_intro_pdf(
    intro_logo: str,
    intro_text: str,
    countdown_text: str,
    printer_name: Optional[str] = None,
    logo_font_name: str = "LogoFont",
    logo_font_path: Optional[str] = None,
    logo_font_size: int = 28,
    body_font_name: str = "Helvetica",
    body_font_path: Optional[str] = None,
    body_font_size: int = 11,
    countdown_font_name: str = "Helvetica",
    countdown_font_path: Optional[str] = None,
    countdown_font_size: int = 10,
    logo_top_margin_mm: float = 30,
    body_top_gap_mm: float = 22,
    paragraph_gap_mm: float = 10,
    countdown_gap_mm: float = 16,
    bottom_wave_gap_mm: float = 8,
    intro_bottom_whitespace_mm: float = 18,
    intro_page_height_mm: float = 260,
    wave_text: str = "~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~",
) -> None:
    """
    Skapar intro-PDF och skickar den till skrivaren.
    """
    pdf_path = create_intro_pdf(
        intro_logo=intro_logo,
        intro_text=intro_text,
        countdown_text=countdown_text,
        printer_name=printer_name,
        logo_font_name=logo_font_name,
        logo_font_path=logo_font_path,
        logo_font_size=logo_font_size,
        body_font_name=body_font_name,
        body_font_path=body_font_path,
        body_font_size=body_font_size,
        countdown_font_name=countdown_font_name,
        countdown_font_path=countdown_font_path,
        countdown_font_size=countdown_font_size,
        logo_top_margin_mm=logo_top_margin_mm,
        body_top_gap_mm=body_top_gap_mm,
        paragraph_gap_mm=paragraph_gap_mm,
        countdown_gap_mm=countdown_gap_mm,
        bottom_wave_gap_mm=bottom_wave_gap_mm,
        intro_bottom_whitespace_mm=intro_bottom_whitespace_mm,
        intro_page_height_mm=intro_page_height_mm,
        wave_text=wave_text,
    )
    send_pdf_to_printer(pdf_path, printer_name)
