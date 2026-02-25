import os
import json
import time
import uuid
import shutil
import subprocess
from datetime import datetime
from typing import Optional, Dict, Any

# from openai import OpenAI

# =======================
# KONFIG
# =======================
STREAM_URL = "https://DIN-WEBBRADIO-STREAM-URL-HÄR"   # mp3/aac stream URL
CHUNK_SECONDS = 12
LANGUAGE = "sv"

# CUPS queue name: kolla med `lpstat -p`
PRINTER_NAME = "Star_TSP100III"  # eller None för default

TRANSCRIBE_MODEL = "gpt-4o-mini-transcribe"
LLM_MODEL = "gpt-5"

OUT_DIR = "out"
os.makedirs(OUT_DIR, exist_ok=True)

client = OpenAI()


# =======================
# HJÄLP: LOGG
# =======================
def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# =======================
# 1) AUDIO CAPTURE
# =======================
def ensure_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "ffmpeg hittades inte. Installera med: brew install ffmpeg"
        )


def capture_audio_chunk(stream_url: str, seconds: int, out_path: str) -> None:
    """
    Spelar in en kort ljudbit från stream och sparar som WAV 16kHz mono.
    Reconnect-flaggor hjälper när streamen droppar.
    """
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
        "-i", stream_url,
        "-t", str(seconds),
        "-ac", "1",
        "-ar", "16000",
        "-f", "wav",
        out_path,
    ]
    subprocess.run(cmd, check=True)


# =======================
# 2) TRANSKRIBERA
# =======================
def transcribe_wav(path: str) -> str:
    with open(path, "rb") as f:
        tr = client.audio.transcriptions.create(
            model=TRANSCRIBE_MODEL,
            file=f,
            response_format="text",
            language=LANGUAGE,
        )
    return (getattr(tr, "text", tr) or "").strip()


# =======================
# 3) LLM: BESLUT + KVITTO-TEXT
# =======================
def decide_and_compose_receipt(transcript: str, memory: str) -> Dict[str, Any]:
    """
    Returnerar JSON:
    {
      "should_print": true/false,
      "title": "...",
      "body": "...",
      "memory_update": "..."
    }
    """
    instructions = (
        "Du lyssnar på svensk webbradio. Du får ett transkript.\n"
        "Uppgift: avgör om något bör skrivas ut som en kort anteckning på kvitto.\n\n"
        "Skriv bara ut om transkriptet innehåller tydlig, konkret och användbar information:\n"
        "- datum/tid/plats\n"
        "- instruktioner, uppmaningar\n"
        "- tydliga viktiga punkter\n\n"
        "Skriv INTE ut om det mest är:\n"
        "- musikprat, reklam utan nytta, småprat\n"
        "- otydligt/fragment\n\n"
        "Returnera ALLTID giltig JSON med exakt nycklarna:\n"
        "{\n"
        '  "should_print": true/false,\n'
        '  "title": "Kort rubrik",\n'
        '  "body": "Max 1200 tecken. Använd radbrytningar.",\n'
        '  "memory_update": "Max 400 tecken: viktig kontext framåt"\n'
        "}\n"
        "Svara inte med något annat än JSON."
    )

    input_text = (
        f"MINNE (kan vara tomt):\n{memory}\n\n"
        f"NYTT TRANSKRIPT:\n{transcript}\n"
    )

    resp = client.responses.create(
        model=LLM_MODEL,
        instructions=instructions,
        input=input_text,
    )

    raw = (resp.output_text or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"Inget JSON i modellsvaret:\n{raw}")

    data = json.loads(raw[start:end + 1])

    for key in ("should_print", "title", "body", "memory_update"):
        if key not in data:
            raise ValueError(f"Saknar nyckel {key} i JSON: {data}")

    # städa
    data["title"] = str(data["title"] or "").strip()
    data["body"] = str(data["body"] or "").strip()
    data["memory_update"] = str(data["memory_update"] or "").strip()

    # hårda gränser (så att kvittot inte ballar ur)
    data["body"] = data["body"][:1200]
    data["memory_update"] = data["memory_update"][:400]

    return data


# =======================
# 4) ESC/POS-BYTES (KVITTO)
# =======================
def escpos_receipt_bytes(title: str, body: str) -> bytes:
    """
    Enkel ESC/POS-layout:
    - init
    - center + bold rubrik
    - body vänster
    - timestamp
    - feed + cut

    OBS: Kräver att skrivaren använder ESC/POS / Star emulation som accepterar kommandon.
    """
    b = bytearray()

    # init
    b += b"\x1b\x40"  # ESC @

    # rubrik (center + bold)
    b += b"\x1b\x61\x01"  # ESC a 1 center
    b += b"\x1b\x45\x01"  # ESC E 1 bold on
    b += (title[:48] + "\n").encode("utf-8", errors="replace")
    b += b"\x1b\x45\x00"  # bold off

    # separator
    b += b"\x1b\x61\x00"  # left
    b += ("-" * 32 + "\n").encode("ascii")

    # body
    for line in body.splitlines():
        b += (line[:48] + "\n").encode("utf-8", errors="replace")

    b += (("\n" + "-" * 32 + "\n").encode("ascii"))
    b += datetime.now().strftime("%Y-%m-%d %H:%M").encode("ascii") + b"\n"

    # feed
    b += b"\n\n\n"

    # cut
    b += b"\x1d\x56\x00"  # GS V 0

    return bytes(b)


def print_raw_bytes_to_cups(raw_bytes: bytes, printer_name: Optional[str]) -> None:
    """
    Skickar RAW-jobb via CUPS `lp -o raw`.
    """
    job_path = os.path.join(OUT_DIR, f"receipt_{uuid.uuid4().hex[:8]}.bin")
    with open(job_path, "wb") as f:
        f.write(raw_bytes)

    cmd = ["lp"]
    if printer_name:
        cmd += ["-d", printer_name]
    cmd += ["-o", "raw", job_path]

    subprocess.run(cmd, check=True)


# =======================
# MAIN LOOP
# =======================
def main() -> None:
    ensure_ffmpeg()

    memory = ""
    log("Startar webbradio → STT → LLM → ESC/POS → skrivare. Avsluta med Ctrl+C")

    while True:
        chunk_id = uuid.uuid4().hex[:8]
        wav_path = os.path.join(OUT_DIR, f"chunk_{chunk_id}.wav")

        try:
            capture_audio_chunk(STREAM_URL, CHUNK_SECONDS, wav_path)
        except subprocess.CalledProcessError as e:
            log(f"ffmpeg-fel (stream?): {e}. Försöker igen...")
            time.sleep(2)
            continue

        try:
            transcript = transcribe_wav(wav_path)
        except Exception as e:
            log(f"Transkribering-fel: {e}. Försöker igen...")
            time.sleep(1)
            continue

        if not transcript:
            log("[SKIP] tomt transkript")
            time.sleep(0.5)
            continue

        try:
            decision = decide_and_compose_receipt(transcript, memory)
        except Exception as e:
            log(f"LLM-fel / JSON-fel: {e}. Försöker igen...")
            time.sleep(1)
            continue

        memory = decision["memory_update"]

        if decision["should_print"]:
            title = decision["title"] or "Meddelande"
            body = decision["body"] or transcript

            try:
                raw = escpos_receipt_bytes(title, body)
                print_raw_bytes_to_cups(raw, PRINTER_NAME)
                log(f"[PRINT] {title}")
            except subprocess.CalledProcessError as e:
                log(f"Utskrift-fel (CUPS/lp): {e}")
        else:
            log(f"[SKIP] {transcript[:120]}")

        # broms: så du inte spam:ar API
        time.sleep(0.6)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Avslutar.")