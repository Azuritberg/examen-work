import json
import time
import textwrap
import subprocess
import sys
import select
from pathlib import Path
from typing import Optional

import vlc

from intro_text import (
    INTRO_TEXT,
    INTRO_LOGO,
    INTRO_DELAY_SECONDS,
    INTRO_COUNTDOWN_TEMPLATE,
    INTRO_WAVE,
)
from pdf_printer import print_lines_as_pdf, print_intro_pdf
from variant_algoritm_schedul import (
    VariantScheduler,
    preset_only_original,
    preset_only_critical,
    preset_only_hallucinated,
    preset_only_authoritative,
    preset_random_per_segment,
    preset_random_per_minute,
    preset_original_then_mixed,
    preset_authoritative_often,
    preset_hallucinations_rare,
)



# NOTE: Den här versionen gör:

# den synkar mot ljudets faktiska uppspelningstid i stället för bara en timer
# man kan pausa / fortsätta
# man kan ändra offset live
# man kan simulera kvittoskrivaren i terminalen   cupsctl WebInterface=yes
# den skriver ut hela chunks/block i stället för rad för rad
# den kan läsa nested JSON-struktur med variants per segment
# den är nu kopplad till en separat scheduler-fil som väljer variant automatiskt
# introduktionen skrivs ut via en separat PDF-layout i pdf_printer.py


# TODO: behöver installera VLC bindings: python -m pip install python-vlc

# När skriptet körs kan man skriva i terminalen:
#   pause        -> pausa ljud
#   resume       -> fortsätt ljud
#   offset +     -> skriv ut text 0.3 sek tidigare
#   offset -     -> skriv ut text -0.2 sek senare
#   status       -> visa uppspelningstid och offset
#   quit         -> avsluta

# kör SKRIPTET i Terminalen:
#   source .venv/bin/activate
#   python radio_to_receipt_main.py
#
# avsluta SKRIPTET:
#   quit


# köra SKRIPTET MED PDF-UTSKRIFT (om USE_PDF_PRINTING är True):
# source .venv/bin/activate
# lpstat -d -p
# python radio_to_receipt_main.py

# =========================
# KONFIG
# =========================
JSON_FILE = "spraket_ai_variant_nested.json"

# Välj preset här för att testa olika upplägg:
#
# preset_only_original()        -> alltid originaltexten
# preset_only_critical()        -> endast kritiska ändringar 
# preset_only_hallucinated()    -> endast hallucinationer
# preset_only_authoritative()   -> endast auktoritativa ändringar
# preset_random_per_segment()   -> slumpmässig variant per segment
# preset_random_per_minute()    -> slumpmässig variant varje minut
# preset_original_then_mixed()  -> börjar med original, övergår sedan till blandat
# preset_authoritative_often()  -> ofta auktoritativa ändringar, ibland original
# preset_hallucinations_rare()  -> sällan hallucinationer, oftast original eller auktoritativa
#
SCHEDULER_PRESET = preset_random_per_segment()

# True = simulera skrivare i terminalen
# False = skicka till kvittoskrivare via lp
DRY_RUN = False

# Sätt skrivarnamn om du vill skriva ut på riktigt
PRINTER_NAME = "Star_TSP143__STR_T_001_"   # eller None Star_TSP100III  Star_TSP143__STR_T_001_

# För 80 mm kvitto är ungefär 42–48 tecken ofta rimligt
RECEIPT_WIDTH = 48

# Extra tomrader efter sista raden i en chunk
EXTRA_FEED_LINES = 2

# Positivt värde = text tidigare
# Negativt värde = text senare
GLOBAL_AUDIO_OFFSET = 1.0

# Hur ofta schedul kollar om något ska skrivas ut
POLL_INTERVAL = 0.02


# PDF-PRINTER KONFIG
PDF_FONT_PATH = None      # Exempel: "path/to/custom_font.ttf"  "/Library/Fonts/Arial.ttf"
PDF_FONT_NAME = "Helvetica"
PDF_FONT_SIZE = 8.5
PDF_LINE_SPACING = 1.2
USE_PDF_PRINTING = True

# INTRO-PDF KONFIG
INTRO_LOGO_FONT_NAME = "RacingSansOne"
INTRO_LOGO_FONT_PATH = "fonts/RacingSansOne-Regular.ttf"
INTRO_LOGO_FONT_SIZE = 28

INTRO_BODY_FONT_NAME = "Helvetica"
INTRO_BODY_FONT_PATH = None
INTRO_BODY_FONT_SIZE = 9

INTRO_COUNTDOWN_FONT_NAME = "Helvetica"
INTRO_COUNTDOWN_FONT_PATH = None
INTRO_COUNTDOWN_FONT_SIZE = 9

INTRO_LOGO_TOP_MARGIN_MM = 30        # mellanrum från toppen av sidan till logotypen
INTRO_BODY_TOP_GAP_MM = 22           # mellanrum mellan logo och text
INTRO_PARAGRAPH_GAP_MM = 3           # mellanrum mellan paragrafer
INTRO_COUNTDOWN_GAP_MM = 4           # mellanrum mellan text och countdown
INTRO_BOTTOM_WAVE_GAP_MM = 30        # mellanrum mellan countdown och våglinje
INTRO_BOTTOM_WHITESPACE_MM = 10      # extra tomrum efter våglinjen innan slutet av sidan  

INTRO_PAGE_HEIGHT_MM = 280           # sidhøjden i mm


# BLOCK-UTSKRIFT KONFIG
BLOCK_TOP_BORDER = True
BLOCK_BOTTOM_BORDER = True
BLOCK_BORDER_CHAR = "-"
BLOCK_BORDER_WIDTH = RECEIPT_WIDTH

# Extra whitespace efter varje block
# Testa 8–12 beroende på hur mycket luft du vill ha
BLOCK_FEED_LINES = 12

# NY: extra tomrader före första riktiga segmentet
FIRST_SEGMENT_PRE_BLANK_LINES = 10


# =========================
# HJÄLPFUNKTIONER
# =========================
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


def format_chunk_as_block(text: str, width: int = RECEIPT_WIDTH) -> list[str]:
    """
    Formaterar en chunk som ett tydligt block med linje ovanför/under.
    """
    wrapped_lines = wrap_text_to_lines(text, width)
    block_lines: list[str] = []

    if BLOCK_TOP_BORDER:
        block_lines.append(BLOCK_BORDER_CHAR * BLOCK_BORDER_WIDTH)

    block_lines.extend(wrapped_lines)

    if BLOCK_BOTTOM_BORDER:
        block_lines.append(BLOCK_BORDER_CHAR * BLOCK_BORDER_WIDTH)

    return block_lines


def build_segment_schedule(data: dict) -> list[dict]:
    """
    Bygger en segmentlista från nested JSON.
    Själva variantvalet görs senare av schedulern.
    """
    segments = []

    for segment in data["segments"]:
        segment_start = float(segment["start_seconds"])

        segments.append({
            "id": segment["id"],
            "start_time": segment["start_time"],
            "start_seconds": segment_start,
            "duration_seconds": float(segment["duration_seconds"]),
            "end_time": segment["end_time"],
            "end_seconds": float(segment["end_seconds"]),
            "speaker": segment.get("speaker"),
            "variants": segment.get("variants", {}),
            "_printed": False,
        })

    segments.sort(key=lambda item: item["start_seconds"])
    return segments


def simulate_printer_output_block(lines: list[str]) -> None:
    for line in lines:
        print(line)
    print("\n" * BLOCK_FEED_LINES, end="")


def send_block_to_printer(
    lines: list[str],
    printer_name: Optional[str] = None
) -> None:
    receipt_text = "\n".join(lines) + "\n"
    receipt_text += "\n" * BLOCK_FEED_LINES

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


def print_or_send_block(
    text: str,
    printer_name: Optional[str],
    dry_run: bool,
    pre_blank_lines: int = 0
) -> None:
    block_lines = []

    if pre_blank_lines > 0:
        block_lines.extend([""] * pre_blank_lines)

    block_lines.extend(format_chunk_as_block(text, RECEIPT_WIDTH))

    # lägg till tomrader efter blocket
    block_lines.extend([""] * BLOCK_FEED_LINES)

    if dry_run:
        simulate_printer_output_block(block_lines)
    else:
        if USE_PDF_PRINTING:
            print_lines_as_pdf(
                lines=block_lines,
                printer_name=printer_name,
                font_size=PDF_FONT_SIZE,
                font_name=PDF_FONT_NAME,
                font_path=PDF_FONT_PATH,
                line_spacing=PDF_LINE_SPACING,
            )
        else:
            send_block_to_printer(block_lines, printer_name)


def print_intro(printer_name: Optional[str], dry_run: bool) -> None:
    """
    Skriver ut introduktionen.
    Vid DRY_RUN visas en enkel terminalversion.
    Vid riktig körning används speciallayoutad intro-PDF.
    """
    countdown_text = INTRO_COUNTDOWN_TEMPLATE.format(seconds=INTRO_DELAY_SECONDS)

    if dry_run:
        terminal_lines = [
            "",
            "",
            INTRO_LOGO.center(RECEIPT_WIDTH),
            "",
            "",
        ]
        terminal_lines.extend(wrap_text_to_lines(INTRO_TEXT, RECEIPT_WIDTH))
        terminal_lines.append("")
        terminal_lines.append("")
        terminal_lines.append(countdown_text.center(RECEIPT_WIDTH))
        terminal_lines.append("")
        terminal_lines.append(INTRO_WAVE.center(RECEIPT_WIDTH))
        terminal_lines.extend([""] * BLOCK_FEED_LINES)
        simulate_printer_output_block(terminal_lines)
        return

    print_intro_pdf(
        intro_logo=INTRO_LOGO,
        intro_text=INTRO_TEXT,
        countdown_text=countdown_text,
        printer_name=printer_name,
        logo_font_name=INTRO_LOGO_FONT_NAME,
        logo_font_path=INTRO_LOGO_FONT_PATH,
        logo_font_size=INTRO_LOGO_FONT_SIZE,
        body_font_name=INTRO_BODY_FONT_NAME,
        body_font_path=INTRO_BODY_FONT_PATH,
        body_font_size=INTRO_BODY_FONT_SIZE,
        countdown_font_name=INTRO_COUNTDOWN_FONT_NAME,
        countdown_font_path=INTRO_COUNTDOWN_FONT_PATH,
        countdown_font_size=INTRO_COUNTDOWN_FONT_SIZE,
        logo_top_margin_mm=INTRO_LOGO_TOP_MARGIN_MM,
        body_top_gap_mm=INTRO_BODY_TOP_GAP_MM,
        paragraph_gap_mm=INTRO_PARAGRAPH_GAP_MM,
        countdown_gap_mm=INTRO_COUNTDOWN_GAP_MM,
        bottom_wave_gap_mm=INTRO_BOTTOM_WAVE_GAP_MM,
        intro_bottom_whitespace_mm=INTRO_BOTTOM_WHITESPACE_MM,
        intro_page_height_mm=INTRO_PAGE_HEIGHT_MM,
        wave_text=INTRO_WAVE,
    )


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
    print("  offset  0.2  -> skriv ut text 0.3 sek tidigare")
    print("  offset -0.2  -> skriv ut text 0.2 sek senare")
    print("  status       -> visa uppspelningstid och offset")
    print("  quit         -> avsluta")
    print()


def choose_text_for_segment(segment: dict, scheduler: VariantScheduler, current_time: float) -> tuple[str, str]:
    """
    Frågar schedulern vilken variant som ska användas för segmentet
    och hämtar text_full för den varianten.
    """
    variant_name = scheduler.choose_variant(segment=segment, current_time=current_time)

    variants = segment.get("variants", {})
    if variant_name not in variants:
        available = ", ".join(variants.keys()) if variants else "inga"
        raise KeyError(
            f"Variant '{variant_name}' saknas i segment {segment['id']}. "
            f"Tillgängliga varianter: {available}"
        )

    text = variants[variant_name].get("text_full", "").strip()
    if not text:
        raise ValueError(
            f"Variant '{variant_name}' i segment {segment['id']} saknar text_full."
        )

    return variant_name, text


# =========================
# HUVUDPROGRAM
# =========================
def main() -> None:
    global GLOBAL_AUDIO_OFFSET

    scheduler = VariantScheduler(SCHEDULER_PRESET)

    data = load_data(JSON_FILE)
    audio_path = resolve_audio_path(data, JSON_FILE)

    segment_schedule = build_segment_schedule(data)
    if not segment_schedule:
        raise ValueError("Inga segment hittades i JSON-filen.")

    print("Program:", data["program"].get("title", "Okänd titel"))
    print("Ljudfil:", audio_path.name)
    print("Scheduler-strategi:", SCHEDULER_PRESET.strategy)
    print("Antal segment/block att skriva ut:", len(segment_schedule))
    print("Läge:", "SIMULERAD SKRIVARE" if DRY_RUN else "RIKTIG SKRIVARE")
    print("Kvittobredd:", RECEIPT_WIDTH, "tecken")
    print("Start-offset:", GLOBAL_AUDIO_OFFSET, "sek")
    print()
    print_help()

    print("Skriver ut introduktion...")
    print_intro(
        printer_name=PRINTER_NAME,
        dry_run=DRY_RUN,
    )

    print(f"Väntar {INTRO_DELAY_SECONDS} sekunder innan ljudet startar...")
    time.sleep(INTRO_DELAY_SECONDS)

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
                        f"nästa block: {next_index + 1}/{len(segment_schedule)}"
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

            while next_index < len(segment_schedule):
                segment = segment_schedule[next_index]
                target_time = max(0.0, segment["start_seconds"] - GLOBAL_AUDIO_OFFSET)

                if current_pos >= target_time:
                    variant_name, text = choose_text_for_segment(
                        segment=segment,
                        scheduler=scheduler,
                        current_time=current_pos
                    )

                    print(f"[Segment {segment['id']}] variant: {variant_name}")

                    pre_blank_lines = FIRST_SEGMENT_PRE_BLANK_LINES if next_index == 0 else 0

                    print_or_send_block(
                        text=text,
                        printer_name=PRINTER_NAME,
                        dry_run=DRY_RUN,
                        pre_blank_lines=pre_blank_lines,
                    )

                    next_index += 1
                else:
                    break

            state = player.get_state()
            if next_index >= len(segment_schedule):
                if state in (vlc.State.Ended, vlc.State.Stopped, vlc.State.NothingSpecial):
                    break

            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print("\nAvbrutet av användaren.")

    finally:
        player.stop()


if __name__ == "__main__":
    main()
