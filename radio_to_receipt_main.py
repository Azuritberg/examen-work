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
    INTRO_END_LOGO,
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
    preset_random_per_chunk,
    preset_random_every_two_chunks,
    preset_original_then_mixed_per_chunk,
    preset_realistic_even_flow,
)


# NOTE: Den här versionen gör:

# den synkar mot ljudets faktiska uppspelningstid i stället för bara en timer
# man kan pausa / fortsätta
# man kan ändra offset live
# man kan simulera kvittoskrivaren i terminalen   cupsctl WebInterface=yes
# den skriver ut hela chunks/block i stället för rad för rad
# den kan läsa nested JSON-struktur med variants per segment
# den är nu kopplad till en separat scheduler-fil som väljer variant automatiskt


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

# JSON filen spraket_ai_variant_nested.json = innehåller alla varianter (original, kritisk, hallucinerad, auktoritär) där formuleringarna är twikade ganska mycket från original transkriptet

# JSON filen spraket_ai_variant_nested_NYA_TR.json = innehåller alla varianter (original, kritisk, hallucinerad, auktoritär) och är mer närmare utgångs texten, så vi kommer använda denna i våra intervjuer.

JSON_FILE = "spraket_ai_variant_nested_NYA_TR.json"

# preset_only_original()                    # Använd alltid originaltexten
# preset_only_critical()                    # Använd alltid den kritiska varianten 
# preset_only_hallucinated()                # Använd alltid den hallucinerade varianten 
# preset_only_authoritative()               # Använd alltid den auktoritära varianten 
# preset_random_per_chunk()                 # Välj en slumpmässig variant per chunk
# preset_random_every_two_chunks()          # Välj en slumpmässig variant every two chunks
# preset_original_then_mixed_per_chunk()    # Använd originaltexten första chunken och blandade varianter för resten
# preset_realistic_even_flow()              # Använden realistisk jämn fördelning av varianter

SCHEDULER_PRESET = preset_realistic_even_flow()     # Välj preset här

DRY_RUN = False      # True = Simulera skrivare i terminalen
                    # False = Skicka till kvittoskrivare via lp

PRINTER_NAME = "Star_TSP143__STR_T_001_"

RECEIPT_WIDTH = 48            # Antal tecken per rad i kvittot.
POLL_INTERVAL = 0.02          # Hur ofta vi kollar tiden (sekunder)

GLOBAL_AUDIO_OFFSET = 0.5     # Sekunder att justera utskriftstidpunkten i förhållande till ljudet. 
                              # Positivt värde gör att texten skrivs ut tidigare, negativt gör att den skrivs ut senare. 
                              # Kan ändras under körning med "offset" kommando.

CHUNK_LEAD_SECONDS = 2.5      # Hur många sekunder innan en chunk ska skrivas ut, i förhållande till när den hörs i ljudet. Hade det innan på 0.5

# PDF-PRINTER KONFIG
PDF_FONT_PATH = None            # Exempel: "path/to/custom_font.ttf"  "/Library/Fonts/Arial.ttf"
PDF_FONT_NAME = "Helvetica"
PDF_FONT_SIZE = 8.2            # Font size i PDF-utskriften.
PDF_LINE_SPACING = 1.3         # 1.2 är standard, öka för mer luft mellan raderna
USE_PDF_PRINTING = True        # Om True, använd PDF-utskrift även för chunk-utskriften. Om False, använd vanlig textutskrift via lp.

INTRO_LOGO_FONT_NAME = "RacingSansOne"
INTRO_LOGO_FONT_PATH = "fonts/RacingSansOne-Regular.ttf"
INTRO_LOGO_FONT_SIZE = 28

INTRO_END_LOGO_FONT_SIZE = 20  # End-logo font size i intro-PDF:en
INTRO_END_LOGO_GAP_MM = 8      # Extra gap mellan slutlogon och nedräkningen i intro-PDF:en

INTRO_BODY_FONT_NAME = "Helvetica"
INTRO_BODY_FONT_PATH = None
INTRO_BODY_FONT_SIZE = 9

INTRO_COUNTDOWN_FONT_NAME = "Helvetica"
INTRO_COUNTDOWN_FONT_PATH = None
INTRO_COUNTDOWN_FONT_SIZE = 9

INTRO_LOGO_TOP_MARGIN_MM = 30       # Extra top-margin för logon i intro-PDF:en
INTRO_BODY_TOP_GAP_MM = 22          # Extra gap mellan logon och brödtexten
INTRO_PARAGRAPH_GAP_MM = 3          # Extra gap mellan paragrafer
INTRO_COUNTDOWN_GAP_MM = 4          # Extra gap mellan brödtexten och nedräkningen
INTRO_BOTTOM_WAVE_GAP_MM = 30       # Extra gap mellan nedräkningen och våglinjen längst ner i intro-PDF:en
INTRO_BOTTOM_WHITESPACE_MM = 10     # Extra whitespace i botten av intro-PDF:en
INTRO_PAGE_HEIGHT_MM = 280          # Höjden på intro-PDF:en i mm. Ändra för att få mer eller mindre whitespace i botten av sidan.

# BLOCK-UTSKRIFT KONFIG
BLOCK_TOP_BORDER = True             # Om True, rita en horisontell linje av BLOCK_BORDER_CHAR ovanför varje chunk
BLOCK_BOTTOM_BORDER = True
BLOCK_BORDER_CHAR = "-"
BLOCK_BORDER_WIDTH = RECEIPT_WIDTH
BLOCK_FEED_LINES = 12
FIRST_SEGMENT_PRE_BLANK_LINES = 10

CHUNK_GAP_PRE_BLANK_LINES = 4       # Extra whitespace efter varje chunk


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
    wrapped_lines = wrap_text_to_lines(text, width)
    block_lines: list[str] = []

    if BLOCK_TOP_BORDER:
        block_lines.append(BLOCK_BORDER_CHAR * BLOCK_BORDER_WIDTH)

    block_lines.extend(wrapped_lines)

    if BLOCK_BOTTOM_BORDER:
        block_lines.append(BLOCK_BORDER_CHAR * BLOCK_BORDER_WIDTH)

    return block_lines


def build_chunk_schedule(data: dict) -> list[dict]:
    schedule = []

    for segment in data["segments"]:
        segment_start = float(segment["start_seconds"])
        variants = segment.get("variants", {})
        original_variant = variants.get("original", {})
        original_chunks = original_variant.get("print_chunks", [])

        for chunk in original_chunks:
            chunk_id = int(chunk["chunk_id"])
            offset_seconds = float(chunk["offset_seconds"])

            schedule.append({
                "segment": segment,
                "chunk": {
                    "chunk_id": chunk_id,
                    "offset_seconds": offset_seconds,
                },
                "print_time": segment_start + offset_seconds,
            })

    schedule.sort(key=lambda item: item["print_time"])
    return schedule


def get_chunk_text_for_variant(segment: dict, variant_name: str, chunk_id: int) -> str:
    variants = segment.get("variants", {})
    if variant_name not in variants:
        available = ", ".join(variants.keys()) if variants else "inga"
        raise KeyError(
            f"Variant '{variant_name}' saknas i segment {segment['id']}. "
            f"Tillgängliga varianter: {available}"
        )

    variant_chunks = variants[variant_name].get("print_chunks", [])
    for chunk in variant_chunks:
        if int(chunk["chunk_id"]) == int(chunk_id):
            return chunk.get("text", "").strip()

    raise ValueError(
        f"Kunde inte hitta chunk_id {chunk_id} i variant '{variant_name}' "
        f"för segment {segment['id']}."
    )


def simulate_printer_output_block(lines: list[str]) -> None:
    for line in lines:
        print(line)
    print("\n" * BLOCK_FEED_LINES, end="")


def send_block_to_printer(lines: list[str], printer_name: Optional[str] = None) -> None:
    receipt_text = "\n".join(lines) + "\n"
    receipt_text += "\n" * BLOCK_FEED_LINES

    cmd = ["lp"]
    if printer_name:
        cmd.extend(["-d", printer_name])
    cmd.append("-")

    subprocess.run(cmd, input=receipt_text, text=True, check=True)


def print_or_send_block(text: str, printer_name: Optional[str], dry_run: bool, pre_blank_lines: int = 0) -> None:
    block_lines = []

    if pre_blank_lines > 0:
        block_lines.extend([""] * pre_blank_lines)

    block_lines.extend(format_chunk_as_block(text, RECEIPT_WIDTH))
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
    countdown_text = INTRO_COUNTDOWN_TEMPLATE.format(seconds=INTRO_DELAY_SECONDS)

    if dry_run:
        terminal_lines = ["", "", INTRO_LOGO.center(RECEIPT_WIDTH), "", ""]
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
        intro_end_logo=INTRO_END_LOGO,
        intro_text=INTRO_TEXT,
        countdown_text=countdown_text,
        printer_name=printer_name,
        logo_font_name=INTRO_LOGO_FONT_NAME,
        logo_font_path=INTRO_LOGO_FONT_PATH,
        logo_font_size=INTRO_LOGO_FONT_SIZE,
        end_logo_font_name=INTRO_LOGO_FONT_NAME,
        end_logo_font_path=INTRO_LOGO_FONT_PATH,
        end_logo_font_size=INTRO_END_LOGO_FONT_SIZE,
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
        end_logo_gap_mm=INTRO_END_LOGO_GAP_MM,
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
    print("  offset  0.2  -> skriv ut text 0.2 sek tidigare")
    print("  offset -0.2  -> skriv ut text 0.2 sek senare")
    print("  status       -> visa uppspelningstid och offset")
    print("  quit         -> avsluta")
    print()


# =========================================
# HUVUDPROGRAM  MED LOOP INFÖR UTSTÄLLNING
# =========================================

def main() -> None:
    global GLOBAL_AUDIO_OFFSET

    data = load_data(JSON_FILE)
    audio_path = resolve_audio_path(data, JSON_FILE)

    chunk_schedule = build_chunk_schedule(data)
    if not chunk_schedule:
        raise ValueError("Inga chunks hittades i JSON-filen.")

    print("Program:", data["program"].get("title", "Okänd titel"))
    print("Ljudfil:", audio_path.name)
    print("Scheduler-strategi:", SCHEDULER_PRESET.strategy)
    print("Antal chunks att skriva ut:", len(chunk_schedule))
    print("Läge:", "SIMULERAD SKRIVARE" if DRY_RUN else "RIKTIG SKRIVARE")
    print("Kvittobredd:", RECEIPT_WIDTH, "tecken")
    print("Start-offset:", GLOBAL_AUDIO_OFFSET, "sek")
    print()
    print_help()

    instance = vlc.Instance()
    player = None

    try:
        while True:
            scheduler = VariantScheduler(SCHEDULER_PRESET)

            print("Skriver ut introduktion...")
            print_intro(printer_name=PRINTER_NAME, dry_run=DRY_RUN)

            print(f"Väntar {INTRO_DELAY_SECONDS} sekunder innan ljudet startar...")
            time.sleep(INTRO_DELAY_SECONDS)

            player = instance.media_player_new()
            media = instance.media_new(str(audio_path))
            player.set_media(media)

            player.play()
            time.sleep(0.4)

            next_index = 0
            paused = False
            running = True

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
                                print("Kunde inte läsa offset. Exempel: offset 0.2")

                    elif command == "status":
                        pos = get_player_position_seconds(player)
                        print(
                            f"Status -> tid: {pos:.2f}s | "
                            f"offset: {GLOBAL_AUDIO_OFFSET:.2f}s | "
                            f"nästa chunk: {next_index + 1}/{len(chunk_schedule)}"
                        )

                    elif command == "quit":
                        print("Avslutar...")
                        return

                    else:
                        print("Okänt kommando.")

                if paused:
                    time.sleep(POLL_INTERVAL)
                    continue

                current_pos = get_player_position_seconds(player)

                while next_index < len(chunk_schedule):
                    item = chunk_schedule[next_index]
                    target_time = max(0.0, item["print_time"] - GLOBAL_AUDIO_OFFSET - CHUNK_LEAD_SECONDS)
                    #target_time = max(0.0, item["print_time"] - GLOBAL_AUDIO_OFFSET)

                    if current_pos >= target_time:
                        segment = item["segment"]
                        chunk = item["chunk"]

                        variant_name = scheduler.choose_variant(
                            segment=segment,
                            chunk=chunk,
                            current_time=current_pos
                        )

                        text = get_chunk_text_for_variant(
                            segment=segment,
                            variant_name=variant_name,
                            chunk_id=chunk["chunk_id"]
                        )

                        print(f"[Segment {segment['id']} chunk {chunk['chunk_id']}] variant: {variant_name}")

                        pre_blank_lines = FIRST_SEGMENT_PRE_BLANK_LINES if next_index == 0 else CHUNK_GAP_PRE_BLANK_LINES

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
                if next_index >= len(chunk_schedule):
                    if state in (vlc.State.Ended, vlc.State.Stopped, vlc.State.NothingSpecial):
                        player.stop()
                        print("Programmet är slut. Startar om från början...")
                        break

                time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print("\nAvbrutet av användaren.")

    finally:
        if player is not None:
            player.stop()


if __name__ == "__main__":
    main()

# # ================================
# # HUVUDPROGRAM  ORGINAL UTAN LOOP
# # ================================

# def main() -> None:
#     global GLOBAL_AUDIO_OFFSET

#     scheduler = VariantScheduler(SCHEDULER_PRESET)

#     data = load_data(JSON_FILE)
#     audio_path = resolve_audio_path(data, JSON_FILE)

#     chunk_schedule = build_chunk_schedule(data)
#     if not chunk_schedule:
#         raise ValueError("Inga chunks hittades i JSON-filen.")

#     print("Program:", data["program"].get("title", "Okänd titel"))
#     print("Ljudfil:", audio_path.name)
#     print("Scheduler-strategi:", SCHEDULER_PRESET.strategy)
#     print("Antal chunks att skriva ut:", len(chunk_schedule))
#     print("Läge:", "SIMULERAD SKRIVARE" if DRY_RUN else "RIKTIG SKRIVARE")
#     print("Kvittobredd:", RECEIPT_WIDTH, "tecken")
#     print("Start-offset:", GLOBAL_AUDIO_OFFSET, "sek")
#     print()
#     print_help()

#     print("Skriver ut introduktion...")
#     print_intro(printer_name=PRINTER_NAME, dry_run=DRY_RUN)

#     print(f"Väntar {INTRO_DELAY_SECONDS} sekunder innan ljudet startar...")
#     time.sleep(INTRO_DELAY_SECONDS)

#     instance = vlc.Instance()
#     player = instance.media_player_new()
#     media = instance.media_new(str(audio_path))
#     player.set_media(media)

#     player.play()
#     time.sleep(0.4)

#     next_index = 0
#     paused = False
#     running = True

#     try:
#         while running:
#             command = read_command_nonblocking()

#             if command:
#                 if command == "pause":
#                     player.pause()
#                     paused = True
#                     print("Pausad.")

#                 elif command == "resume":
#                     player.play()
#                     paused = False
#                     print("Fortsätter.")

#                 elif command.startswith("offset "):
#                     parts = command.split(maxsplit=1)
#                     if len(parts) == 2:
#                         try:
#                             GLOBAL_AUDIO_OFFSET = float(parts[1])
#                             print(f"Ny offset: {GLOBAL_AUDIO_OFFSET:.2f} sek")
#                         except ValueError:
#                             print("Kunde inte läsa offset. Exempel: offset 0.2")

#                 elif command == "status":
#                     pos = get_player_position_seconds(player)
#                     print(
#                         f"Status -> tid: {pos:.2f}s | "
#                         f"offset: {GLOBAL_AUDIO_OFFSET:.2f}s | "
#                         f"nästa chunk: {next_index + 1}/{len(chunk_schedule)}"
#                     )

#                 elif command == "quit":
#                     print("Avslutar...")
#                     running = False

#                 else:
#                     print("Okänt kommando.")

#             if not running:
#                 break

#             if paused:
#                 time.sleep(POLL_INTERVAL)
#                 continue

#             current_pos = get_player_position_seconds(player)

#             while next_index < len(chunk_schedule):
#                 item = chunk_schedule[next_index]
#                 target_time = max(0.0, item["print_time"] - GLOBAL_AUDIO_OFFSET - CHUNK_LEAD_SECONDS)
#                 #target_time = max(0.0, item["print_time"] - GLOBAL_AUDIO_OFFSET)

#                 if current_pos >= target_time:
#                     segment = item["segment"]
#                     chunk = item["chunk"]

#                     variant_name = scheduler.choose_variant(
#                         segment=segment,
#                         chunk=chunk,
#                         current_time=current_pos
#                     )

#                     text = get_chunk_text_for_variant(
#                         segment=segment,
#                         variant_name=variant_name,
#                         chunk_id=chunk["chunk_id"]
#                     )

#                     print(f"[Segment {segment['id']} chunk {chunk['chunk_id']}] variant: {variant_name}")

#                     pre_blank_lines = FIRST_SEGMENT_PRE_BLANK_LINES if next_index == 0 else CHUNK_GAP_PRE_BLANK_LINES

#                     print_or_send_block(
#                         text=text,
#                         printer_name=PRINTER_NAME,
#                         dry_run=DRY_RUN,
#                         pre_blank_lines=pre_blank_lines,
#                     )

#                     next_index += 1
#                 else:
#                     break

#             state = player.get_state()
#             if next_index >= len(chunk_schedule):
#                 if state in (vlc.State.Ended, vlc.State.Stopped, vlc.State.NothingSpecial):
#                     break

#             time.sleep(POLL_INTERVAL)

#     except KeyboardInterrupt:
#         print("\nAvbrutet av användaren.")

#     finally:
#         player.stop()


# if __name__ == "__main__":
#     main()
