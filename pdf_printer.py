import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from reportlab.pdfgen import canvas
from reportlab.lib.units import mm


PDF_WIDTH_MM = 80
PDF_HEIGHT_MM = 200
PDF_LEFT_MARGIN_MM = 5
PDF_TOP_MARGIN_MM = 10
PDF_BOTTOM_MARGIN_MM = 10


def create_receipt_pdf(
    lines: list[str],
    font_size: int = 14,
    font_name: str = "Helvetica",
) -> str:
    """
    Skapar en tillfällig PDF-fil med kvittolayout och returnerar sökvägen.
    """
    width = PDF_WIDTH_MM * mm
    height = PDF_HEIGHT_MM * mm

    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    pdf_path = temp_file.name
    temp_file.close()

    c = canvas.Canvas(pdf_path, pagesize=(width, height))
    c.setFont(font_name, font_size)

    x = PDF_LEFT_MARGIN_MM * mm
    y = height - (PDF_TOP_MARGIN_MM * mm)

    line_height = font_size + 4

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
    font_size: int = 14,
    font_name: str = "Helvetica",
) -> None:
    """
    Skapar PDF av raderna och skickar den till skrivaren.
    """
    pdf_path = create_receipt_pdf(
        lines=lines,
        font_size=font_size,
        font_name=font_name,
    )
    send_pdf_to_printer(pdf_path, printer_name)