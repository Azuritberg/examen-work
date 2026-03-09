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
#   python radio-to-receipt-ny.py
#
# avsluta SKRIPTET:
#   quit


# köra SKRIPTET MED PDF-UTSKRIFT (om USE_PDF_PRINTING är True):
# source .venv/bin/activate
# lpstat -d -p
# python radio-to-receipt-ny.py

# =========================
# KONFIG
# =========================
JSON_FILE = "spraket_ai_sync_segments.json"

# True = simulera skrivare i terminalen
# False = skicka till kvittoskrivare via lp
DRY_RUN = False

# Sätt skrivarnamn om du vill skriva ut på riktigt
PRINTER_NAME = "Star_TSP143__STR_T_001_"   # eller None Star_TSP100III  Star_TSP143__STR_T_001_

# För 80 mm kvitto är ungefär 42–48 tecken ofta rimligt
RECEIPT_WIDTH = 52

# Extra tomrader efter sista raden i en chunk
EXTRA_FEED_LINES = 2

# Positivt värde = text tidigare
# Negativt värde = text senare
GLOBAL_AUDIO_OFFSET = 0.0

# Hur ofta schedulern kollar om något ska skrivas ut
POLL_INTERVAL = 0.05


# PDF-PRINTER KONFIG
PDF_FONT_NAME = "Helvetica"
PDF_FONT_SIZE = 8
USE_PDF_PRINTING = True


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


def send_line_to_printer(text: str, printer_name: Optional[str] = None, is_last_line_in_chunk: bool = False) -> None:
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


# def print_or_send_line(text: str, printer_name: Optional[str], dry_run: bool, is_last_line_in_chunk: bool) -> None:
#     if dry_run:
#         simulate_printer_output_line(text, is_last_line_in_chunk)
#     else:
#         send_line_to_printer(text, printer_name, is_last_line_in_chunk)

def print_or_send_line(text: str, printer_name: Optional[str], dry_run: bool, is_last_line_in_chunk: bool) -> None:
    if dry_run:
        simulate_printer_output_line(text, is_last_line_in_chunk)
    else:
        if USE_PDF_PRINTING:
            lines = wrap_text_to_lines(text, RECEIPT_WIDTH)
            print_lines_as_pdf(
                lines=lines,
                printer_name=printer_name,
                font_size=PDF_FONT_SIZE,
                font_name=PDF_FONT_NAME,
            )
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
    print("  offset +     -> skriv ut text 0.3 sek tidigare")
    print("  offset -     -> skriv ut text 0.2 sek senare")
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





## ------------------------------------------------------------------ ##
## DENNA KODEN SKRIVER UT I RADER OCH HAR EN KOMMAND PANEL I TERMINALEN MED LIVE STATUS
## ------------------------------------------------------------------ ##



# import curses
# import json
# import subprocess
# import textwrap
# import time
# from collections import deque
# from pathlib import Path
# from typing import Optional

# import vlc


# # =========================
# # KONFIG
# # =========================
# JSON_FILE = "spraket_ai_sync_segments.json"

# # True = simulera skrivare i terminalen
# # False = skicka till kvittoskrivare via lp
# DRY_RUN = True

# # Sätt skrivarnamn om du vill skriva ut på riktigt
# PRINTER_NAME = "Star_TSP100III"   # eller None

# # För 80 mm kvitto är ungefär 42–48 tecken ofta rimligt
# RECEIPT_WIDTH = 42

# # Extra tomrader efter sista raden i en chunk
# EXTRA_FEED_LINES = 2

# # Positivt värde = text tidigare
# # Negativt värde = text senare
# GLOBAL_AUDIO_OFFSET = 0.0

# # Hur ofta huvudloopen kollar läget
# POLL_INTERVAL = 0.05

# # Hur mycket +/- ändrar offset live
# OFFSET_STEP = 0.10

# # Hur många rader receipt-logg vi sparar i panelen
# LOG_HISTORY_LINES = 200


# # =========================
# # HJÄLPFUNKTIONER
# # =========================
# def load_data(json_path: str) -> dict:
#     path = Path(json_path)
#     if not path.exists():
#         raise FileNotFoundError(f"JSON-filen hittades inte: {json_path}")

#     with path.open("r", encoding="utf-8") as f:
#         return json.load(f)


# def resolve_audio_path(data: dict, json_path: str) -> Path:
#     audio_name = data["program"]["audio_file"]
#     json_dir = Path(json_path).resolve().parent
#     audio_path = (json_dir / audio_name).resolve()

#     if not audio_path.exists():
#         raise FileNotFoundError(f"Ljudfilen hittades inte: {audio_path}")

#     return audio_path


# def wrap_text_to_lines(text: str, width: int = RECEIPT_WIDTH) -> list[str]:
#     paragraphs = text.splitlines() or [text]
#     lines: list[str] = []

#     for paragraph in paragraphs:
#         paragraph = paragraph.strip()

#         if not paragraph:
#             lines.append("")
#             continue

#         wrapped = textwrap.wrap(
#             paragraph,
#             width=width,
#             break_long_words=False,
#             break_on_hyphens=False,
#         )

#         if wrapped:
#             lines.extend(wrapped)
#         else:
#             lines.append("")

#     return lines


# def find_next_chunk_print_time(schedule: list[dict], current_index: int, fallback: float = 2.0) -> float:
#     if current_index + 1 < len(schedule):
#         return schedule[current_index + 1]["print_time"]
#     return schedule[current_index]["print_time"] + fallback


# def flatten_schedule(data: dict) -> list[dict]:
#     schedule = []

#     for segment in data["segments"]:
#         segment_start = segment["start_seconds"]

#         for chunk in segment["print_chunks"]:
#             actual_print_time = segment_start + chunk["offset_seconds"]

#             schedule.append({
#                 "segment_id": segment["id"],
#                 "chunk_id": chunk["chunk_id"],
#                 "print_time": float(actual_print_time),
#                 "text": chunk["text"],
#             })

#     schedule.sort(key=lambda item: item["print_time"])
#     return schedule


# def build_line_events(schedule: list[dict]) -> list[dict]:
#     events: list[dict] = []

#     for index, chunk in enumerate(schedule):
#         chunk_start = chunk["print_time"]
#         next_chunk_time = find_next_chunk_print_time(schedule, index, fallback=2.0)
#         available_duration = max(0.8, next_chunk_time - chunk_start)

#         lines = wrap_text_to_lines(chunk["text"], RECEIPT_WIDTH)
#         if not lines:
#             continue

#         if len(lines) == 1:
#             line_times = [chunk_start]
#         else:
#             step = available_duration / len(lines)
#             line_times = [chunk_start + (i * step) for i in range(len(lines))]

#         for i, line in enumerate(lines):
#             events.append({
#                 "segment_id": chunk["segment_id"],
#                 "chunk_id": chunk["chunk_id"],
#                 "line_index": i,
#                 "print_time": float(line_times[i]),
#                 "text": line,
#                 "is_last_line_in_chunk": i == len(lines) - 1,
#             })

#     events.sort(key=lambda item: item["print_time"])
#     return events


# def send_line_to_printer(text: str, printer_name: Optional[str], is_last_line_in_chunk: bool) -> None:
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
#         check=True,
#     )


# def get_player_position_seconds(player: vlc.MediaPlayer) -> float:
#     current_ms = player.get_time()
#     if current_ms < 0:
#         return 0.0
#     return current_ms / 1000.0


# def format_mmss(seconds: float) -> str:
#     total = max(0, int(seconds))
#     minutes = total // 60
#     secs = total % 60
#     return f"{minutes:02d}:{secs:02d}"


# def safe_addstr(win, y: int, x: int, text: str, attr=0) -> None:
#     max_y, max_x = win.getmaxyx()
#     if y < 0 or y >= max_y or x >= max_x:
#         return

#     clipped = text[: max(0, max_x - x - 1)]
#     try:
#         win.addstr(y, x, clipped, attr)
#     except curses.error:
#         pass


# def draw_box_title(win, title: str) -> None:
#     win.box()
#     safe_addstr(win, 0, 2, f" {title} ", curses.A_BOLD)


# def append_to_log(log_lines: deque[str], text: str, is_last_line_in_chunk: bool) -> None:
#     if text == "":
#         log_lines.append("")
#     else:
#         for line in wrap_text_to_lines(text, RECEIPT_WIDTH):
#             log_lines.append(line)

#     if is_last_line_in_chunk:
#         for _ in range(EXTRA_FEED_LINES):
#             log_lines.append("")


# # =========================
# # UI
# # =========================
# def draw_ui(
#     stdscr,
#     title: str,
#     audio_name: str,
#     player_pos: float,
#     offset: float,
#     paused: bool,
#     dry_run: bool,
#     next_index: int,
#     total_events: int,
#     last_status: str,
#     log_lines: deque[str],
# ) -> None:
#     stdscr.erase()
#     height, width = stdscr.getmaxyx()

#     header_h = 8
#     controls_h = 6
#     log_h = max(8, height - header_h - controls_h - 1)

#     header_win = stdscr.derwin(header_h, width, 0, 0)
#     controls_win = stdscr.derwin(controls_h, width, header_h, 0)
#     log_win = stdscr.derwin(log_h, width, header_h + controls_h, 0)

#     draw_box_title(header_win, " Radio to Receipt / Kontrollpanel ")
#     safe_addstr(header_win, 1, 2, f"Program: {title}")
#     safe_addstr(header_win, 2, 2, f"Ljudfil: {audio_name}")
#     safe_addstr(header_win, 3, 2, f"Tid: {format_mmss(player_pos)} ({player_pos:.2f}s)")
#     safe_addstr(header_win, 4, 2, f"Offset: {offset:+.2f}s")
#     safe_addstr(header_win, 5, 2, f"Läge: {'PAUSAD' if paused else 'SPELAR'} | {'SIMULERAD SKRIVARE' if dry_run else 'RIKTIG SKRIVARE'}")
#     safe_addstr(header_win, 6, 2, f"Nästa rad: {min(next_index + 1, total_events)}/{total_events}")

#     draw_box_title(controls_win, " Tangenter ")
#     safe_addstr(controls_win, 1, 2, "p = pause   r = resume   q = quit")
#     safe_addstr(controls_win, 2, 2, "+ = offset tidigare   - = offset senare")
#     safe_addstr(controls_win, 3, 2, "s = statusrad   SPACE = pause/resume")
#     safe_addstr(controls_win, 4, 2, last_status)

#     draw_box_title(log_win, " Receipt-simulering ")
#     inner_h, inner_w = log_win.getmaxyx()
#     visible_lines = inner_h - 2
#     recent = list(log_lines)[-visible_lines:]

#     for i, line in enumerate(recent, start=1):
#         safe_addstr(log_win, i, 2, line[: max(0, inner_w - 4)])

#     header_win.noutrefresh()
#     controls_win.noutrefresh()
#     log_win.noutrefresh()
#     curses.doupdate()


# # =========================
# # HUVUDPROGRAM
# # =========================
# def run(stdscr) -> None:
#     global GLOBAL_AUDIO_OFFSET

#     curses.curs_set(0)
#     stdscr.nodelay(True)
#     stdscr.keypad(True)

#     data = load_data(JSON_FILE)
#     audio_path = resolve_audio_path(data, JSON_FILE)

#     chunk_schedule = flatten_schedule(data)
#     if not chunk_schedule:
#         raise ValueError("Inga print_chunks hittades i JSON-filen.")

#     line_events = build_line_events(chunk_schedule)
#     if not line_events:
#         raise ValueError("Inga line events kunde byggas.")

#     instance = vlc.Instance()
#     player = instance.media_player_new()
#     media = instance.media_new(str(audio_path))
#     player.set_media(media)

#     player.play()
#     time.sleep(0.4)

#     next_index = 0
#     paused = False
#     running = True
#     last_status = "Redo."
#     log_lines: deque[str] = deque(maxlen=LOG_HISTORY_LINES)

#     try:
#         while running:
#             key = stdscr.getch()

#             if key != -1:
#                 if key in (ord("q"), ord("Q")):
#                     last_status = "Avslutar..."
#                     running = False

#                 elif key in (ord("p"), ord("P")):
#                     player.pause()
#                     paused = True
#                     last_status = "Pausad."

#                 elif key in (ord("r"), ord("R")):
#                     player.play()
#                     paused = False
#                     last_status = "Fortsätter."

#                 elif key == ord(" "):
#                     if paused:
#                         player.play()
#                         paused = False
#                         last_status = "Fortsätter."
#                     else:
#                         player.pause()
#                         paused = True
#                         last_status = "Pausad."

#                 elif key == ord("+"):
#                     GLOBAL_AUDIO_OFFSET += OFFSET_STEP
#                     last_status = f"Offset ändrad till {GLOBAL_AUDIO_OFFSET:+.2f}s"

#                 elif key == ord("-"):
#                     GLOBAL_AUDIO_OFFSET -= OFFSET_STEP
#                     last_status = f"Offset ändrad till {GLOBAL_AUDIO_OFFSET:+.2f}s"

#                 elif key in (ord("s"), ord("S")):
#                     pos = get_player_position_seconds(player)
#                     last_status = (
#                         f"Status -> tid {pos:.2f}s | "
#                         f"offset {GLOBAL_AUDIO_OFFSET:+.2f}s | "
#                         f"nästa rad {min(next_index + 1, len(line_events))}/{len(line_events)}"
#                     )

#             if not paused:
#                 current_pos = get_player_position_seconds(player)

#                 while next_index < len(line_events):
#                     item = line_events[next_index]
#                     target_time = max(0.0, item["print_time"] - GLOBAL_AUDIO_OFFSET)

#                     if current_pos >= target_time:
#                         if DRY_RUN:
#                             append_to_log(log_lines, item["text"], item["is_last_line_in_chunk"])
#                         else:
#                             send_line_to_printer(
#                                 text=item["text"],
#                                 printer_name=PRINTER_NAME,
#                                 is_last_line_in_chunk=item["is_last_line_in_chunk"],
#                             )

#                         next_index += 1
#                     else:
#                         break

#             player_pos = get_player_position_seconds(player)

#             draw_ui(
#                 stdscr=stdscr,
#                 title=data["program"].get("title", "Okänd titel"),
#                 audio_name=audio_path.name,
#                 player_pos=player_pos,
#                 offset=GLOBAL_AUDIO_OFFSET,
#                 paused=paused,
#                 dry_run=DRY_RUN,
#                 next_index=next_index,
#                 total_events=len(line_events),
#                 last_status=last_status,
#                 log_lines=log_lines,
#             )

#             state = player.get_state()
#             if next_index >= len(line_events):
#                 if state in (vlc.State.Ended, vlc.State.Stopped, vlc.State.NothingSpecial):
#                     last_status = "Klart."
#                     draw_ui(
#                         stdscr=stdscr,
#                         title=data["program"].get("title", "Okänd titel"),
#                         audio_name=audio_path.name,
#                         player_pos=player_pos,
#                         offset=GLOBAL_AUDIO_OFFSET,
#                         paused=paused,
#                         dry_run=DRY_RUN,
#                         next_index=next_index,
#                         total_events=len(line_events),
#                         last_status=last_status,
#                         log_lines=log_lines,
#                     )
#                     time.sleep(1.0)
#                     break

#             time.sleep(POLL_INTERVAL)

#     finally:
#         player.stop()


# def main() -> None:
#     curses.wrapper(run)


# if __name__ == "__main__":
#     main()











## ------------------------------------------------------------------ ##
## DEN ENKLARE VERSIONEN UTAN CURSES-UI, BARA KOMMANDO OCH SIMULERAD SKRIVARE I TERMINALEN, SOM INTE KAN ÄNDRA OFFSET LIVE, BARA PAUSA OCH FORTSÄTTA. LÄMNAS KVAR NEDANFÖR SOM REFERENS OCH STARTPUNKT FÖR DEN MER AVANCERADE VERSIONEN OVANFÖR.


# import json
# import time
# import textwrap
# import threading
# import queue
# import subprocess
# from pathlib import Path
# from typing import Optional

# import vlc


# # NOTE: Den här versionen gör:

# # den synkar mot ljudets faktiska uppspelningstid i stället för bara en timer
# # man kan pausa / fortsätta
# # man kan ändra offset live
# # man kan simulera kvittoskrivaren i terminalen


# # TODO: behöver installera VLC bindings: python -m pip install python-vlc

# # När skriptet körs kan man skriva i terminalen:
# #   pause        -> pausa ljud
# #   resume       -> fortsätt ljud
# #   offset 0.3   -> skriv ut text 0.3 sek tidigare => om ljudet kanske är lite före i uppspelningen
# #   offset -0.2  -> skriv ut text 0.2 sek senare => om ljudet kanske är lite efter i uppspelningen
# #   status       -> visa uppspelningstid och offset
# #   quit         -> avsluta

# # kör SKRIPTET:
# #   source .venv/bin/activate
# #   python radio-to-receipt-ny.py 
# #
# # avsluta SKRIPTET:
# #   quit


# # =========================
# # KONFIG
# # =========================
# JSON_FILE = "spraket_ai_sync_segments.json"

# # True = simulera skrivare i terminalen
# # False = skicka till kvittoskrivare via lp
# DRY_RUN = True

# # Sätt skrivarnamn om du vill skriva ut på riktigt
# PRINTER_NAME = "Star_TSP100III"   # eller None

# # För 80 mm kvitto är ungefär 42–48 tecken ofta rimligt
# RECEIPT_WIDTH = 42

# # Extra tomrader efter sista raden i en chunk
# EXTRA_FEED_LINES = 2

# # Positivt värde = text tidigare
# # Negativt värde = text senare
# GLOBAL_AUDIO_OFFSET = 0.0

# # Hur ofta schedulern kollar om något ska skrivas ut
# POLL_INTERVAL = 0.05


# # =========================
# # GLOBALT KÖLÄGE FÖR KOMMANDON
# # =========================
# command_queue: queue.Queue[str] = queue.Queue()


# # =========================
# # HJÄLPFUNKTIONER
# # =========================
# def load_data(json_path: str) -> dict:
#     path = Path(json_path)
#     if not path.exists():
#         raise FileNotFoundError(f"JSON-filen hittades inte: {json_path}")

#     with path.open("r", encoding="utf-8") as f:
#         return json.load(f)


# def resolve_audio_path(data: dict, json_path: str) -> Path:
#     audio_name = data["program"]["audio_file"]
#     json_dir = Path(json_path).resolve().parent
#     audio_path = (json_dir / audio_name).resolve()

#     if not audio_path.exists():
#         raise FileNotFoundError(f"Ljudfilen hittades inte: {audio_path}")

#     return audio_path


# def wrap_text_to_lines(text: str, width: int = RECEIPT_WIDTH) -> list[str]:
#     paragraphs = text.splitlines() or [text]
#     lines: list[str] = []

#     for paragraph in paragraphs:
#         paragraph = paragraph.strip()

#         if not paragraph:
#             lines.append("")
#             continue

#         wrapped = textwrap.wrap(
#             paragraph,
#             width=width,
#             break_long_words=False,
#             break_on_hyphens=False
#         )

#         if wrapped:
#             lines.extend(wrapped)
#         else:
#             lines.append("")

#     return lines


# def find_next_chunk_print_time(schedule: list[dict], current_index: int, fallback: float = 2.0) -> float:
#     """
#     Hitta nästa chunks starttid.
#     Om det inte finns någon nästa chunk, använd fallback-sekunder.
#     """
#     if current_index + 1 < len(schedule):
#         return schedule[current_index + 1]["print_time"]
#     return schedule[current_index]["print_time"] + fallback


# def flatten_schedule(data: dict) -> list[dict]:
#     """
#     Läser segment/chunks från JSON och skapar en platt chunk-lista.
#     Varje chunk får sin starttid.
#     """
#     schedule = []

#     for segment in data["segments"]:
#         segment_start = segment["start_seconds"]

#         for chunk in segment["print_chunks"]:
#             actual_print_time = segment_start + chunk["offset_seconds"]

#             schedule.append({
#                 "segment_id": segment["id"],
#                 "chunk_id": chunk["chunk_id"],
#                 "print_time": float(actual_print_time),
#                 "text": chunk["text"],
#             })

#     schedule.sort(key=lambda item: item["print_time"])
#     return schedule


# def build_line_events(schedule: list[dict]) -> list[dict]:
#     """
#     Bygger en lista med line events.
#     Varje rad i en chunk får en egen utskriftstid.
#     """
#     events: list[dict] = []

#     for index, chunk in enumerate(schedule):
#         chunk_start = chunk["print_time"]
#         next_chunk_time = find_next_chunk_print_time(schedule, index, fallback=2.0)
#         available_duration = max(0.8, next_chunk_time - chunk_start)

#         lines = wrap_text_to_lines(chunk["text"], RECEIPT_WIDTH)

#         if not lines:
#             continue

#         if len(lines) == 1:
#             line_times = [chunk_start]
#         else:
#             step = available_duration / len(lines)
#             line_times = [chunk_start + (i * step) for i in range(len(lines))]

#         for i, line in enumerate(lines):
#             events.append({
#                 "segment_id": chunk["segment_id"],
#                 "chunk_id": chunk["chunk_id"],
#                 "line_index": i,
#                 "print_time": float(line_times[i]),
#                 "text": line,
#                 "is_last_line_in_chunk": i == len(lines) - 1
#             })

#     events.sort(key=lambda item: item["print_time"])
#     return events


# def simulate_printer_output_line(text: str, is_last_line_in_chunk: bool) -> None:
#     print(text)
#     if is_last_line_in_chunk:
#         print("\n" * (EXTRA_FEED_LINES - 1), end="")


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


# def print_or_send_line(text: str, printer_name: Optional[str], dry_run: bool, is_last_line_in_chunk: bool) -> None:
#     if dry_run:
#         simulate_printer_output_line(text, is_last_line_in_chunk)
#     else:
#         send_line_to_printer(text, printer_name, is_last_line_in_chunk)


# def get_player_position_seconds(player: vlc.MediaPlayer) -> float:
#     current_ms = player.get_time()
#     if current_ms < 0:
#         return 0.0
#     return current_ms / 1000.0


# def command_listener() -> None:
#     while True:
#         try:
#             command = input().strip()
#             command_queue.put(command)
#             if command == "quit":
#                 break
#         except EOFError:
#             break


# def print_help() -> None:
#     print("Kommandon under körning:")
#     print("  pause        -> pausa ljud")
#     print("  resume       -> fortsätt ljud")
#     print("  offset 0.3   -> skriv ut text 0.3 sek tidigare")
#     print("  offset -0.2  -> skriv ut text 0.2 sek senare")
#     print("  status       -> visa uppspelningstid och offset")
#     print("  quit         -> avsluta")
#     print()


# # =========================
# # HUVUDPROGRAM
# # =========================
# def main() -> None:
#     global GLOBAL_AUDIO_OFFSET

#     data = load_data(JSON_FILE)
#     audio_path = resolve_audio_path(data, JSON_FILE)

#     chunk_schedule = flatten_schedule(data)
#     if not chunk_schedule:
#         raise ValueError("Inga print_chunks hittades i JSON-filen.")

#     line_events = build_line_events(chunk_schedule)
#     if not line_events:
#         raise ValueError("Inga line events kunde byggas.")

#     print("Program:", data["program"].get("title", "Okänd titel"))
#     print("Ljudfil:", audio_path.name)
#     print("Antal chunks:", len(chunk_schedule))
#     print("Antal rader att skriva ut:", len(line_events))
#     print("Läge:", "SIMULERAD SKRIVARE" if DRY_RUN else "RIKTIG SKRIVARE")
#     print("Kvittobredd:", RECEIPT_WIDTH, "tecken")
#     print("Start-offset:", GLOBAL_AUDIO_OFFSET, "sek")
#     print()
#     print_help()

#     listener_thread = threading.Thread(target=command_listener, daemon=True)
#     listener_thread.start()

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
#             while not command_queue.empty():
#                 command = command_queue.get()

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
#                             print("Kunde inte läsa offset. Exempel: offset 0.3")

#                 elif command == "status":
#                     pos = get_player_position_seconds(player)
#                     print(
#                         f"Status -> tid: {pos:.2f}s | "
#                         f"offset: {GLOBAL_AUDIO_OFFSET:.2f}s | "
#                         f"nästa rad: {next_index + 1}/{len(line_events)}"
#                     )

#                 elif command == "quit":
#                     print("Avslutar...")
#                     running = False
#                     break

#                 elif command:
#                     print("Okänt kommando.")

#             if not running:
#                 break

#             if paused:
#                 time.sleep(POLL_INTERVAL)
#                 continue

#             current_pos = get_player_position_seconds(player)

#             while next_index < len(line_events):
#                 item = line_events[next_index]
#                 target_time = max(0.0, item["print_time"] - GLOBAL_AUDIO_OFFSET)

#                 if current_pos >= target_time:
#                     print_or_send_line(
#                         text=item["text"],
#                         printer_name=PRINTER_NAME,
#                         dry_run=DRY_RUN,
#                         is_last_line_in_chunk=item["is_last_line_in_chunk"]
#                     )
#                     next_index += 1
#                 else:
#                     break

#             state = player.get_state()
#             if next_index >= len(line_events):
#                 if state in (vlc.State.Ended, vlc.State.Stopped, vlc.State.NothingSpecial):
#                     break

#             time.sleep(POLL_INTERVAL)

#     except KeyboardInterrupt:
#         print("\nAvbrutet av användaren.")

#     finally:
#         player.stop()


# if __name__ == "__main__":
#     main()















## ------------------------------------------------------------------ ##
## DENNA KODEN SKRIVER UT RADERNA I CHUNKSEN UTIFRÅN LJUDETS UPPPELNINGSTID


# import json
# import time
# import textwrap
# import threading
# import queue
# import subprocess
# from pathlib import Path
# from typing import Optional

# import vlc

# # NOTE: Den här versionen gör:

# # den synkar mot ljudets faktiska uppspelningstid i stället för bara en timer
# # man kan pausa / fortsätta
# # man kan ändra offset live
# # man kan simulera kvittoskrivaren i terminalen


# # TODO: behöver installera VLC bindings: python -m pip install python-vlc

# # När skriptet körs kan man skriva i terminalen:
# #   pause        -> pausa ljud
# #   resume       -> fortsätt ljud
# #   offset 0.3   -> skriv ut text 0.3 sek tidigare => om ljudet kanske är lite före i uppspelningen
# #   offset -0.2  -> skriv ut text 0.2 sek senare => om ljudet kanske är lite efter i uppspelningen
# #   status       -> visa uppspelningstid och offset
# #   quit         -> avsluta

# # kör SKRIPTET:
# #   source .venv/bin/activate
# #   python radio-to-receipt-ny.py 
# #
# # avsluta SKRIPTET:
# #   quit


# # =========================
# # KONFIG
# # =========================
# JSON_FILE = "spraket_ai_sync_segments.json"

# # True = simulera skrivare i terminalen
# # False = skicka till kvittoskrivare via lp
# DRY_RUN = True

# # Sätt skrivarnamn om du vill skriva ut på riktigt
# PRINTER_NAME = "Star_TSP100III"   # eller None

# # För 80 mm kvitto är ungefär 42–48 tecken ofta rimligt.
# RECEIPT_WIDTH = 42

# # Extra tomrader efter sista raden i en chunk
# EXTRA_FEED_LINES = 2

# # Positivt värde = text tidigare
# # Negativt värde = text senare
# GLOBAL_AUDIO_OFFSET = 0.0

# # Hur ofta schedulern kollar om något ska skrivas ut
# POLL_INTERVAL = 0.05


# # =========================
# # GLOBALT KÖLÄGE FÖR KOMMANDON
# # =========================
# command_queue: queue.Queue[str] = queue.Queue()


# # =========================
# # HJÄLPFUNKTIONER
# # =========================
# def load_data(json_path: str) -> dict:
#     path = Path(json_path)
#     if not path.exists():
#         raise FileNotFoundError(f"JSON-filen hittades inte: {json_path}")

#     with path.open("r", encoding="utf-8") as f:
#         return json.load(f)


# def resolve_audio_path(data: dict, json_path: str) -> Path:
#     audio_name = data["program"]["audio_file"]
#     json_dir = Path(json_path).resolve().parent
#     audio_path = (json_dir / audio_name).resolve()

#     if not audio_path.exists():
#         raise FileNotFoundError(f"Ljudfilen hittades inte: {audio_path}")

#     return audio_path


# def flatten_schedule(data: dict) -> list[dict]:
#     schedule = []

#     for segment in data["segments"]:
#         segment_start = segment["start_seconds"]

#         for chunk in segment["print_chunks"]:
#             actual_print_time = segment_start + chunk["offset_seconds"]

#             schedule.append({
#                 "segment_id": segment["id"],
#                 "chunk_id": chunk["chunk_id"],
#                 "print_time": float(actual_print_time),
#                 "text": chunk["text"],
#             })

#     schedule.sort(key=lambda item: item["print_time"])
#     return schedule


# def wrap_receipt_text(text: str, width: int = RECEIPT_WIDTH) -> str:
#     paragraphs = text.splitlines() or [text]
#     wrapped_parts = []

#     for paragraph in paragraphs:
#         paragraph = paragraph.strip()
#         if not paragraph:
#             wrapped_parts.append("")
#             continue

#         wrapped_lines = textwrap.wrap(
#             paragraph,
#             width=width,
#             break_long_words=False,
#             break_on_hyphens=False
#         )
#         wrapped_parts.append("\n".join(wrapped_lines))

#     return "\n".join(wrapped_parts)


# def build_receipt_text(text: str) -> str:
#     wrapped = wrap_receipt_text(text, RECEIPT_WIDTH)
#     return wrapped.rstrip() + ("\n" * EXTRA_FEED_LINES)


# def simulate_printer_output(text: str) -> None:
#     receipt_text = build_receipt_text(text)

#     print("\n" + "=" * RECEIPT_WIDTH)
#     print(receipt_text.rstrip())
#     print("=" * RECEIPT_WIDTH + "\n")


# def send_to_printer(text: str, printer_name: Optional[str] = None) -> None:
#     receipt_text = build_receipt_text(text)

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


# def print_or_send(text: str, printer_name: Optional[str], dry_run: bool) -> None:
#     if dry_run:
#         simulate_printer_output(text)
#     else:
#         send_to_printer(text, printer_name)


# def get_player_position_seconds(player: vlc.MediaPlayer) -> float:
#     current_ms = player.get_time()
#     if current_ms < 0:
#         return 0.0
#     return current_ms / 1000.0


# def command_listener() -> None:
#     """
#     Lyssnar på terminalkommandon i bakgrunden.
#     Exempel:
#       pause
#       resume
#       offset 0.3
#       offset -0.2
#       status
#       quit
#     """
#     while True:
#         try:
#             command = input().strip()
#             command_queue.put(command)
#             if command == "quit":
#                 break
#         except EOFError:
#             break


# def print_help() -> None:
#     print("Kommandon under körning:")
#     print("  pause        -> pausa ljud")
#     print("  resume       -> fortsätt ljud")
#     print("  offset 0.3   -> skriv ut text 0.3 sek tidigare")
#     print("  offset -0.2  -> skriv ut text 0.2 sek senare")
#     print("  status       -> visa uppspelningstid och offset")
#     print("  quit         -> avsluta")
#     print()


# # =========================
# # HUVUDPROGRAM
# # =========================
# def main() -> None:
#     global GLOBAL_AUDIO_OFFSET

#     data = load_data(JSON_FILE)
#     audio_path = resolve_audio_path(data, JSON_FILE)
#     schedule = flatten_schedule(data)

#     if not schedule:
#         raise ValueError("Inga print_chunks hittades i JSON-filen.")

#     print("Program:", data["program"].get("title", "Okänd titel"))
#     print("Ljudfil:", audio_path.name)
#     print("Antal utskriftsblock:", len(schedule))
#     print("Läge:", "SIMULERAD SKRIVARE" if DRY_RUN else "RIKTIG SKRIVARE")
#     print("Kvittobredd:", RECEIPT_WIDTH, "tecken")
#     print("Start-offset:", GLOBAL_AUDIO_OFFSET, "sek")
#     print()
#     print_help()

#     listener_thread = threading.Thread(target=command_listener, daemon=True)
#     listener_thread.start()

#     instance = vlc.Instance()
#     player = instance.media_player_new()
#     media = instance.media_new(str(audio_path))
#     player.set_media(media)

#     # Starta ljudet
#     player.play()

#     # Vänta lite så VLC hinner starta
#     time.sleep(0.4)

#     next_index = 0
#     paused = False
#     running = True

#     try:
#         while running:
#             # Hantera kommandon
#             while not command_queue.empty():
#                 command = command_queue.get()

#                 if command == "pause":
#                     player.pause()
#                     paused = True
#                     print("Pausad.")

#                 elif command == "resume":
#                     # VLC använder samma pause()-anrop för toggle,
#                     # men play() fungerar bra här för att fortsätta.
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
#                             print("Kunde inte läsa offset. Exempel: offset 0.3")

#                 elif command == "status":
#                     pos = get_player_position_seconds(player)
#                     print(
#                         f"Status -> tid: {pos:.2f}s | "
#                         f"offset: {GLOBAL_AUDIO_OFFSET:.2f}s | "
#                         f"nästa chunk: {next_index + 1}/{len(schedule)}"
#                     )

#                 elif command == "quit":
#                     print("Avslutar...")
#                     running = False
#                     break

#                 elif command:
#                     print("Okänt kommando.")

#             if not running:
#                 break

#             if paused:
#                 time.sleep(POLL_INTERVAL)
#                 continue

#             current_pos = get_player_position_seconds(player)

#             # Skriv ut alla chunks som nu är "förfallna"
#             while next_index < len(schedule):
#                 item = schedule[next_index]
#                 target_time = max(0.0, item["print_time"] - GLOBAL_AUDIO_OFFSET)

#                 if current_pos >= target_time:
#                     print_or_send(item["text"], PRINTER_NAME, DRY_RUN)
#                     next_index += 1
#                 else:
#                     break

#             # Om ljudet är klart och allt är utskrivet
#             state = player.get_state()
#             if next_index >= len(schedule):
#                 if state in (vlc.State.Ended, vlc.State.Stopped, vlc.State.NothingSpecial):
#                     break

#             time.sleep(POLL_INTERVAL)

#     except KeyboardInterrupt:
#         print("\nAvbrutet av användaren.")

#     finally:
#         player.stop()


# if __name__ == "__main__":
#     main()