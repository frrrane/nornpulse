# NornPulse — Devpost written description

Draft for the submission form. Every number here was read from the live
warehouse on 26 Aug 2026; re-check before submitting, because several move.

---

## Inspiration

Add captions to a video on a 100K–1M subscriber channel and it earns **+34%
reach**. Add them to a video on a 0–100 subscriber channel and it *loses* **4%**.

Same decision. Opposite answer. Both measured inside their own size band,
across 4,557,605,031 real YouTube videos.

That is not an edge case. Almost every piece of short-form advice in
circulation — post at the weekend, always caption, open with a shock stat —
was derived from channels that already have an audience, then sold to channels
that do not. Applied at the wrong scale some of it is not merely weaker: it
reverses, because the mechanism that made it work (a subscriber base feeding
browse traffic) does not exist yet.

We wanted an agent that makes short films for the filmmaker who doesn't have a
studio's back catalogue of audience data behind them — the solo creator who is
simultaneously the filmmaker, the crew and the fan — and can say *why* it made
each choice, and can tell the difference between a thing it measured and a
thing it assumed.

## What it does

NornPulse is a filmmaker's agent: given a topic or a source video, it writes
the premise, generates or cuts the footage, and publishes a short film — and
it grounds every creative decision in the size band of the channel actually
publishing it. The two real channels behind the numbers on this page have 2
and 14 subscribers, not 100,000; nothing here assumes a studio audience.

Given nothing but a channel, it will:

1. **Ask what is travelling.** Urðr queries ClickHouse for trending topics and
   returns one with a denominator attached — *"unboxing: 3 trending videos,
   median 9,689,063 views"* — not a hunch.
2. **Write the brief.** Verðandi drafts three candidate premises, picks one,
   and records why it beat the others. Each is structured as three beats in
   eight seconds: setup, turn, escalation.
3. **Check the rights before generating.** A deterministic pass over the
   title, caption and tags for named people, franchises, brands and quoted
   lyrics. It also states what it did *not* check — whether the generated
   footage resembles a protected work, and whether any use would qualify as
   fair use.
4. **Generate and finish.** Veo produces the footage, Mímir narrates it, and
   the hook is burned into the first three seconds and cleared before the
   punchline.
5. **Forecast before publishing.** A calibrated view forecast is written down
   *first*, so it can be wrong in public rather than adjusted afterwards.
6. **Stop at a human.** Every clip is emailed for approval. Nothing publishes
   itself.
7. **Grade itself later.** Real statistics sync back and the forecast is
   scored against what actually happened.

It also cuts vertical clips from existing long-form video, grounding the cut
in YouTube's own most-replayed graph — where real viewers of the source
actually scrubbed back to — rather than in a model's opinion about which part
is interesting.

## How we built it

**Six agents**, named for the Norns and their kin:

| Agent | Role |
|---|---|
| **Urðr** | reads history out of ClickHouse |
| **Verðandi** | decides what to make, where to cut, what the hook is |
| **Skuld** | renders vertical with FFmpeg |
| **Bragi** | scores the music |
| **Heimdall** | makes the cover |
| **Mímir** | narrates |

Gemini 3.6 Flash orchestrates, Veo 3.1 generates footage, Lyria scores, and
everything runs on Cloud Run with secrets in Secret Manager.

**ClickHouse is the argument, not the storage.** Every claim above is a query,
and without a warehouse that can scan billions of rows per read, the honest
version of this product does not exist — the alternative is what everyone else
ships: advice with no denominator. It is reached exclusively through the
official `mcp-clickhouse` MCP server, held open as a persistent stdio session
because spawning it cost ~3s per call.

Four layers: a frozen 4.56-billion-row public dataset for structural
questions; a live trending layer for what is moving today; this project's own
published outcomes; and its own forecasts, graded later.

## Challenges we ran into

**The benchmark was wrong about us, and finding out why was the best thing
that happened.** Pointed at our own channels, the size-band benchmark
predicted ~2,500 views. Our two real channels sit at medians of **13** and
**343** — individually, not pooled; the live dashboard's own "overstated by"
tile reports the pooled figure across every 0-100-subscriber channel we have
history for, which lands between these two and moves as more channels join.
The population median was roughly our best-ever video, not a typical one.

The reason: the public dataset is a *crawl*. It only contains videos that were
discoverable enough to be crawled. A channel posting into the void is not in
it — so banding by subscriber count does not remove survivorship bias, because
the population inside the band is filtered too. Forecasts are now calibrated
against each channel's own history. Uncalibrated 2,400 → calibrated 305 →
actual median 343.

**A correlation we reported twice turned out to be an artifact.** An apparent
−0.25 relationship became **+0.13** once a minimum-views floor excluded clips
with too few views for a rate to mean anything. It is documented in the code
that computes it, because the interesting part is that we published the wrong
number first.

**Two silent breakages survived a green test suite.** A parameter mismatch and
a timestamp regex that read 169 subtitle cues as zero — both from one commit,
both invisible to every test. The lesson is written into the codebase as a
recurring rule: *an instruction is not a control.* Wherever a prompt tells the
model to do something, a deterministic check verifies it did.

## What we learned

Most AI tools present everything they output with identical confidence. The
more useful thing turned out to be the opposite: labelling each decision by
what it rests on — **measured**, **assumed**, or **model judgement** — and
refusing to answer when the sample is too thin. A typical clip declares *3
measured, 4 assumed, 1 model judgement*, inline.

The scoreboard reports how many of its own forecasts are gradeable so far
rather than averaging something comforting over two clips.

## What's next

- Word-level captions — timing currently follows transcript-line cues rather
  than per-word timestamps, so the kinetic reveal stays off
  (`WORD_CHUNK_CAPTIONS = False`) until a transcription source supplies
  per-word timing honestly. Emoji already render in titles; they're still
  stripped from the burned-in banner and narration, since neither libass nor
  FFmpeg's `drawtext` can render colour emoji.
- Weaving generated B-roll under narration where the transcript says something
  the source footage does not show.
- Automating ingestion — but only once its output is being approved
  consistently in human review, not before.

## What's done since the last read

- **A critic agent.** `agent/preflight.py` checks a clip against faults a
  human reviewer has actually rejected before — not a taste judge, a
  deterministic checklist built from real rejection comments.

## Try it

**Live:** https://nornpulse.nornlabs.ai — read-only, every chart running
against the real warehouse.

**Built with:** ClickHouse · Gemini 3.6 Flash · Veo 3.1 · Lyria · Cloud Run ·
FFmpeg · Streamlit · Python

**Submitted to the ClickHouse partner track.**
