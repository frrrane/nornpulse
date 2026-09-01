# NornPulse backlog

Ordered roughly by leverage, not by size. Deadline for the Devpost
submission is **Wed 9 September 2026, 2pm PDT**.

---

## Hard requirements (cannot ship without)

- [ ] **Demo video, ≤3 minutes.** Script, beats, capture and assembly all
  exist — `demo_capture.py` records the seven driveable beats, `demo_assemble.py`
  narrates and cuts, and a silent dry run comes out at 2:41. What is missing is
  the two hand-shot beats, which currently render as slates. Two shots are manual (the trend
  loop in a terminal, the approval email) and are the ones that show the
  product doing what the competition is about — film those first.
- [x] **Written description** for the Devpost entry — `DESCRIPTION.md`. Numbers
  read live on 26 Aug 2026; re-check before submitting.

## Next

- [ ] **Word-level caption timings.** Captions are one-per-line because a
  transcript line carries a start time and nothing else, so the kinetic
  word-chunk reveal had to guess each chunk's moment by character count and
  lagged the audio by about a chunk by mid-line — three clips were rejected
  for it. Ask the transcription model for per-word timestamps, then set
  `skuld_renderer.WORD_CHUNK_CAPTIONS = True` to restore the reveal honestly.

- [ ] **Scheduled staging.** `agent/norn_cron.py` exists but nothing imports it,
  and its body predates the trend loop — wiring it up means rewriting it against
  `trend_publish.py --stage`. Deliberately staging-only: a timer that fills a
  review queue is safe to be wrong, a timer that publishes is not. Kept through
  an over-engineering audit for this reason; the module now says so in its own
  docstring so it does not read as dead code.

- [ ] **Upload cadence.** Six uploads/day maximum (1,600 quota units each
  against 10,000/day). Views need days to mature, so elapsed time is the
  binding constraint — every day without publishing is outcome data that will
  not exist by the deadline. `publish_file.py` handles externally-made video;
  `scripts/stage_for_review.py` → `check_approvals.py` handles pipeline-generated clips.

## Engagement ideas, proposed and parked

These came out of reviewing real rejected clips. Ordered by effect per unit
of work, judged against what the clips actually looked like.

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


- [ ] **Word-level caption timing.** Captions currently follow transcript
  cues, which is a sentence-level rhythm. Word-level pop timing reads as
  considerably more energetic and is what the format's conventions expect.

## Housekeeping

## Known problems worth fixing

- [x] **DEMO_SCRIPT.md and demo_beats.py have drifted.** Fixed: the prose is
  now rendered from the beats and `tests/test_demo_beats.py` fails in both
  directions if they diverge again. The beats gained the generation and
  human-gate sections the prose was missing.

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
- [x] **Punchier titles.** Root cause was that neither title-writing prompt
      (`verdandi_orchestrator.py`, `trend_loop.py`) ever explained HOW a
      chosen hook_type should shape the words — a model could pick the
      correctly-grounded label and still write "NASA's Plan For A
      Permanent Moon Base". `HOOK_TITLE_GUIDANCE` in `urdr_analytics.py`
      gives concrete writing guidance per hook type, shared by both
      prompts; `trend_loop.py` also had hook_type asked for AFTER title in
      its JSON schema, so a model literally couldn't have written to a
      choice it hadn't made yet — reordered. Verified live: a real run
      produced "Hippo Swings Butterfly Net At High-Speed Flying Ball
      Explosion" for a visual_disruption hook, not a topic label.
- [x] **Weave generated footage into cut clips.** All three forms, cheapest
      first, each a paid generation call on a clip that previously cost
      nothing beyond rendering, so a real line item at six uploads a day
      (form 3's is cheaper — an image call, not video):

      1. *Generated cold-open* (`agent/weaver.py`): a crossfaded Veo opener
         in front of the cut, addressing the exact "cuts unexpectedly at
         the second second" complaint from a real past rejection. Was
         fully built and wired through
         `VerdandiOrchestrator.orchestrate_generation`/`orchestrate_batch`
         months before this line was last touched, but nothing above the
         orchestrator ever exposed a way to turn it on — now a slider in
         the Create page's Advanced Settings (default 0, off) and
         `NORNPULSE_OPENER_SEC` in `scripts/stage_for_review.py`.
      2. *Generated B-roll under narration* (`agent/weaver.py`):
         `identify_broll_moment` reasons over a clip's own transcript for
         a span the source footage genuinely can't show, and correctly
         says "nowhere" on most clips rather than forcing an insert —
         verified against the real sample_data transcript (correctly
         picked the loop-quantum-gravity passage) and against
         deliberately concrete narration (correctly found nothing).
         `insert_broll` swaps only the picture for that window via
         ffmpeg, the clip's own audio stream-copied through untouched —
         verified byte-identical via a raw PCM diff. Exposed the same
         way: a Create page checkbox, `NORNPULSE_BROLL` for the CLI.
      3. *Generated backdrop instead of blur* (`agent/skuld_renderer.py`,
         `agent/heimdall_visualizer.py`): the smallest of the three —
         reuses Heimdall's existing image-generation (built for cover
         thumbnails) rather than a new Veo call, since a backdrop needs
         no motion. `compose_backdrop` asks for atmosphere, not a
         subject, so it doesn't compete with the source footage
         composited on top of it. Falls back to `blurred_background` on
         a failed generation. Verified against real ffmpeg: extracted
         and viewed an actual composited frame, not just the
         filter-graph string. Exposed the same way: a Create page
         checkbox, `NORNPULSE_GENERATED_BACKDROP` for the CLI.
- [x] **A critic agent, and an audience agent** (`agent/critic.py`,
      `agent/audience.py`). The critic sits between the brief and
      generation, checked against real, live-read rejection history (19
      comments as of today), PASS/REVISE/BLOCK defaulting to REVISE, one
      real revision attempt before handing back. The audience agent
      watches the finished clip's own sampled frames and caption timeline
      and says where it would scroll and why — advisory, wired into both
      staging paths, never auto-rejecting. Both verified against real
      rejected and published clips, not just unit tests.
- [x] One more UI overhaul, sequenced before demo capture. Hero graphic,
      sidebar mark sizing/linking, enlarged clip cards, Material Symbols
      replacing emoji, natural-language chart/table labels, Norse-name-first
      labeling, ClickHouse fallbacks for Home/Review, and — the one piece
      that was actually still missing on a direct check — click-to-inspect
      on the Intelligence page's charts.
- [x] Owner-only YouTube Analytics — average view duration and the
      retention curve, the numbers that say *why* rather than *how many*.
      The module existed with zero callers until `sync_retention.py`
      actually wired it up and started writing real per-clip data.
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
