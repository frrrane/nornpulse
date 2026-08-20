# ⚡ NornPulse: Autonomous Media Engine

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Norn Labs](https://img.shields.io/badge/By-Norn%20Labs-blueviolet)](https://nornlabs.ai)
[![Gemini](https://img.shields.io/badge/Powered%20By-Gemini%202.0%20Flash-orange)](https://ai.google.dev/)
[![ClickHouse](https://img.shields.io/badge/Database-ClickHouse-yellow)](https://clickhouse.com/)

**NornPulse** is an autonomous media intelligence and video generation engine developed for **[Norn Labs](https://nornlabs.ai)**. It transforms 16:9 widescreen videos into viral, high-retention 9:16 vertical shorts (for TikTok, YouTube Shorts, and Instagram Reels) by orchestrating historical retention telemetry with real-time multimodal AI reasoning.

---

## 🌌 The Three Norns Architecture

In Norse mythology, the three Norns weave the threads of fate at the Well of Urðr. In NornPulse, each Norn governs a critical stage of the autonomous media pipeline:

```text
                               16:9 Source Video
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ ᚢ Urðr (The Past) ─── ClickHouse Retention Analytics                        │
│ Queries historical drop-off curves, 3s hold rates, and viral hook taxonomies│
└─────────────────────────────────────┬───────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ ᚹ Verðandi (The Present) ─── Gemini 2.0 Flash Orchestrator                  │
│ Analyzes transcripts, grounds decisions in Urðr's telemetry, & outputs clips│
└─────────────────────────────────────┬───────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ ᛋ Skuld (The Future) ─── FFmpeg 9:16 Manifestation                          │
│ Slices timestamps and renders 1080x1920 vertical shorts with smart crop     │
└─────────────────────────────────────┬───────────────────────────────────────┘
                                      │
                                      ▼
                         ⚡ High-Converting 9:16 Short
```

### 1. `agent/urdr_analytics.py` (ᚢ Urðr — The Past)
- Talks to **ClickHouse** exclusively through the official **ClickHouse MCP server** (`mcp-clickhouse`), bridged via `agent/clickhouse_mcp_client.py` — no direct DB client library in the runtime path, per the Agentic Cinema ClickHouse track requirement.
- Manages `video_hook_retention` and historical engagement telemetry.
- Calculates retention decay curves across hook types (`shock_stat`, `curiosity_gap`, `contrarian_claim`, `problem_agitation`, etc.).
- Supplies real-time statistical priors to Gemini 2.0 Flash.

### 2. `agent/verdandi_orchestrator.py` (ᚹ Verðandi — The Present)
- Powered by the **Google GenAI SDK** using **Gemini 2.0 Flash** (`gemini-2.0-flash`).
- Ingests timestamped video transcripts and retrieves Urðr's historical benchmarks.
- Decides optimal start/end timestamps, hook titles, predicted 3s hold rates, completion rates, and social copy.

### 3. `agent/skuld_renderer.py` (ᛋ Skuld — The Future)
- Slices source video with millisecond accuracy using **FFmpeg**.
- Converts 16:9 horizontal video into 1080x1920 9:16 vertical format.
- Offers multiple crop modes:
  - **High-Res Center Crop**: Focused speaker extraction.
  - **Blurred Background Canvas**: Full 16:9 video centered over a stylized, blurred vertical background.
- Generates preview thumbnails and downloadable MP4 shorts.
- Mixes in Bragi's composed score (below), ducked under the original audio.

### 4. `agent/bragi_composer.py` (🎵 Bragi — Music)
- Composes an original instrumental background score per clip via **Google Lyria 3** (`lyria-3-clip-preview`).
- Genre/mood/bpm/energy are grounded in `music_virality_benchmarks` — Urðr's ClickHouse table correlating musical attributes with global YouTube Shorts virality per hook type, so the score isn't a random pick but the highest-virality combination on record for that hook type.
- Caches composed tracks on disk by (genre, mood, bpm), so repeated hook types reuse a track instead of paying for a fresh Lyria call every time.

---

## 📁 Repository Structure

```text
nornpulse/
├── agent/
│   ├── __init__.py
│   ├── urdr_analytics.py        # ᚢ Urðr: ClickHouse hook retention intelligence
│   ├── clickhouse_mcp_client.py # Bridge to the official ClickHouse MCP server (mcp-clickhouse)
│   ├── verdandi_orchestrator.py # ᚹ Verðandi: Gemini 2.0 Flash transcript reasoning
│   ├── skuld_renderer.py       # ᛋ Skuld: FFmpeg 16:9 -> 9:16 vertical short renderer
│   └── bragi_composer.py       # 🎵 Bragi: Lyria 3 original scores, grounded in Urðr's music benchmarks
├── utils/
│   ├── __init__.py
│   └── sample_generator.py      # Synthetic 16:9 video and sample transcripts
├── app.py                       # ⚡ Streamlit frontend dashboard
├── docker-compose.yml           # ClickHouse container (ports 8123, 9000)
├── requirements.txt             # Python dependencies
├── .env.example                 # Environment variables template
├── LICENSE                      # MIT License
└── README.md                    # Project documentation
```

---

## 🚀 Quickstart Guide

### 1. Start ClickHouse via Docker Compose
Launch the ClickHouse instance on ports `8123` (HTTP) and `9000` (TCP):
```bash
docker compose up -d
```

Verify ClickHouse is running:
```bash
curl http://localhost:8123/ping
# Output: Ok.
```

### 2. Configure Environment Variables
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Add your **Google Gemini API Key** to `.env`:
```env
GEMINI_API_KEY=your_gemini_api_key_here
```

### 3. Install Dependencies
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```
This also installs the `mcp-clickhouse` CLI into the venv — Urðr launches it as a stdio subprocess on every ClickHouse call, so it must be importable/on `PATH` (activating the venv is enough; no separate install step needed).

### 4. Launch the Dashboard
```bash
streamlit run app.py
```
Open [http://localhost:8501](http://localhost:8501) in your browser.

---

## ⚡ Features & Workflow

1. **Autonomous 1-Click Generation**: Select or generate a synthetic 16:9 test video, pick a sample transcript, and hit **"⚡ Unleash The Norns"**.
2. **Urðr Analytics Hub**: Interactive ClickHouse charts showing 3s drop-off benchmarks, duration sweet spots, and an interactive SQL query console.
3. **Verðandi AI Playground**: Inspect raw Gemini 2.0 Flash responses and structured output schemas.
4. **Skuld Video Studio**: Standalone video trimmer with center crop and blurred background rendering modes.

---

## 📜 License

MIT License. Copyright (c) 2026 **Norn Labs** ([nornlabs.ai](https://nornlabs.ai)).
