# NornPulse backlog

Ordered roughly by leverage, not by size. Deadline for the Devpost
submission is **Wed 9 September 2026, 2pm PDT**.

---

## Hard requirements (cannot ship without)

- [ ] **Demo video, ≤3 minutes.** The single most compressible-looking item
  that is actually the least compressible: a good three minutes takes about
  two days once re-takes are counted. Protect its slot ahead of any feature.
- [ ] **Written description** for the Devpost entry.

## In progress

- [ ] **Pitch rewrite.** Reposition everything around advice asymmetry —
  short-form advice is measured on channels that already have an audience, so
  applied to a channel that doesn't, some of it reverses. Second act: the same
  bias turned up in our own grounding layer at 10×, and we corrected it.
  Touches `README.md`, the Home page hero, and the demo script.

## Next

- [ ] **Upload cadence.** Six uploads/day maximum (1,600 quota units each
  against 10,000/day). Views need days to mature, so elapsed time is the
  binding constraint — every day without publishing is outcome data that will
  not exist by the deadline. `publish_file.py` handles externally-made video;
  `test_hitl.py` → `check_approvals.py` handles pipeline-generated clips.

## Housekeeping

- [ ] **GitHub sync and repo clean-up.** Several root scripts predate the
  current structure and are either dead or misleadingly named:
  `approve_and_publish.py` (a one-off with a hardcoded title, superseded by
  `check_approvals.py`), `probe_clickhouse_mcp.py`, `generate_test_assets.py`,
  `daemon.py`, and `test_pipeline.py` / `test_hitl.py`, which sit at the root
  looking like pytest files while being manual end-to-end runners that spend
  real API credit. Decide what stays, move the runners out of the way of
  `pytest`, and keep the README's structure block honest afterwards.

  Also check what is committed that should not be, and what is ignored that
  should be tracked.

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

- [ ] **Cold start ~62s.** Cloud Run scales to zero. `--min-instances=1` is
  about $12/month and removes it. Fine to leave until the demo is recorded,
  but do not let a judge meet a 62-second blank page.

- [ ] **Emoji in captions.** libass cannot render CBDT colour emoji; would need
  an ffmpeg PNG overlay pass.

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
