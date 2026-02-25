## Examen - Work

## Radio → LLM → Receipt Printer

Radio → LLM → Receipt Printer is an experimental media prototype that listens to live web radio, transcribes the audio using OpenAI Speech-to-Text, lets a language model interpret the content, and prints the generated output on a thermal receipt printer (Star TSP100III).

The project explores how AI can reinterpret live information streams and materialize them as physical text in real time.

---

## What It Does

- **Live audio input**
  - Connects to a web radio stream (MP3/AAC).

- **Audio chunk recording**
  - Uses `ffmpeg` to capture short audio segments (e.g., 12 seconds).
  - Saves them as `.wav` files.

- **Speech-to-Text (STT)**
  - Sends audio chunks to OpenAI for transcription.
  - Converts spoken news into raw text.

- **LLM interpretation**
  - The transcribed text is processed by a language model.
  - The model decides whether something is “print-worthy”.
  - If so, it generates a formatted receipt-style output.

- **Physical output**
  - Sends raw ESC/POS data to a Star TSP100III via CUPS.
  - Prints AI-interpreted content as a receipt.

---

## Installation (macOS)

1. Install Dependencies

- Installera Homebrew (om du inte har det)

Öppna Terminal och kör:

```bash

- Installera ffmpeg
    brew install ffmpeg

- Testa:
    ffmpeg -version

- Installera Python (via brew)
    brew install python

- Testa:
    python3 --version
    pip3 --version

```

## Set Up the Project

- Clone or navigate to the project folder: => finns på GitHub examen-work

```bash

- Projekt
  cd examen-work

- Create a virtual environment:

  python3 -m venv .venv
  source .venv/bin/activate


- Skapa en virtuell miljö - Detta gör att vi slipper installera paket globalt på datorn.

  python3 -m venv .venv
  source .venv/bin/activate

=> Vi ser nu (.venv) i terminalen.

- Installera OpenAI Python-paket
    pip install openai

```

## Add OpenAI API Key

```bash

- Sätt din OpenAI API-nyckel
    Tillfälligt i terminalen:

  export OPENAI_API_KEY="DIN_NYCKEL_HÄR"

- Vill man slippa göra detta varje gång:

  echo 'export OPENAI_API_KEY="DIN_NYCKEL_HÄR"' >> ~/.zshrc
  source ~/.zshrc

```

## Printer Setup (Star TSP100III)

Add the printer in:

- System Settings → Printers & Scanners

```bash

- Koppla Star TSP100III (CUPS) - Kvittoskrivaren

1. Lägg till skrivaren

Gå till:

Systeminställningar → Skrivare och skannrar

Lägg till din Star TSP100III.

- Hitta CUPS-namnet - Check the printer name:

I terminalen:
  lpstat -p

  Exempel out:  printer Star_TSP100III is idle ...

- Då ska man använda:
    PRINTER_NAME = "Star_TSP100III"

=> Testa utskrift

echo "Hej eller Test print" | lp -d Star_TSP100III

- Om detta => Fungerar då är skrivaren korrekt kopplad.

```

## Projektstruktur

radio-llm-receipt/
│
├── .venv/
├── radio_to_receipt.py
└── out/

`.venv/` → virtuell miljö

`radio_to_receipt.py` → huvudprogram

`out/` → skapas automatiskt av programmet

---

## Köra programmet

- När allt är installerat: Kör programet i teriminalen

```bash

source .venv/bin/activate
python radio_to_receipt.py

```

## Om Programmet börjar då:

- Spela in radiostream
- Transkribera
- Generera tolkning
- Skriva ut kvitto (om modellen avgör att något ska skrivas ut)

---
