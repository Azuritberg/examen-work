import json
import time
import textwrap
import subprocess
import sys
import select
from pathlib import Path
from typing import Optional

import vlc

from pdf_printer import print_lines_as_pdf



# NOTE: Den här versionen gör:

# den synkar mot ljudets faktiska uppspelningstid i stället för bara en timer
# man kan pausa / fortsätta
# man kan ändra offset live
# man kan simulera kvittoskrivaren i terminalen   cupsctl WebInterface=yes
# den skriver nu ut hela chunks/block i stället för rad för rad
# den kan läsa nested JSON-struktur med variants per segment


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
#   python radio-to-receipt-block.py
#
# avsluta SKRIPTET:
#   quit


# köra SKRIPTET MED PDF-UTSKRIFT (om USE_PDF_PRINTING är True):
# source .venv/bin/activate
# lpstat -d -p
# python radio-to-receipt-block.py

# =========================
# KONFIG
# =========================
JSON_FILE = "spraket_ai_variant_nested.json"

# Vilken variant som ska köras från nested JSON
# Exempel: "original", "critical", "hallucinated", "authoritative_ai"
SELECTED_VARIANT = "original"

# True = simulera skrivare i terminalen
# False = skicka till kvittoskrivare via lp
DRY_RUN = True

# Sätt skrivarnamn om du vill skriva ut på riktigt
PRINTER_NAME = "Star_TSP143__STR_T_001_"   # eller None Star_TSP100III  Star_TSP143__STR_T_001_

# För 80 mm kvitto är ungefär 42–48 tecken ofta rimligt
RECEIPT_WIDTH = 48

# Extra tomrader efter sista raden i en chunk
EXTRA_FEED_LINES = 2

# Positivt värde = text tidigare
# Negativt värde = text senare
GLOBAL_AUDIO_OFFSET = 0.5

# Hur ofta schedulern kollar om något ska skrivas ut
POLL_INTERVAL = 0.05


# PDF-PRINTER KONFIG
PDF_FONT_PATH = None      # Exempel: "path/to/custom_font.ttf"  "/Library/Fonts/Arial.ttf"
PDF_FONT_NAME = "Helvetica"
PDF_FONT_SIZE = 8
PDF_LINE_SPACING = 1.2
USE_PDF_PRINTING = True


# BLOCK-UTSKRIFT KONFIG
BLOCK_TOP_BORDER = True
BLOCK_BOTTOM_BORDER = True
BLOCK_BORDER_CHAR = "-"
BLOCK_BORDER_WIDTH = RECEIPT_WIDTH

# Extra whitespace efter varje block
# Testa 8–12 beroende på hur mycket luft du vill ha
BLOCK_FEED_LINES = 3


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


def flatten_schedule(data: dict, selected_variant: str) -> list[dict]:
    """
    Läser segment/chunks från nested JSON och skapar en platt chunk-lista.
    Varje chunk får sin starttid.

    Förväntad struktur:
    segment["variants"][selected_variant]["print_chunks"]
    """
    schedule = []

    for segment in data["segments"]:
        segment_start = segment["start_seconds"]
        variants = segment.get("variants", {})

        if selected_variant not in variants:
            available = ", ".join(variants.keys()) if variants else "inga"
            raise KeyError(
                f"Variant '{selected_variant}' saknas i segment {segment['id']}. "
                f"Tillgängliga varianter: {available}"
            )

        selected = variants[selected_variant]
        print_chunks = selected.get("print_chunks", [])

        for chunk in print_chunks:
            actual_print_time = segment_start + chunk["offset_seconds"]

            schedule.append({
                "segment_id": segment["id"],
                "chunk_id": chunk["chunk_id"],
                "print_time": float(actual_print_time),
                "text": chunk["text"],
            })

    schedule.sort(key=lambda item: item["print_time"])
    return schedule


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


def print_or_send_block(text: str, printer_name: Optional[str], dry_run: bool) -> None:
    block_lines = format_chunk_as_block(text, RECEIPT_WIDTH)

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


# =========================
# HUVUDPROGRAM
# =========================
def main() -> None:
    global GLOBAL_AUDIO_OFFSET

    data = load_data(JSON_FILE)
    audio_path = resolve_audio_path(data, JSON_FILE)

    chunk_schedule = flatten_schedule(data, SELECTED_VARIANT)
    if not chunk_schedule:
        raise ValueError("Inga print_chunks hittades i JSON-filen.")

    print("Program:", data["program"].get("title", "Okänd titel"))
    print("Ljudfil:", audio_path.name)
    print("Vald variant:", SELECTED_VARIANT)
    print("Antal block att skriva ut:", len(chunk_schedule))
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
                        f"nästa block: {next_index + 1}/{len(chunk_schedule)}"
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

            while next_index < len(chunk_schedule):
                item = chunk_schedule[next_index]
                target_time = max(0.0, item["print_time"] - GLOBAL_AUDIO_OFFSET)

                if current_pos >= target_time:
                    print_or_send_block(
                        text=item["text"],
                        printer_name=PRINTER_NAME,
                        dry_run=DRY_RUN,
                    )
                    next_index += 1
                else:
                    break

            state = player.get_state()
            if next_index >= len(chunk_schedule):
                if state in (vlc.State.Ended, vlc.State.Stopped, vlc.State.NothingSpecial):
                    break

            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print("\nAvbrutet av användaren.")

    finally:
        player.stop()


if __name__ == "__main__":
    main()
