import json
import time
import textwrap
import subprocess
import sys
import select
from pathlib import Path
from typing import Optional

import os
import tempfile

# PDF (ReportLab)
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


import vlc


# NOTE: Den här versionen gör:

# den synkar mot ljudets faktiska uppspelningstid i stället för bara en timer
# man kan pausa / fortsätta
# man kan ändra offset live
# man kan simulera kvittoskrivaren i terminalen


# TODO: behöver installera VLC bindings: python -m pip install python-vlc

# När skriptet körs kan man skriva i terminalen:
#   pause        -> pausa ljud
#   resume       -> fortsätt ljud
#   offset 0.3   -> skriv ut text 0.3 sek tidigare
#   offset -0.2  -> skriv ut text 0.2 sek senare
#   status       -> visa uppspelningstid och offset
#   quit         -> avsluta

# kör SKRIPTET:
#   source .venv/bin/activate
#   python radio-to-receipt-ny.py
#
# avsluta SKRIPTET:
#   quit


# =========================
# KONFIG
# =========================
# =========================
# PDF/Custom font mode
# =========================
PDF_MODE = True  # True => render each line to a PDF using any font, then print with lp

# Receipt width ~72 mm printable area on 80 mm paper
PDF_PAGE_WIDTH_MM = 72.0
PDF_LEFT_MARGIN_MM = 4.0
PDF_RIGHT_MARGIN_MM = 4.0
PDF_TOP_MARGIN_MM = 1
PDF_BOTTOM_MARGIN_MM = 1

# Font settings (change these!)
PDF_FONT_NAME = "Times-Roman"  # Any font registered with ReportLab
PDF_FONT_SIZE = 8           # Try 12–14 for body text
PDF_LINE_SPACING = 1      # 1.0–1.3 typical

# Optional: use a custom TTF/OTF by specifying a path and name
# Example:
# PDF_FONT_PATH = "/Library/Fonts/Futura.ttc"
# PDF_FONT_NAME = "Futura"
PDF_FONT_PATH: Optional[str] = None

JSON_FILE = "spraket_ai_sync_segments.json"

# True = simulera skrivare i terminalen
# False = skicka till kvittoskrivare via lp
DRY_RUN = False

# Sätt skrivarnamn om du vill skriva ut på riktigt
PRINTER_NAME = "Star_TSP143__STR_T_001_"   # eller None (model: Star_TSP100III)

# För 80 mm kvitto är ungefär 42–48 tecken ofta rimligt
RECEIPT_WIDTH = 52

# Extra tomrader efter sista raden i en chunk
EXTRA_FEED_LINES = 2

# Positivt värde = text tidigare
# Negativt värde = text senare
GLOBAL_AUDIO_OFFSET = 0.0

# Hur ofta schedulern kollar om något ska skrivas ut
POLL_INTERVAL = 0.05


# =========================
# HJÄLPFUNKTIONER
# =========================
def _register_pdf_font_if_needed():
    """Register a custom TTF/OTF once if a font path is provided."""
    if PDF_FONT_PATH:
        if not Path(PDF_FONT_PATH).exists():
            raise FileNotFoundError(f"Font file not found: {PDF_FONT_PATH}")
        pdfmetrics.registerFont(TTFont(PDF_FONT_NAME, PDF_FONT_PATH))

def _build_single_line_pdf(out_path: str, text: str, *, is_last_line_in_chunk: bool) -> None:
    """
    Renders a single text line to a narrow PDF page.
    If it's the last line in a chunk, we extend the page height to emulate EXTRA_FEED_LINES.
    """
    page_w = PDF_PAGE_WIDTH_MM * mm

    # Compute line height & page height
    line_height = PDF_FONT_SIZE * PDF_LINE_SPACING
    extra_lines = EXTRA_FEED_LINES if is_last_line_in_chunk else 0
    content_h = PDF_TOP_MARGIN_MM * mm + line_height + (extra_lines * line_height) + PDF_BOTTOM_MARGIN_MM * mm
    page_h = max(content_h, 30 * mm)  # ensure a small minimum height

    c = canvas.Canvas(out_path, pagesize=(page_w, page_h))
    c.setFont(PDF_FONT_NAME, PDF_FONT_SIZE)

    x = PDF_LEFT_MARGIN_MM * mm
    y = page_h - PDF_TOP_MARGIN_MM * mm - PDF_FONT_SIZE  # baseline

    # No wrapping here—your scheduler already wrapped lines.
    c.drawString(x, y, text)
    c.showPage()
    c.save()
    
def load_data(json_path: str) -> dict:
    path = Path(json_path)
    if not path.exists():
        raise FileNotFoundError(f"JSON-filen hittades inte: {json_path}")

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def resolve_audio_path(data: dict, json_path: str) -> Path:
    audio_name = data["program"]["audio_file"]
    json_dir = Path(json_path).resolve().parent
    audio_path = (json_dir / audio_name).resolve()

    if not audio_path.exists():
        raise FileNotFoundError(f"Ljudfilen hittades inte: {audio_path}")

    return audio_path


def wrap_text_to_lines(text: str, width: int = RECEIPT_WIDTH) -> list[str]:
    paragraphs = text.splitlines() or [text]
    lines: list[str] = []

    for paragraph in paragraphs:
        paragraph = paragraph.strip()

        if not paragraph:
            lines.append("")
            continue

        wrapped = textwrap.wrap(
            paragraph,
            width=width,
            break_long_words=False,
            break_on_hyphens=False
        )

        if wrapped:
            lines.extend(wrapped)
        else:
            lines.append("")

    return lines


def find_next_chunk_print_time(schedule: list[dict], current_index: int, fallback: float = 2.0) -> float:
    """
    Hitta nästa chunks starttid.
    Om det inte finns någon nästa chunk, använd fallback-sekunder.
    """
    if current_index + 1 < len(schedule):
        return schedule[current_index + 1]["print_time"]
    return schedule[current_index]["print_time"] + fallback


def flatten_schedule(data: dict) -> list[dict]:
    """
    Läser segment/chunks från JSON och skapar en platt chunk-lista.
    Varje chunk får sin starttid.
    """
    schedule = []

    for segment in data["segments"]:
        segment_start = segment["start_seconds"]

        for chunk in segment["print_chunks"]:
            actual_print_time = segment_start + chunk["offset_seconds"]

            schedule.append({
                "segment_id": segment["id"],
                "chunk_id": chunk["chunk_id"],
                "print_time": float(actual_print_time),
                "text": chunk["text"],
            })

    schedule.sort(key=lambda item: item["print_time"])
    return schedule


def build_line_events(schedule: list[dict]) -> list[dict]:
    """
    Bygger en lista med line events.
    Varje rad i en chunk får en egen utskriftstid.
    """
    events: list[dict] = []

    for index, chunk in enumerate(schedule):
        chunk_start = chunk["print_time"]
        next_chunk_time = find_next_chunk_print_time(schedule, index, fallback=2.0)
        available_duration = max(0.8, next_chunk_time - chunk_start)

        lines = wrap_text_to_lines(chunk["text"], RECEIPT_WIDTH)

        if not lines:
            continue

        if len(lines) == 1:
            line_times = [chunk_start]
        else:
            step = available_duration / len(lines)
            line_times = [chunk_start + (i * step) for i in range(len(lines))]

        for i, line in enumerate(lines):
            events.append({
                "segment_id": chunk["segment_id"],
                "chunk_id": chunk["chunk_id"],
                "line_index": i,
                "print_time": float(line_times[i]),
                "text": line,
                "is_last_line_in_chunk": i == len(lines) - 1
            })

    events.sort(key=lambda item: item["print_time"])
    return events


def simulate_printer_output_line(text: str, is_last_line_in_chunk: bool) -> None:
    print(text)
    if is_last_line_in_chunk:
        print("\n" * (EXTRA_FEED_LINES - 1), end="")


# def send_line_to_printer(text: str, printer_name: Optional[str] = None, is_last_line_in_chunk: bool = False) -> None:
#     receipt_text = text.rstrip() + "\n"
#     if is_last_line_in_chunk:
#         receipt_text += "\n" * EXTRA_FEED_LINES

#     cmd = ["lp"]
#     if printer_name:
#         cmd.extend(["-d", printer_name])
#     cmd.append("-")

#     subprocess.run(
#         cmd,
#         input=receipt_text,
#         text=True,
#         check=True
#     )

def send_line_to_printer(text: str, printer_name: Optional[str] = None, is_last_line_in_chunk: bool = False) -> None:
    """
    In PDF mode: render a one-line PDF and send it via 'lp'.
    In text mode: send plain text to 'lp' (original behavior).
    """
    if not printer_name:
        raise RuntimeError("Ingen skrivarkö angiven. Sätt PRINTER_NAME eller kör i DRY_RUN-läge.")

    if PDF_MODE:
        _register_pdf_font_if_needed()

        # Create a temp PDF for this line
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp_path = tmp.name

        try:
            _build_single_line_pdf(tmp_path, text.rstrip(), is_last_line_in_chunk=is_last_line_in_chunk)
            cmd = ["lp", "-d", printer_name, tmp_path]
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
    else:
        # --- original plain text path ---
        receipt_text = text.rstrip() + "\n"
        if is_last_line_in_chunk:
            receipt_text += "\n" * EXTRA_FEED_LINES

        cmd = ["lp"]
        if printer_name:
            cmd.extend(["-d", printer_name])
        cmd.append("-")

        subprocess.run(
            cmd,
            input=receipt_text,
            text=True,
            check=True
        )
def print_or_send_line(text: str, printer_name: Optional[str], dry_run: bool, is_last_line_in_chunk: bool) -> None:
    if dry_run:
        simulate_printer_output_line(text, is_last_line_in_chunk)
    else:
        send_line_to_printer(text, printer_name, is_last_line_in_chunk)


def get_player_position_seconds(player: vlc.MediaPlayer) -> float:
    current_ms = player.get_time()
    if current_ms < 0:
        return 0.0
    return current_ms / 1000.0


def read_command_nonblocking() -> Optional[str]:
    """
    Läser ett kommando från terminalen om användaren har skrivit något.
    Returnerar None om inget kommando finns redo.
    """
    ready, _, _ = select.select([sys.stdin], [], [], 0)

    if ready:
        line = sys.stdin.readline()
        if line:
            return line.strip()

    return None


def print_help() -> None:
    print("Kommandon under körning:")
    print("  pause        -> pausa ljud")
    print("  resume       -> fortsätt ljud")
    print("  offset +   -> skriv ut text 0.3 sek tidigare")
    print("  offset -  -> skriv ut text 0.2 sek senare")
    print("  status       -> visa uppspelningstid och offset")
    print("  quit         -> avsluta")
    print()


# =========================
# HUVUDPROGRAM
# =========================
def main() -> None:
    global GLOBAL_AUDIO_OFFSET

    data = load_data(JSON_FILE)
    audio_path = resolve_audio_path(data, JSON_FILE)

    chunk_schedule = flatten_schedule(data)
    if not chunk_schedule:
        raise ValueError("Inga print_chunks hittades i JSON-filen.")

    line_events = build_line_events(chunk_schedule)
    if not line_events:
        raise ValueError("Inga line events kunde byggas.")

    print("Program:", data["program"].get("title", "Okänd titel"))
    print("Ljudfil:", audio_path.name)
    print("Antal chunks:", len(chunk_schedule))
    print("Antal rader att skriva ut:", len(line_events))
    print("Läge:", "SIMULERAD SKRIVARE" if DRY_RUN else "RIKTIG SKRIVARE")
    print("Kvittobredd:", RECEIPT_WIDTH, "tecken")
    print("Start-offset:", GLOBAL_AUDIO_OFFSET, "sek")
    print()
    print_help()

    instance = vlc.Instance()
    player = instance.media_player_new()
    media = instance.media_new(str(audio_path))
    player.set_media(media)

    player.play()
    time.sleep(0.4)

    next_index = 0
    paused = False
    running = True

    try:
        while running:
            command = read_command_nonblocking()

            if command:
                if command == "pause":
                    player.pause()
                    paused = True
                    print("Pausad.")

                elif command == "resume":
                    player.play()
                    paused = False
                    print("Fortsätter.")

                elif command.startswith("offset "):
                    parts = command.split(maxsplit=1)
                    if len(parts) == 2:
                        try:
                            GLOBAL_AUDIO_OFFSET = float(parts[1])
                            print(f"Ny offset: {GLOBAL_AUDIO_OFFSET:.2f} sek")
                        except ValueError:
                            print("Kunde inte läsa offset. Exempel: offset 0.3")

                elif command == "status":
                    pos = get_player_position_seconds(player)
                    print(
                        f"Status -> tid: {pos:.2f}s | "
                        f"offset: {GLOBAL_AUDIO_OFFSET:.2f}s | "
                        f"nästa rad: {next_index + 1}/{len(line_events)}"
                    )

                elif command == "quit":
                    print("Avslutar...")
                    running = False

                else:
                    print("Okänt kommando.")

            if not running:
                break

            if paused:
                time.sleep(POLL_INTERVAL)
                continue

            current_pos = get_player_position_seconds(player)

            while next_index < len(line_events):
                item = line_events[next_index]
                target_time = max(0.0, item["print_time"] - GLOBAL_AUDIO_OFFSET)

                if current_pos >= target_time:
                    print_or_send_line(
                        text=item["text"],
                        printer_name=PRINTER_NAME,
                        dry_run=DRY_RUN,
                        is_last_line_in_chunk=item["is_last_line_in_chunk"]
                    )
                    next_index += 1
                else:
                    break

            state = player.get_state()
            if next_index >= len(line_events):
                if state in (vlc.State.Ended, vlc.State.Stopped, vlc.State.NothingSpecial):
                    break

            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print("\nAvbrutet av användaren.")

    finally:
        player.stop()


if __name__ == "__main__":
    main()