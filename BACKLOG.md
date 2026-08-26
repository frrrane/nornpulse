# NornPulse backlog

Ordered roughly by leverage, not by size. Deadline for the Devpost
submission is **Wed 9 September 2026, 2pm PDT**.

---

## Hard requirements (cannot ship without)

- [ ] **Demo video, ≤3 minutes.** The single most compressible-looking item
  that is actually the least compressible: a good three minutes takes about
  two days once re-takes are counted. Protect its slot ahead of any feature.
- [ ] **Written description** for the Devpost entry.

## Next

- [ ] **Upload cadence.** Six uploads/day maximum (1,600 quota units each
  against 10,000/day). Views need days to mature, so elapsed time is the
  binding constraint — every day without publishing is outcome data that will
  not exist by the deadline. `publish_file.py` handles externally-made video;
  `scripts/stage_for_review.py` → `check_approvals.py` handles pipeline-generated clips.

## Engagement ideas, proposed and parked

These came out of reviewing real rejected clips. Ordered by effect per unit
of work, judged against what the clips actually looked like.

- [ ] **Weave generated footage into cut clips.** Both halves already exist
  and have never been connected: `agent/footage.py` generates Veo clips,
  `agent/skuld_renderer.py` cuts and composites source video. Three forms,
  cheapest first:

  1. *Generated cold-open*, one or two seconds before the cut begins — the
     visual equivalent of the hook banner. One Veo call per clip, lands on
     the first second, which is the second that decides retention.
  2. *Generated B-roll under narration*, where the transcript says
     something the source does not show. This is the one that earns its
     keep: the NASA source has long stretches where the audio is more
     interesting than the picture. Needs the model to identify which
     moments lack visual support, which is real reasoning rather than a
     wiring job.
  3. *Generated backdrop instead of blur*, compositing the source over a
     themed generated background rather than a blurred copy of itself.

  Every insert is a paid Veo call on a clip that currently costs nothing
  beyond rendering, so at six uploads a day this is a real line item.

- [ ] **A critic agent, and an audience agent.** Two paired-review ideas,
  and they are not the same one. The pipeline already has three
  check-before-spending gates — the rights `watchdog`, `brief_warnings` and
  the provenance layer — so this is that shape applied to quality.

  A **critic** sits between the brief and generation, arguing with the brief
  in text while that is still nearly free. Every rejection so far has cost a
  full paid generation. What would make it work rather than rubber-stamp:
  show it the actual rejection history (six clips, with reasons — "not
  funny", "title cropped", "too bouncy", "completely broken"), make it name
  the specific thing that will make a viewer scroll rather than emit a score
  out of ten, and give it a verdict it can lose — PASS / REVISE / BLOCK,
  defaulting to REVISE, with one revision loop.

  An **audience** agent is the complement and probably the more interesting
  of the two: not "is this well made" but "would I keep watching". It should
  see the finished artefact rather than the brief — frames and captions, in
  order — and answer where it would have scrolled and why. That is a
  different question from the critic's, and it is the question the channel
  actually lives or dies on.

  The honest ceiling on both: a model critiquing a model shares its blind
  spots. A critic would have caught the held-pose ending and the mismatched
  title, because both are visible in the text. Neither would have caught
  "not funny at all", because the same taste wrote the joke. Craft defects,
  not taste — which is still most of what has been rejected.

- [ ] **Tags are weak, and in four distinct ways.** The clip published as
  `ncSGySusHUg` went out with:

  ```
  "moon's harshest environment", nasa, thermal swing, lunar,
  south, pole, experiences, extreme, temperatures, builds, landing, Shorts
  ```

  1. *Phrases split inconsistently.* `thermal swing` survived as a pair
     while `south` and `pole` were emitted separately — the one term a
     searcher would actually type is the one that got broken up.
  2. *Generic verbs and adjectives ranked as tags.* `experiences`,
     `builds`, `landing`, `extreme`. They describe nothing and match
     nothing; `_is_usable` filters length and stopwords but not
     part-of-speech.
  3. *Nothing validated.* Every tag came back `model` provenance, "not
     present in the current trending" — the Shorts snapshot is comedy and
     entertainment, so a space clip can never find a match in it. The
     validation step is real and simply cannot fire for this channel's
     subject matter, which is worth saying out loud rather than leaving as
     an unexplained absence of MEASURED tags.
  4. *The older uploads are worse.* The three nornpulse videos published
     before `tag_selector.py` existed all carry the identical set
     `AI, NornPulse, Shorts, Tech`, none of which describes a white hole, a
     dark-energy star or a mediocre star. Fixable in Studio.

  The interesting half is (3): tag validation needs a corpus that covers
  the channel's subject, which the current trending ingest cannot supply.
  Either ingest per-topic Shorts alongside the general chart, or accept
  that tags on a niche channel are model judgement and label them plainly
  as such.

- [ ] **Punchier titles.** "NASA's Plan For A Permanent Moon Base" is
  descriptive, not curious. The channel's own best-performing hook types are
  curiosity_gap and shock_stat; the title is written as if neither applied.

- [ ] **Word-level caption timing.** Captions currently follow transcript
  cues, which is a sentence-level rhythm. Word-level pop timing reads as
  considerably more energetic and is what the format's conventions expect.

## Housekeeping

## Known problems worth fixing

- [ ] **DEMO_SCRIPT.md and demo_beats.py have drifted.** The prose script
  predates the scoreboard and the trend loop; the machine-readable beats
  include the scoreboard. They are supposed to say the same thing, and the
  capture is driven by the latter.

- [ ] **SlopTokDaily's existing tags are malformed.** 36 of 57 tag entries
  across the 37 published videos are a single space- or newline-separated
  string — YouTube splits tags on commas, so `#DoomScroll #InfiniteScroll
  #LowEffort …` is one 99-character tag that matches nothing. New uploads are
  fixed by `agent/tag_selector.py`; the existing 37 would need editing in
  Studio to recover.

- [ ] **Emoji in the banner and captions.** Neither libass nor ffmpeg's
  drawtext can render colour emoji — libass cannot read CBDT tables, and
  drawtext draws a hollow box. `shortsmith.hook_text` strips them for that
  reason, and a reviewer has since asked for them in the hook banner too.

  Doing it properly is a compositing job rather than a parameter: detect
  the emoji in a title, resolve each to a glyph image, measure where the
  drawn text leaves a gap for it, and overlay the images at the right
  position and scale — then keep that alignment correct when the banner
  wraps or the type shrinks to fit. Worth doing, but it is a real piece of
  work and should not be attempted as a quick win.

- [ ] **Scheduled `sync_stats.py`.** Currently manual. Forecasts cannot be
  graded without it running regularly.

- [ ] **`visual_style_benchmarks` and `music_virality_benchmarks` are still
  seeded priors**, not measured. The public dataset has no visual or audio
  features, so there is nothing to ground them against — which the provenance
  layer already reports honestly. Worth stating rather than fixing.

## Deferred (deliberately)

- **`nornlabs.ai/nornpulse` as a path.** Needs an external HTTPS load balancer
  with a serverless NEG plus `--server.baseUrlPath`, roughly $18/month and a
  class of routing bugs. `nornpulse.nornlabs.ai` already works and costs
  nothing. Revisit after the deadline.

- **Full Google OAuth verification.** Takes weeks. The consent screen is
  published unverified, which is sufficient.

## Done

- [x] Repo clean-up. Removed four dead root scripts: `test_pipeline.py` and
      `daemon.py` could not import at all, `approve_and_publish.py` was a
      one-off superseded by `check_approvals.py`, and `probe_clickhouse_mcp.py`
      was a throwaway written to discover the MCP response shape before
      UrdrAnalytics could parse it. The two working manual runners moved to
      `scripts/`, so nothing outside `tests/` looks like a pytest file — which
      is how a root-level `test_pipeline.py` sat broken for weeks without
      anything noticing.

- [x] First clip published through the full pipeline —
      `youtube.com/shorts/ncSGySusHUg`, public, forecast attached and
      gradeable from ~28 Aug. The calibration clock is running.
- [x] Pitch repositioned around the ClickHouse partner track, which is the
      track the entry is judged in. Eight tables and four layers stated
      directly under the pitch.
- [x] Cold start removed — `--min-instances=1`, measured at 0.15s against
      the ~62s it was.
- [x] Display typefaces bundled from Google Fonts (Anton, Archivo Black,
      Bebas Neue, Oswald Bold), preferred over system faces so a render
      looks the same locally and in the container.
- [x] Vertex AI routing, so Google Cloud credit can pay for model calls
      that AI Studio's separate prepay wallet cannot.
- [x] Owner-only YouTube Analytics — average view duration and the
      retention curve, the numbers that say *why* rather than *how many*.
- [x] Source segments grounded in YouTube's own most-replayed graph.
- [x] Query guardrails on agent-written SQL, failing loudly rather than
      truncating silently.
- [x] Secret Manager for the four real secrets.

- [x] Grounded tags, validated against the live trending snapshot
- [x] Channels as first-class objects, with per-channel OAuth tokens
- [x] Existing channel history ingestion (`channel_video_history`)
- [x] Per-channel calibrated reach forecast
- [x] `publish_file.py` for externally-produced video
- [x] Pre-flight rights check (`agent/watchdog.py`) — blocks named and
      unmistakably-depicted third-party property before generation and
      before publish; states what it does not cover
- [x] Trend-driven generation (`agent/trend_loop.py`, `agent/footage.py`,
      `trend_publish.py`) — planning is free and default; generation is
      behind an explicit flag because Veo bills per second
- [x] Forecast calibration scoreboard (`agent/scoreboard.py`) — currently
      reports 0 of 13 gradeable, which is the correct answer until
      forecasts age past the 3-day floor
- [x] `nornlabs.ai` rebuilt, privacy policy published
- [x] OAuth consent screen published — refresh tokens no longer expire weekly
