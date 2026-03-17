# InterDirector — AI Filmmaker's Suite

> Free, open-source AI assistant for indie filmmakers. Dailies processing, script breakdown, color grading. Runs locally. Zero cost.

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-green.svg)](https://fastapi.tiangolo.com)
[![Groq](https://img.shields.io/badge/AI-Llama%203.3%2070B-orange.svg)](https://console.groq.com)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## What It Does

### 📽 Dailies Reviewer
Upload your raw shoot day footage. InterDirecto does what a script supervisor and dailies reviewer do manually every night:

- **Whisper** transcribes all audio locally — no cloud, no cost
- AI identifies every take with timecode
- Rates each take: **PRINT / GOOD / NG**
- Detects focus, exposure, camera stability from actual frames (OpenCV)
- Flags continuity issues and ADR candidates
- Summarises director and AD communications
- Exports a **professional PDF report** ready for the editing room

### 📄 Script Breakdown
Upload any PDF screenplay:

- Extracts every scene, character, location, prop
- Flags VFX shots, stunt scenes, night scenes, exterior scenes
- Generates a complete **shot list** per scene
- Suggests a day-by-day **shoot schedule**
- Writes department notes for costume, sound, camera, production design
- Exports a **production-ready PDF breakdown**

### 🎨 Color Grade Match + Cinematic Looks
Upload a reference image — a film still, photograph, anything:

- AI analyzes the exact color palette (shadow / midtone / highlight zones)
- Generates ready-to-use **FFmpeg filter chain** — copy, paste, done
- Generates **DaVinci Resolve** lift / gamma / gain values
- Includes **cinematic look presets** with thumbnails:
  - 🟡 Hollywood Warm
  - 🔵 Teal & Orange
  - 🌑 Bleach Bypass
  - 🟣 Cyberpunk Neon
  - ❄️ Sci-Fi Cold
  - 🔴 Mad Max Saturated
  - 🎞 Vintage Film
  - 🌙 Nordic Noir
  - and more

---

## Stack

| Layer | Tech |
|---|---|
| Backend | Python · FastAPI · Uvicorn |
| AI | Groq API · Llama 3.3 70B Versatile (free tier) |
| Transcription | OpenAI Whisper (runs fully local) |
| Frame Analysis | OpenCV · NumPy |
| Video Processing | FFmpeg |
| PDF Generation | ReportLab |
| Script Parsing | PyMuPDF · pdfplumber |
| Frontend | Vanilla HTML / CSS / JS — no framework, no build step |

---

## Getting Started

### Requirements
- Python 3.10+
- FFmpeg installed and in PATH
- Free Groq API key — [console.groq.com](https://console.groq.com)

### Install & Run

**Linux / Mac**
```bash
git clone https://github.com/aditya8975/InterDirecto
cd InterDirecto/setai
bash run.sh
```

**Windows**
```bash
git clone https://github.com/aditya8975/InterDirecto
cd InterDirecto\setai
run.bat
```

Then open `frontend/index.html` in your browser, paste your Groq API key in Settings, and start.

---

## Cost

| Component | Cost |
|---|---|
| Whisper transcription | $0 — runs on your CPU |
| Groq AI (Llama 3.3 70B) | $0 — free tier |
| FFmpeg processing | $0 |
| Hosting | $0 — runs on your machine |
| **Total** | **$0** |

---

## Project Structure

```
InterDirecto/
├── setai/
│   ├── backend/
│   │   ├── main.py          # FastAPI app — all three modules
│   │   └── requirements.txt
│   ├── frontend/
│   │   └── index.html       # Complete UI — single file, no build
│   ├── run.sh               # Linux/Mac start
│   └── run.bat              # Windows start
└── README.md
```

---

## Who This Is For

- Indie filmmakers working with skeleton crews
- Film school students who can't afford StudioBinder, ScriptE, or a colorist
- YouTube / short film creators who want professional-grade tools without subscriptions
- Anyone shooting a film without a full production office behind them

---

## Roadmap

- [ ] OpenCV real frame analysis per take (focus, exposure, stability scores)
- [ ] Dialogue accuracy checker — line vs. script comparison
- [ ] ADR candidate flagging from audio analysis
- [ ] Shot-to-shot color matching across shoot days
- [ ] Scene intelligence dashboard — emotion arc, pacing, coverage map
- [ ] Project memory — one setup, every module remembers your production

---

## Contributing

Pull requests welcome. If you're a filmmaker who uses this — open an issue describing what you actually needed on set that this didn't do. That feedback shapes the roadmap.

---

## License

MIT — free to use, modify, and distribute.

---

*Built by a filmmaker, for filmmakers. No VC funding. No subscriptions. No cloud lock-in.*
