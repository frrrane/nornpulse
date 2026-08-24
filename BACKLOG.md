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

- [ ] **Trend-driven sloptok generation.** Read the trending snapshot, pick a
  topic SlopTokDaily could speak to, write a brief, generate footage, and take
  it through the existing clip → caption → forecast → publish path. This is the
  loop that makes the system agentic rather than a tool, and it is the natural
  home for the comedy profile that already exists in `channels.json`.

  Constraints that shape it: footage must be copyright-clean, so generated
  (Veo / Grok Imagine) first, with public-domain archives — Internet Archive,
  NASA, Prelinger, Wikimedia Commons — behind the same interface. Music is
  already clean via Lyria. Never re-cut trending videos; that is the trap.

- [ ] **Calibration scoreboard.** Every forecast against what actually
  happened, and the share that landed inside its own p10–p90 band. Both numbers
  are already stored; this is a view, not new plumbing. Needs published
  forecasts to age a few days before it shows anything real.

- [ ] **Upload cadence.** Six uploads/day maximum (1,600 quota units each
  against 10,000/day). Views need days to mature, so elapsed time is the
  binding constraint — every day without publishing is outcome data that will
  not exist by the deadline. `publish_file.py` handles externally-made video;
  `test_hitl.py` → `check_approvals.py` handles pipeline-generated clips.

## Known problems worth fixing

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
- [x] `nornlabs.ai` rebuilt, privacy policy published
- [x] OAuth consent screen published — refresh tokens no longer expire weekly
