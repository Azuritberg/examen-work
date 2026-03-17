import subprocess
import tempfile
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

    usable_width = width - ((PDF_LEFT_MARGIN_MM + PDF_RIGHT_MARGIN_MM) * mm)
    _ = usable_width  # sparad för ev. senare utveckling

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













# ---------------------------------------------------------- #
# Gamlman version av print_lines_as_pdf


# import subprocess
# import tempfile
# from pathlib import Path
# from typing import Optional

# from reportlab.pdfgen import canvas
# from reportlab.lib.units import mm
# from reportlab.pdfbase import pdfmetrics
# from reportlab.pdfbase.ttfonts import TTFont


# PDF_WIDTH_MM = 72
# PDF_HEIGHT_MM = 200

# PDF_LEFT_MARGIN_MM = 3
# PDF_RIGHT_MARGIN_MM = 3
# PDF_TOP_MARGIN_MM = 4
# PDF_BOTTOM_MARGIN_MM = 4


# def create_receipt_pdf(
#     lines: list[str],
#     font_size: int = 8,
#     font_name: str = "Helvetica",
# ) -> str:
#     """
#     Skapar en tillfällig PDF-fil med kvittolayout och returnerar sökvägen.
#     """
#     width = PDF_WIDTH_MM * mm
#     height = PDF_HEIGHT_MM * mm

#     temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
#     pdf_path = temp_file.name
#     temp_file.close()

#     c = canvas.Canvas(pdf_path, pagesize=(width, height))
#     c.setFont(font_name, font_size)

#     x = PDF_LEFT_MARGIN_MM * mm
#     y = height - (PDF_TOP_MARGIN_MM * mm)

#     # Lite tätare eller luftigare radavstånd
#     line_height = font_size + 2 # 2, 4, eller 6 kan vara bra att testa

#     for line in lines:
#         if y < PDF_BOTTOM_MARGIN_MM * mm:
#             c.showPage()
#             c.setFont(font_name, font_size)
#             y = height - (PDF_TOP_MARGIN_MM * mm)

#         c.drawString(x, y, line)
#         y -= line_height

#     c.save()
#     return pdf_path


# def send_pdf_to_printer(pdf_path: str, printer_name: Optional[str] = None) -> None:
#     """
#     Skickar PDF-filen till skrivaren via lp.
#     """
#     cmd = ["lp"]

#     if printer_name:
#         cmd.extend(["-d", printer_name])

#     cmd.append(pdf_path)

#     subprocess.run(cmd, check=True)


# def print_lines_as_pdf(
#     lines: list[str],
#     printer_name: Optional[str] = None,
#     font_size: int = 14,
#     font_name: str = "Helvetica",
# ) -> None:
#     """
#     Skapar PDF av raderna och skickar den till skrivaren.
#     """
#     pdf_path = create_receipt_pdf(
#         lines=lines,
#         font_size=font_size,
#         font_name=font_name,
#     )
#     send_pdf_to_printer(pdf_path, printer_name)