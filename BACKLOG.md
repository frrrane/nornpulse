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

- **Agentic video processing** (Google's 2 Sep 2026 announcement — up to 88%
  fewer tokens, 66% lower cost on video calls). Checked live rather than
  from the announcement's own sample code, per this project's established
  rule that only a real call settles model/platform availability. It is a
  different API surface (`client.interactions.create`, not the
  `models.generate_content` this codebase uses) and its video input needs
  either a Files-API `uri` or inline base64 `data` — the Files API path is
  out regardless, `genai.Client.files.upload` raises "This method is only
  supported in the Gemini Developer client" on the Vertex route this
  project bills through. Inline base64 `data` with `processing: "agentic"`
  got past request validation but was rejected with "Unsupported model
  interaction" on Vertex for both `gemini-3.6-flash` and the announcement's
  own `gemini-3.7-flash` — a clean 400, before any billing check. The same
  call against AI Studio got past that same validation and failed only on
  this project's already-depleted AI Studio prepay wallet (the reason
  billing moved to Vertex in the first place), which reads as the feature
  being real and live there. Conclusion: not adoptable on this project's
  billing path today. Revisit once Vertex serves `interactions.create`, or
  if AI Studio credits are ever topped back up.

## Done

- [x] **Grounded crazy/warmth, banner/caption font, and emphasis-word
      selection per hook_type.** From reviewing real rejection history
      (`critic.rejection_history()`, 20 entries): "too bouncy" appeared
      twice while `crazy` sat at one flat `0.3` for every clip regardless
      of content, and "nicer font"/"more engaging font" appeared twice
      while the banner font was not even configurable — `render_vertical_short`
      had no `banner_font` parameter at all until this, so every clip used
      whichever `DISPLAY_FACE` `text_fit.font_file` tried first.

      `skuld_renderer.caption_style_from_visual(motion_effect, color_grade)`
      derives (crazy, warmth) from the SAME per-hook-type visual benchmark
      row that already grounds `crop_mode`/`motion_effect`/`color_grade`
      (`get_top_visual_benchmark`) — the signal driving how energetic the
      camera motion is and how warm the color grade is now also drives the
      caption reveal, instead of an independently-guessed style sitting
      next to it. `HOOK_TYPE_BANNER_FONT`/`HOOK_TYPE_CAPTION_FONT` pair
      each of the 8 `HOOK_TITLE_GUIDANCE` hook types with a face, labelled
      honestly as an editorial judgement call rather than a benchmark
      lookup, since — unlike the visual treatment — there is no existing
      data to ground a font pairing in.

      Wired into `verdandi_orchestrator.py`'s render tool: `warmth`/`crazy`
      on `orchestrate_generation`/`orchestrate_batch` changed from fixed
      `float` defaults to `Optional[float] = None`, resolved per-clip from
      the hook_type unless a caller pins an explicit value — what the
      Create page's sliders already do, so manual renders are unaffected;
      only the automated path (which never set either) gains grounding.
      Verified against real renders: a real `render_vertical_short` call
      with an explicit `banner_font`, frame extracted and viewed, showing
      a visibly different typeface from the default.

      Also improved emphasis-word selection (`_highlight_emphasis_word`),
      not itself named in any real rejection but requested alongside the
      above: it picked the single longest word in a caption chunk, which
      meant a number ("93%" strips to "93", two characters) never cleared
      the four-letter floor and could never be the highlight — exactly
      backwards for a shock_stat-style caption, where the number IS the
      hook. Now prioritises a digit-carrying word, then a deliberately
      shouted (ALL-CAPS, len > 1) word, and only then falls back to the
      original longest-word rule.

- [x] **Emoji in kinetic captions — checked properly rather than built.**
      A caption's text comes from transcribing spoken audio, and nobody
      speaks an emoji: confirmed against real transcript fixtures
      (`sample_data/transcripts/*.txt`) that neither carries a single
      character in the emoji range. The word-chunk-synced overlay this
      would need (harder than either hook banner — placed once statically
      — since a chunk's own reveal timing would have to carry the glyph
      too) isn't worth building for a case that doesn't occur. Added the
      cheap defensive floor instead:
      `generate_rebased_ass_subtitle_file` now runs `text_fit.strip_emoji`
      on a caption's cleaned text before it reaches libass, so the
      hypothetical (a hand-edited transcript override, a future
      transcription-model quirk) degrades to a dropped character rather
      than the hollow box libass would otherwise draw.

- [x] **Emoji in Skuld's cut-clip banner.** Same compositing approach as
      shortsmith's hook, extended into a real multi-input filter graph
      instead of a standalone ffmpeg pass. The layout math (centre a text
      line and a trailing emoji together as one group, drop the emoji
      rather than overflow the frame) was pulled out of shortsmith into
      `agent.text_fit.place_trailing_emoji` so both renderers share it —
      "the measuring belongs in one place" is literally this module's own
      stated purpose.

      `_build_banner_filter` now returns `(filter_fragment, emoji_png,
      emoji_pos)` instead of a bare string; `render_vertical_short`
      reserves the emoji's ffmpeg input index the same way
      `generated_backdrop` already reserves one for its own image input
      (`banner_emoji_idx`, computed before `_build_banner_filter` runs so
      the overlay node can reference it, then folded into the existing
      `next_input_idx` bookkeeping narration/music already used) and
      overlays it onto `[scaled]` after the edge fade rather than
      threading a second output label through the crop/motion/colour/
      caption chain above it — the one deliberate corner cut here: the
      emoji doesn't share the ~0.35s edge fade with the rest of the frame,
      same spirit as shortsmith's hard-cut-instead-of-alpha-fade.

      Verified against real renders on real source footage, not just the
      command string: a real `render_vertical_short` call with an emoji
      banner, extracted and viewed the composited frame; the harder
      emoji+narration+music combination (stresses the input-index
      bookkeeping hardest); and the no-emoji path unchanged.

- [x] **Emoji in shortsmith's generated-clip hook.** A trailing decorative
      emoji run (the shape titles in this pipeline actually use — a model
      appends emoji, it does not scatter them mid-sentence) is now
      composited as a real colour image instead of being dropped.
      `agent.text_fit` gained `split_trailing_emoji` (splits the run off
      before length truncation, so it survives instead of getting cut
      along with the words around it) and `emoji_glyph` (renders it via
      Pillow's `embedded_color=True`). The font source needed no bundling:
      the Dockerfile already apt-installs `fonts-noto-color-emoji`, with a
      comment that had already anticipated exactly this PNG-overlay
      approach; a workstation ChromeOS path covers local dev. Both
      candidate paths were verified against the real files, not assumed —
      the container's exact apt-installed font was pulled out of the real
      base image (`python:3.11-slim-bookworm`) and Pillow rendered real
      coloured pixels from it before this shipped. One real constraint
      surfaced by that verification: NotoColorEmoji ships exactly one
      embedded bitmap strike (109px) and FreeType refuses every other
      size, so a glyph always renders at that fixed size first and gets
      resized to whatever the caller needs.

      `shortsmith.finish()` composites the glyph beside the hook's last
      line via ffmpeg's `overlay`, centred with it as one group rather
      than the text re-centred alone with the emoji bolted onto whatever
      room is left, and drops the emoji rather than compositing it if the
      combined width would overflow the frame — same "worst case is the
      bare clip" degrade as everywhere else in this function. The
      no-emoji path (still the common case) is untouched: the new
      filter_complex-based overlay path only engages when there is
      actually a glyph to composite, so the already-verified plain-`-vf`
      path never changes shape. Verified against real ffmpeg renders, not
      just the command string — extracted and viewed real frames for the
      hook-only path, the harder narration+audio-mix+overlay combined
      path (real audio and video streams confirmed via ffprobe), and the
      post-hold frame (both text and emoji correctly cleared together).

      Not yet done: Skuld's cut-clip banner and kinetic captions — see
      Known problems, above, for why those are a separate, bigger piece.

- [x] **Word-level caption timings.** Root cause was that a transcript line
      carried a start time and nothing else, so the kinetic word-chunk
      reveal had to guess each chunk's moment by character count across a
      window running to the next line's start — and lagged the audio by
      about a chunk by mid-line. Three clips were rejected for it.
      `utils/transcribe.py`'s prompt now asks for a timestamp before every
      word, not just each line's first (`_shift_timestamps`' chunk-offset
      rewrite needed no change — it's a global regex substitution, so it
      already covers however many timestamps a line carries).
      `skuld_renderer.py` gained `_line_word_times` (detects a line with
      one real timestamp per word, distinguished from the legacy two-marker
      "[start] ... [explicit end]" shape) and
      `_distribute_chunk_times_from_words` (chunk boundaries from those
      real times instead of the character-count guess), and
      `WORD_CHUNK_CAPTIONS` is now `True`. A line that doesn't carry real
      per-word marks — an already-cached transcript from before this
      change, or a response that skipped the instruction — falls back to
      one un-split caption for its whole window rather than the
      discredited guess, so old and new transcripts can coexist safely.
      Also fixed a latent bug the per-word format would have tripped:
      `explicit_end` used to trigger on `len(times) >= 2`, which a
      many-timestamps-per-line transcript would have satisfied on its
      *second word's* time; narrowed to `== 2` so only the legacy shape
      hits it. Verified against a real cached transcript (13 lines, still
      one caption each, no drift) and a realistic per-word transcript
      (chunk boundaries landed exactly on each word's real timestamp,
      widened only where the 0.28s floor required it).

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
- [x] **Quality signals now actually reach the dashboard.** A product
      audit found that every check this project produces — preflight's
      rejection-history checklist, the audience reaction, owner-measured
      retention, even the older `opening_problem` flag despite its own
      comment claiming otherwise — was print-only or email-only, never
      reaching the clip's own metadata or `page_review()`, the actual
      persistent surface a reviewer uses. Fixed: `preflight.check_clip`
      now runs unconditionally inside `VerdandiOrchestrator` itself
      (free, so no reason to gate it) instead of only in
      `stage_for_review.py`, and its findings plus `opening_problem` and
      `audience_reaction` are written onto the clip record, so both real
      callers (Create page, the CLI script) pick them up automatically.
      `page_review()` gained three severity-ordered lines on its
      existing extras pattern. Also closed a related gap the same audit
      found: `agent/audience.py` covered the trend-generated and
      externally-supplied paths but not `VerdandiOrchestrator` itself —
      the pipeline's own primary path, the one that produced the
      published clips being graded. Added as a fourth opt-in
      (`audience_check`), same pattern as the three weave forms.
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
