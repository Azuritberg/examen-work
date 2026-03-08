import json
import time
import shutil
import subprocess
from pathlib import Path
from typing import Optional


# =========================
# KONFIG
# =========================
JSON_FILE = "spraket_ai_sync_segments.json"
DRY_RUN = True              # True = skriv bara till terminalen, False = skicka till skrivare
PRINTER_NAME = None         # ändra till "Star_TSP100III" eller None för standardskrivare
POLL_INTERVAL = 0.05        # hur ofta vi kollar tiden (sekunder)
EXTRA_FEED_LINES = 2        # extra tomrader efter varje chunk
GLOBAL_AUDIO_OFFSET = 0.0   # 0.3  # Justera detta värde (i sekunder) för att kompensera för eventuella fördröjningar i ljuduppspelningen eller skrivaren. Positivt värde gör att texten skrivs ut tidigare, negativt gör att den skrivs ut senare.


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


def flatten_schedule(data: dict) -> list[dict]:
    schedule = []

    for segment in data["segments"]:
        segment_start = segment["start_seconds"]

        for chunk in segment["print_chunks"]:
            actual_print_time = segment_start + chunk["offset_seconds"]

            schedule.append({
                "segment_id": segment["id"],
                "chunk_id": chunk["chunk_id"],
                "print_time": actual_print_time,
                "text": chunk["text"],
                "segment_start": segment["start_seconds"],
                "segment_end": segment["end_seconds"],
            })

    schedule.sort(key=lambda item: item["print_time"])
    return schedule


def build_receipt_text(text: str) -> str:
    return text.rstrip() + "\n" * EXTRA_FEED_LINES


def send_to_printer(text: str, printer_name: Optional[str] = None) -> None:
    receipt_text = build_receipt_text(text)

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


def print_or_send(text: str, printer_name: Optional[str], dry_run: bool) -> None:
    if dry_run:
        print(text)
    else:
        send_to_printer(text, printer_name)


def start_audio_player(audio_path: Path) -> subprocess.Popen:
    if shutil.which("afplay") is None:
        raise RuntimeError("Kunde inte hitta 'afplay'. Detta script är gjort för macOS.")

    return subprocess.Popen(["afplay", str(audio_path)])


# =========================
# HUVUDPROGRAM
# =========================
def main() -> None:
    data = load_data(JSON_FILE)
    audio_path = resolve_audio_path(data, JSON_FILE)
    schedule = flatten_schedule(data)

    if not schedule:
        raise ValueError("Inga print_chunks hittades i JSON-filen.")

    print("Program:", data["program"].get("title", "Okänd titel"))
    print("Ljudfil:", audio_path.name)
    print("Antal utskriftsblock:", len(schedule))
    print("Läge:", "DRY RUN" if DRY_RUN else "SKRIVARE")
    print()

    player = start_audio_player(audio_path)
    start_monotonic = time.monotonic()

    try:
        for item in schedule:
            target_time = max(0, item["print_time"] - GLOBAL_AUDIO_OFFSET) # Se till att mål-tiden inte blir negativ, vilket kan hända om offseten är större än print_time
            
            
            # target_time = item["print_time"] - GLOBAL_AUDIO_OFFSET # Justera mål-tiden med den globala offseten

            while True:
                elapsed = time.monotonic() - start_monotonic
                remaining = target_time - elapsed

                if remaining <= 0:
                    break

                time.sleep(min(POLL_INTERVAL, remaining))

            output_text = item["text"]
            print_or_send(output_text, PRINTER_NAME, DRY_RUN)

        player.wait()

    except KeyboardInterrupt:
        print("\nAvbrutet av användaren.")
        player.terminate()
        raise

    finally:
        if player.poll() is None:
            player.terminate()


if __name__ == "__main__":
    main()



#  TODO:

# global timing-offset

# olika print-stilar för rubrik / talare / brödtext

# automatisk radbrytning för kvittobredd

# loggfil med exakt när varje chunk faktiskt skickades

# stöd för “cut” mellan sektioner om skrivaren klarar det

# Nästa steg är att anpassas koden/texten till kvittobredd, så att varje chunk automatiskt bryts snyggt för Star TSP100III.





## ------------------------------------------------------------------ ##

# KOMMENTARER: Koden här nedan är den ursprungliga versionen som jag utgick ifrån när jag gjorde ändringarna ovan. Jag har lämnat den kvar här i kommenterad form för att visa vad som har ändrats och för att underlätta jämförelse. Denna del kan ignoreras eller tas bort i den slutgiltiga versionen. 

# De största ändringarna i den nya versionen är: att denna koden har en time-stamp i början av varje utskriftsblock, att den inte längre har segment/chunk-id i prefixet, att den inte längre har segmentstart/segmentend i varje item, och att den inte längre har en horisontell linje mellan varje block i terminalen när DRY_RUN är True.

# EXEMPEL:  
#  [00:10] segment 1 / chunk 1
#  Sanna Carlsson, hur mycket använder du artificiell intelligens?
#  ----------------------------------------

#  [00:14] segment 1 / chunk 2
#  Väldigt lite som jag vet om, men ganska mycket som jag inte tänker på.
#  ----------------------------------------

## Utan texten skrivs ut i terminalen så ser det ut så här:

# Sanna Carlsson, hur mycket använder du artificiell intelligens?
# Väldigt lite som jag vet om, men ganska mycket som jag inte tänker på.

# import json
# import time
# import shutil
# import subprocess
# from pathlib import Path
# from typing import Optional


# # =========================
# # KONFIG
# # =========================
# JSON_FILE = "spraket_ai_sync_segments.json"
# DRY_RUN = True            # True = skriv bara till terminalen, False = skicka till skrivare
# PRINTER_NAME = None       # t.ex. "Star_TSP100III" eller None för standardskrivare
# POLL_INTERVAL = 0.05      # hur ofta vi kollar tiden (sekunder)
# EXTRA_FEED_LINES = 2      # extra tomrader efter varje chunk
# GLOBAL_AUDIO_OFFSET = 0.0 # 0.3  # Justera detta värde (i sekunder) för att kompensera för eventuella fördröjningar i ljuduppspelningen eller skrivaren. Positivt värde gör att texten skrivs ut tidigare, negativt gör att den skrivs ut senare.

# # =========================
# # HJÄLPFUNKTIONER
# # =========================
# def format_mmss(seconds: float) -> str:
#     total = max(0, int(seconds))
#     minutes = total // 60
#     secs = total % 60
#     return f"{minutes:02d}:{secs:02d}"


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
#     """
#     Gör om nested segments -> print_chunks till en platt lista
#     med exakta utskriftstider.
#     """
#     schedule = []

#     for segment in data["segments"]:
#         segment_start = segment["start_seconds"]

#         for chunk in segment["print_chunks"]:
#             actual_print_time = segment_start + chunk["offset_seconds"]

#             schedule.append({
#                 "segment_id": segment["id"],
#                 "chunk_id": chunk["chunk_id"],
#                 "print_time": actual_print_time,
#                 "text": chunk["text"],
#                 "segment_start": segment["start_seconds"],
#                 "segment_end": segment["end_seconds"],
#             })

#     schedule.sort(key=lambda item: item["print_time"])
#     return schedule


# def build_receipt_text(text: str) -> str:
#     return text.rstrip() + "\n" * EXTRA_FEED_LINES


# def send_to_printer(text: str, printer_name: Optional[str] = None) -> None:
#     """
#     Skickar text till CUPS via lp.
#     lp kan läsa från stdin om man använder '-'.
#     """
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
#         print(text)
#         print("-" * 40)
#     else:
#         send_to_printer(text, printer_name)


# def start_audio_player(audio_path: Path) -> subprocess.Popen:
#     """
#     Startar ljudet på macOS via afplay.
#     """
#     if shutil.which("afplay") is None:
#         raise RuntimeError("Kunde inte hitta 'afplay'. Detta script är gjort för macOS.")

#     return subprocess.Popen(["afplay", str(audio_path)])


# # =========================
# # HUVUDPROGRAM
# # =========================
# def main() -> None:
#     data = load_data(JSON_FILE)
#     audio_path = resolve_audio_path(data, JSON_FILE)
#     schedule = flatten_schedule(data)

#     if not schedule:
#         raise ValueError("Inga print_chunks hittades i JSON-filen.")

#     print("Program:", data["program"].get("title", "Okänd titel"))
#     print("Ljudfil:", audio_path.name)
#     print("Antal utskriftsblock:", len(schedule))
#     print("Läge:", "DRY RUN" if DRY_RUN else "SKRIVARE")
#     print()

#     player = start_audio_player(audio_path)
#     start_monotonic = time.monotonic()

#     try:
#         for item in schedule:
#              target_time = max(0, item["print_time"] - GLOBAL_AUDIO_OFFSET)
#            # target_time = item["print_time"]

#             while True:
#                 elapsed = time.monotonic() - start_monotonic
#                 remaining = target_time - elapsed

#                 if remaining <= 0:
#                     break

#                 time.sleep(min(POLL_INTERVAL, remaining))

#             stamp = format_mmss(item["print_time"])
#             prefix = f"[{stamp}] segment {item['segment_id']} / chunk {item['chunk_id']}\n"
#             output_text = prefix + item["text"]

#             print_or_send(output_text, PRINTER_NAME, DRY_RUN)

#         # Vänta tills ljudspelaren är klar
#         player.wait()

#     except KeyboardInterrupt:
#         print("\nAvbrutet av användaren.")
#         player.terminate()
#         raise

#     finally:
#         if player.poll() is None:
#             player.terminate()


# if __name__ == "__main__":
#     main()