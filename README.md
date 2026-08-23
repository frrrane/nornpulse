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
- Also manages `music_virality_benchmarks` (Bragi's genre/mood/bpm grounding) and `visual_style_benchmarks` (Skuld's crop/motion/color-grade grounding) — the same hook_type taxonomy correlated with historical virality per creative dimension.
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
  - **Top-Anchored Crop**: Same blurred-canvas composition, but the sharp foreground sits in the upper two-thirds so burned-in captions never overlap the subject.
  - **Cinematic Letterbox**: Full frame fit to width with solid black bars, a moodier "film" look than the blurred canvas.
- Adds a camera motion treatment per clip — a slow Ken Burns zoom-in, an accelerating punch-in zoom, or a sinusoidal shake — plus a color grade on the actual video pixels (cool/desaturated, warm glow, or vibrant punch) distinct from the Warmth slider, which only tints captions/banner.
- crop mode, motion, and color grade are chosen per hook_type from Urðr's `visual_style_benchmarks` (see below), the same grounding principle as Bragi's music and Heimdall's thumbnails — the "sentiment" driving the edit is real ClickHouse data, not an ad hoc per-render guess.
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

Optional: `CAPTION_FONT` selects the burned-in caption typeface (default `Arial Black`). It must name a real heavy/display weight that exists on the host — libass substitutes **silently** when it doesn't, so captions quietly render at the wrong weight with nothing logged. Check a host with `fc-match "Arial Black"`. The container sets `CAPTION_FONT="Roboto Black"` for exactly this reason (see Docker below).

### 3. Install Dependencies
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```
This also installs the `mcp-clickhouse` CLI into the venv — Urðr launches it as a stdio subprocess on every ClickHouse call. It's resolved by absolute path relative to the running interpreter (see `clickhouse_mcp_client.resolve_mcp_command`), so it works whether or not the venv is activated: `venv/bin/streamlit run app.py`, a systemd unit, and a container entrypoint are all fine, not just `source venv/bin/activate`.

If ClickHouse ever *is* unreachable, the app degrades to in-memory fallback benchmarks rather than crashing — but it says so loudly, with a red banner above the tabs naming the specific cause and offering a retry. Fallback mode is never silent, because a generation run grounded in synthetic data instead of real ClickHouse history looks identical to a healthy one otherwise.

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

## 🐳 Docker / Cloud Run

```bash
docker build -t nornpulse .
docker run -p 8080:8080 --env-file .env nornpulse
```

The image is built for Cloud Run: it listens on `$PORT` (default 8080) and binds `0.0.0.0`. Three things in it are load-bearing and fail *silently* if dropped, so the build guards two of them explicitly:

- **ffmpeg/ffprobe** — Skuld shells out for every render and probe.
- **`mcp-clickhouse`** — must land in the same environment that runs the app, since Urðr launches it as a subprocess resolved relative to `sys.executable`. Missing it would degrade the app to in-memory fallback benchmarks while still looking healthy, so the build fails instead.
- **A black-weight caption font** — `Arial Black` doesn't exist on a slim Debian image, and fontconfig was measured falling all the way back to `DejaVu Sans "Book"`, i.e. *regular* weight, with nothing logged. The image installs Roboto Black, sets `CAPTION_FONT` to it, and fails the build if no black-weight face resolves.

Secrets are never baked in — `.env`, `client_secrets.json` and `.credentials/` are excluded via `.dockerignore` and `.gcloudignore`, and are supplied as mounted Secret Manager values.

Deploying also needs the runtime service account to hold `roles/secretmanager.secretAccessor` on each secret; `--set-secrets` fails without it. Grant it once per secret:

```bash
gcloud secrets add-iam-policy-binding nornpulse-gemini-api-key \
  --member="serviceAccount:PROJECT_NUMBER-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor" --project=PROJECT
```

Note that **YouTube publishing does not work in the container as-is**: it needs `client_secrets.json` plus an interactive OAuth consent flow. Generation, rendering and analytics all work; publishing is expected to happen from a local run.

## ⚡ ClickHouse MCP session

All ClickHouse access goes through the official `mcp-clickhouse` server (a hackathon track requirement). The server is held open as a **persistent stdio session** rather than respawned per call — spawning cost ~3s per query, which was tolerable at a handful of queries and became the dominant cost once the dashboard grounded itself in the global benchmark tables.

| | per query | cold Tab 3 load |
|---|---|---|
| per-call subprocess | ~3.3s | **71.0s** |
| persistent session | ~0.35s | **10.3s** |

The session restarts automatically if the subprocess dies or if any `CLICKHOUSE_*` setting changes (the subprocess reads its configuration once, at spawn, so reusing it after a change would query the old host). If it can't start, calls fall back to the per-call path — a broken optimisation shouldn't be worse than not having it. Set `NORNPULSE_MCP_PERSISTENT=0` to force the old behaviour.

Calls are serialised through one session, so a long materialisation blocks reads behind it. Streamlit reruns are sequential, so this hasn't mattered in practice.

## ✍️ Captions

Five typefaces are selectable in Advanced Settings — Roboto Black, Roboto Condensed, League Spartan, Lato Black, DejaVu Sans. Every one is installed in the image and checked at build time, because **libass substitutes silently** for a font it cannot resolve: a missing face does not error, it just renders the video in a different weight with nothing logged.

Timestamps are requested to the millisecond. Whole-second timestamps quantise every caption to the nearest second, which reads as visibly out of sync with the speech.

**Emoji cannot be burned into captions.** Noto Color Emoji is installed, but libass does not support colour bitmap fonts (CBDT/sbix) — measured on this image, an emoji in a caption renders with zero non-grey pixels. Emoji in the YouTube title and description work fine and are where the social caption already puts them; burning them into the frame would need an ffmpeg PNG overlay.

## 🧾 Decision provenance

Every clip carries a **How this was decided** panel listing each choice the pipeline made and the basis for it, at one of three levels:

- **Measured** — read from the materialised global facts, within this channel's size band, with a sample size attached. Hook, captions and the reach forecast.
- **Seeded prior** — from a hand-written benchmark table. Framing, camera motion, colour grade and score. The public dataset has no visual or audio features, so there is nothing external to measure these against, and the panel says so rather than letting them sit next to the measured figures looking equally solid.
- **Model judgement** — Verðandi's reading of this specific transcript. The cut.

A typical clip is 3 measured, 4 assumed, 1 model. That ratio is the honest state of the system, and showing it is more useful than implying everything is grounded.

## 📈 Syncing real performance

```bash
python sync_stats.py            # --dry-run to preview
```

Reads public view counts back onto `published_clip_outcomes`, closing the prediction-to-ground-truth loop. It runs **unattended**, because it authenticates with `YOUTUBE_API_KEY` rather than OAuth: this project's consent screen is in Testing, and Google expires those refresh tokens after **7 days**, so anything scheduled on the OAuth path breaks weekly. An API key does not expire. Publishing still needs OAuth — a key can read public data and cannot upload.

Schedule it with cron:

```
0 */6 * * * cd /path/to/nornpulse && venv/bin/python sync_stats.py >> sync.log 2>&1
```

Videos that are deleted, private, or never published are flagged unmeasurable rather than recorded as zero views, so they stay out of the cross-validation charts instead of counting as missed predictions.

## 🔒 Public demo mode

The Devpost submission needs a URL a judge can open, which means `--allow-unauthenticated`. `NORNPULSE_DEMO_MODE=1` (set by `deploy_cloud_run.sh`, off by default locally) closes off everything that writes or spends:

- The **SQL console is not rendered at all** — it runs user-supplied SQL with write access enabled and `remoteSecure()` available, so a visitor could write to the warehouse or push data to another host. A disabled textarea would still advertise the endpoint.
- **Link ingestion is disabled** — and could not work regardless: YouTube bot-blocks datacenter IPs, so `yt-dlp` fails from Cloud Run for both the download and the duration probe that precedes it. Uploading a file is offered instead, which skips the download entirely and lets a visitor drive the real pipeline.
- **Publish, approve/reject, delete and sync are stood down**, each saying why where it stands. Every generate button spends real Gemini, Lyria and Imagen credit with no ceiling.

Nothing about the product is hidden: every page, chart and grounded decision runs live against the real warehouse and the 4.56-billion-row dataset, and the clips on display were produced by this pipeline. A static test asserts each costly action carries a gate, so a button added later without one fails the suite.

## 🌍 Global grounding (three data layers)

NornPulse's decisions are grounded in three layers that all live in ClickHouse:

| Layer | Source | Horizon |
|---|---|---|
| `global_youtube_benchmarks` | ClickHouse's public **4.56-billion-row** YouTube dataset, via `remoteSecure` | frozen late-2021 |
| `trending_snapshots` | YouTube Data API `videos.list(chart=mostPopular)` | current |
| `published_clip_outcomes` | your own published clips | your ground truth |

```bash
python seed_global_benchmarks.py            # materialise the historical facts (~5 min)
python ingest_trending.py --regions US,GB   # snapshot what's trending now (1 quota unit/region)
```

The historical facts are **materialised, not queried live**: the public playground caps execution at 120s server-side, and a demo shouldn't break because a shared endpoint is busy. Sampling is `cityHash64(id) % N`; sample sizes are shown in the UI.

**Everything is read within a channel-size band.** This is not cosmetic. Captioned videos skew heavily toward large established channels, so an unstratified comparison measures the channel's audience rather than the effect being asked about — subtitles appear to lift median views ~15% while simultaneously showing five times *lower* views-per-subscriber. Banded, the real picture emerges: for 0-100 subscriber channels captions give **no view lift at all (-5%)** but a **+67% like rate**, while for 100k-1M channels they give **+31% views** and a comparable **+69%** like rate. Engagement lift holds at every size; reach lift only appears once a channel has an audience.

**Hook patterns from real titles.** `video_hook_retention` is seeded data; the hook taxonomy is now also measured against real English-language titles — **694,161 videos** classified into the same eight buckets, stratified by channel size.

This is the only fact that reads `title`, the one expensive column in a 4.5-billion-row table, and every whole-table approach exceeds the 120s cap (a hash-modulo sample still scans `id`; a numeric predicate then reads title for survivors — measured at 247s). What works is the sort key: the table is ordered by `uploader`, so range predicates prune on the primary index. Fourteen lettered windows are sampled and unioned, which runs in **8 seconds** and spreads the sample instead of taking one alphabetical block.

Language has to be filtered too — `detectLanguage` is disabled on the playground, and an unfiltered sample is mostly non-English, which an English pattern matcher silently files as "plain". An earlier attempt returned *zero* `contrarian_claim` rows across 2.9M videos for exactly this reason.

**Verðandi chooses from this, not from the seed rows.** The hook taxonomy handed to Gemini is re-ranked by measured global reach for the configured channel size, each entry carrying its median views, sample size and lift over an unstyled title, with the prompt stating that the order is measured. Hook types with no global figure (`visual_disruption` is not inferable from a title) stay available but are marked and ranked behind the measured ones, and a bucket under 1,000 samples can never take rank one. `key_insights` is derived rather than hardcoded — the old payload asserted "Shock Stats and Curiosity Gaps generate >92% 3-second hold rate", which the measured data contradicts.

For a 0–100 subscriber channel the best well-sampled hook is **curiosity_gap**, +15% over an unstyled title across 9,100 videos — while `shock_stat`, which the seeded benchmarks rank first, actually underperforms a plain title. Buckets under 1,000 videos are charted but never headlined, since ranking on median alone hands the top slot to whichever thin bucket got lucky.

**Unmeasurable outcomes.** A row whose video is deleted, private, or was never published carries `video_unavailable`, and is excluded from the cross-validation charts while staying in the table for the audit trail. Zero measured views and *no possible measurement* are different things: conflating them puts fabricated misses on the chart. This matters because a stale 900,000-view row for a video that isn't public once dominated this panel, while a genuinely live clip with 338 views was recorded as zero.

**Reach forecast.** Before publishing, each clip shows a p10–p50–p90 view range for a channel of your size, adjusted by the factors actually measured (captioning, upload day), with the derivation shown per factor. It is stored alongside the clip in `published_clip_outcomes`, in the same units as `actual_view_count`, so Tab 3 can plot forecast against reality on a diagonal. `predicted_virality_score` is an internal 0-100 ranking with no external referent — plotting it against view counts only ever showed whether the ordering held.

**The forecast is age-aware.** Every global median is a *lifetime* figure — the dataset observed each video once, at whatever age it happened to be — so scoring a clip published this morning against one is a category error, not a forecast miss. The dataset answers this itself: `upload_date` versus `fetch_date` gives each video's age when its views were counted, and grouping by that yields an observed growth curve. A 0–100 subscriber video reaches roughly 70% of its lifetime views after a week or two.

The first-week buckets do not exist for small channels — the 2021 crawl caught too few of them — which is exactly the age a fresh clip is. Rather than extrapolate a curve, such a clip is marked `too_early_to_judge` and held out of the cross-validation chart, with its age stated.

Read the forecast as *comparable videos got this much*, not *this clip will*. Every factor is correlational, and nothing in it looks at the clip's actual content.

**Upload day barely matters.** Across all of YouTube weekends appear ~25% better on reach-per-subscriber — but that is a channel-size artifact, since weekend uploads skew toward small hobbyist channels. Banded, the spread across all seven days is about 8% (2,068–2,231 median views for 0-100 subscriber channels). The dashboard, the forecast multiplier and the weekday chart all read the same banded median-views figure, because ranking by one metric while forecasting in another produced two contradictory claims on the same screen.

⚠️ **Scope.** The public dataset was crawled 27 Nov – 13 Dec 2021, so its view counts are frozen there and it predates mature Shorts behaviour. It has no duration column, so it cannot separate Shorts from long-form — nothing derived from it is a Shorts-specific benchmark. That is exactly what the trending layer is for: the API returns `contentDetails.duration`, so actual Shorts are identifiable, along with the tags currently in circulation.

## 🧪 Tests

```bash
pip install -r requirements-dev.txt
pytest
```

338 offline unit tests covering the pure logic — time parsing, caption chunking/timing and ASS generation, FFmpeg filter-graph construction for every crop mode / motion effect / colour grade, SQL literal escaping, ClickHouse connection diagnostics, the virality-score heuristic, Verðandi's duration/window clamp and metadata reconciliation, the HITL staging email's MIME structure and HTML escaping, the review-decision ledger, the global-grounding accessors' stratification and degradation paths, and the persistent MCP session's reuse, restart and fallback logic. They need no API keys, no ClickHouse, no FFmpeg and no SMTP connection, and run in about ten seconds.

Several cases are regression guards for bugs found by live testing: caption overlap, the crop-before-blur ordering in `blurred_background`, the `split=2` rule for named filter pads, `ORDER BY` binding to only the last `SELECT` of a `UNION ALL`, a clamp that could emit an end timestamp *before* its start when the model requested a range outside the user's Cut Range, and metadata reconciliation silently dropping every render field it didn't list by name.

`test_pipeline.py` and `test_hitl.py` at the repo root are separate — they are manual end-to-end runners that call the real Gemini / ClickHouse / Gmail APIs, and are excluded from the `pytest` suite.

## 📧 Human-in-the-loop staging

Nothing reaches YouTube without a human approving it. `test_hitl.py` runs the full pipeline and emails each rendered short for review:

```bash
python test_hitl.py [video_path] [transcript_path] [count]
# defaults: sample_data/yt_input.mp4, sample_data/raw_transcript.txt, 3
```

Each email carries the 9:16 render as an attachment, Heimdall's cover inline, and a review table of what the system decided — social caption, hook type and Urðr rank, the cut range, and the crop / motion / colour-grade treatment the benchmarks selected.

**Approve / Reject with comments.** The email has two buttons. They are `mailto:` links, so a decision is an ordinary reply whose subject carries the verdict (`[NornPulse] APPROVE clip_1`) and whose body is your comment — no public callback URL, so it works the same before and after deployment. Apply pending replies with:

```bash
python check_approvals.py --channel UCxxxx --privacy public   # --dry-run to preview
```

Approved clips upload and are logged to `published_clip_outcomes`; rejected clips are **archived to `output_clips/rejected/`, never deleted** — a render costs real API spend and a rejection comment is the most useful training signal the system produces. Published clips move to `output_clips/published/` for the same reason.

The **Review Queue & Library** tab is the durable view: every rendered clip across `output_clips/`, `rejected/` and `published/`, filtered by state, each card carrying its title, hook and Urðr rank, cut range, visual treatment, Heimdall cover, and the decision and comment recorded against it. Pending clips can be approved or rejected there. State is read from the ledger rather than from which directory a file sits in — the ledger records intent, and a failed move shouldn't rewrite history. Deletion is deliberately separate from rejection and asks twice.

The Pipeline tab's Review & Publish column has the same two buttons plus a comment box, for the clips you just generated. Both surfaces write one shared ledger (`output_clips/review_decisions.json`, mirrored best-effort into ClickHouse's `clip_review_decisions`), so a clip already published from either place is skipped rather than uploaded twice. The JSON is the source of truth deliberately: the dashboard must still show your decisions when ClickHouse is unreachable.

`upload_to_youtube_shorts` defaults to `privacy_status="private"` so nothing goes live by accident. OAuth is cached at `.credentials/youtube_token.json` and is **bound to whichever account authorized it** — delete that file when switching channels, or the upload will silently land on the old one.

## 📜 License

MIT License. Copyright (c) 2026 **Norn Labs** ([nornlabs.ai](https://nornlabs.ai)).
