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
- Powered by the **Google GenAI SDK** using **Gemini** (`gemini-3.6-flash`).
- Ingests timestamped video transcripts and retrieves Urðr's historical benchmarks.
- The source video is always uploaded and attached too, transcript or not — Verðandi weighs the actual vocal delivery/energy it observes, not just transcript word content, when a hook_type implies a particular tone (a punchy `shock_stat` pick needs a delivery that actually lands as punchy).
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

### 5. `agent/heimdall_visualizer.py` (👁️ Heimdall — Sight)
- Composes an original 9:16 cover thumbnail per clip via Gemini's native image generation (`gemini-3-pro-image`).
- Grounded in the same Urðr `music_virality_benchmarks` row Bragi composes its score from — the mood/genre/energy that suits a hook_type acoustically is the same signal that should drive its visual mood.
- Unlike Bragi's tracks, never cached — each thumbnail is grounded in that specific clip's hook title, so there's no meaningful reuse across clips.
- If the connected YouTube channel is phone-verified, the thumbnail is set as the video's custom cover automatically on publish.

### 6. `agent/mimir_narrator.py` (🗣️ Mímir — Voice)
- Generates an AI voiceover via Gemini's native TTS (`gemini-3.1-flash-tts-preview`), mixed into the clip under Bragi's score.
- Two triggers: **fill silence** (vision-mode clips with no dialogue narrate their own hook line) and **enhance** (a transcript exists, but the clip's sliced audio measured too quiet to reliably follow — see `skuld_renderer.measure_audio_mean_volume` — so the actual transcript text for that window gets read back clearly instead).
- Voice selection is grounded in the same `music_virality_benchmarks` row's `energy_level` Bragi and Heimdall already use.

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
│   ├── bragi_composer.py       # 🎵 Bragi: Lyria 3 original scores, grounded in Urðr's music benchmarks
│   ├── heimdall_visualizer.py  # 👁️ Heimdall: Gemini-generated 9:16 cover thumbnails, same grounding as Bragi
│   └── mimir_narrator.py       # 🗣️ Mímir: Gemini TTS narration — fill-silence or unintelligible-audio fallback
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
4. **Manual Cut Range**: Optionally restrict generation to a portion of the source video via a range slider — Verðandi only sees (and can only render from) that window, enforced both in the prompt and as a hard code-level clamp. Pair with **Cut Energy** to bias clip length toward the calm/long or snappy/short end of the duration range.
5. **Long-Video Auto-Window**: Sources longer than 10 minutes with no manual Cut Range set get one bounded window auto-selected instead of Verðandi reasoning over the entire runtime in a single call — toggle **Random** (fresh offset each run) or **From Start**.
6. **Batch Mode**: Point it at a YouTube channel or playlist URL instead of a single video — runs the full pipeline once per video (capped at 3), then ranks every resulting clip by predicted virality score in the Review & Publish column.
7. **Caption Translation**: Optionally burn in captions translated into a different language than the source (e.g. a Turkish-language drama captioned in English for a Shorts audience). Translation happens line-by-line, preserving each line's original `[MM:SS]` timestamp exactly — only the words change, so kinetic caption timing is unaffected. Verðandi's own reasoning and Mímir's enhance-narration fallback both still use the original-language transcript; only the on-screen text is translated.

---

## 📜 License

MIT License. Copyright (c) 2026 **Norn Labs** ([nornlabs.ai](https://nornlabs.ai)).
