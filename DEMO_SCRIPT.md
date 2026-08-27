# Demo video script — 3:00 hard cap

Devpost requires ≤3 minutes. Every claim below is checkable on the live demo;
nothing here is a figure that would need a caveat if a judge paused the video.

**The narration below is generated from `demo_beats.py` and must match it
verbatim.** The two files drifted once — the prose predated the scoreboard and
the trend loop while the beats predated nothing — and `tests/test_demo_beats.py`
now fails if they diverge again. Edit the beats; re-render this.

Record at 1920×1080. Have `nornpulse.nornlabs.ai` **already warm** before you
start — Cloud Run scales to zero and a cold start is ~62s. Either hit it once a
minute beforehand or set `--min-instances=1` for the day.

---

## 0:00 – 0:16 · The hook

**On screen:** Home page, the two caption metrics side by side.

> Here are two numbers from the same dataset. Captions on a channel with a
> hundred thousand subscribers: plus thirty-four percent reach. The same
> captions on a channel with under a hundred subscribers: minus four
> percent. Same decision. Opposite answer.

## 0:16 – 0:32 · The problem

**On screen:** Slow scroll of the Home page.

> Nearly every piece of short-form advice was measured on channels that
> already have an audience, then sold to channels that don't. At the wrong
> scale it doesn't just weaken. It reverses. Four and a half billion videos,
> banded by channel size.

## 0:32 – 1:03 · It makes the video

**On screen:** **Manual shot.** Terminal running `trend_publish.py --generate` — topic, brief, rights verdict — then the finished Short playing full-frame, sound on.

> So it makes videos on that basis. Urðr asks ClickHouse what is travelling
> and returns a topic with a denominator: unboxing, three trending videos,
> median nine point seven million views. Verðandi writes the brief, three
> beats in eight seconds, and picks one premise over two others. A rights
> check runs before a frame exists. Veo generates, Mímir narrates, the hook
> burns into the first three seconds. That clip is live, and nobody typed a
> word of it.

## 1:03 – 1:20 · Every decision, labelled

**On screen:** Provenance panel on a finished clip, expanded.

> And every decision is labelled. Three measured, four assumed, one model
> judgement. The hook is measured, with a sample size attached. The framing
> is a seeded prior, because the public dataset has no visual features to
> ground it against, and it says so.

## 1:20 – 1:38 · Pointed at itself

**On screen:** Intelligence page → benchmark vs reality panel.

> A tool that only audits other people's advice is doing half the job, so we
> pointed it at ourselves. The benchmark says a channel this size gets two
> and a half thousand views. Our two real channels get thirteen, and three
> hundred and forty-three.

## 1:38 – 1:55 · Why the benchmark is wrong

**On screen:** Intelligence page, scrolled to the crawl-bias note.

> Here is why. The public dataset is a crawl, so it only contains videos
> discoverable enough to be crawled. A channel posting into the void isn't
> in it. Banding by size doesn't remove survivorship bias, because the
> population inside the band is filtered too.

## 1:55 – 2:13 · The scoreboard

**On screen:** The scoreboard panel.

> So forecasts are calibrated against the channel's own history, and then
> graded. Right now the scoreboard says two of sixteen are gradeable: three
> are too young, six were published before forecasts were recorded, and five
> point at videos that no longer exist. It reports that instead of averaging
> an accuracy figure over two clips.

## 2:13 – 2:21 · The human gate, automated

**On screen:** Review page, the real decision ledger read from ClickHouse.

> Nothing publishes itself. Every clip goes to a human by email, with a
> comment that goes back into the record.

## 2:21 – 2:34 · The human gate, by hand

**On screen:** **Manual shot.** The approval email in a real inbox, reply comment "could be funnier" visible.

> This one was approved with the note, could be funnier. The forecast is
> written down before publication, then graded against what happened. It can
> be wrong in public, which is the point.

## 2:34 – 2:53 · Close

**On screen:** Home page, the thesis line.

> Most AI tools present everything they output with identical confidence.
> This one tells you which parts it measured, which it assumed, and which it
> guessed, and when the sample is too thin it refuses to answer. NornPulse.
> Every chart is running against the real warehouse right now.

<!-- generated: 431 words, ~167s at 155wpm, 173s at 150wpm -->

**End card:** `nornpulse.nornlabs.ai`

---

## Shot list to capture beforehand

Playwright drives everything except the two beats marked **manual**; those are
the two that show the product doing the thing the competition is about, so
they are the ones to film first, not last.

- [ ] **Terminal: `trend_publish.py --channel sloptokdaily --generate`** —
      topic with its denominator, the brief, the rights verdict
- [ ] **The generated Short playing full-frame, sound on**
- [ ] **The approval email in a real inbox**, with the reply comment visible
- [ ] Home page, full scroll, warm instance
- [ ] A provenance panel expanded, showing all three levels
- [ ] Intelligence → benchmark vs reality panel
- [ ] The scoreboard panel, gradeable count visible
- [ ] Outcomes table with non-zero actual views

## Do not say

- Anything implying Norn Labs is a company — it's an independent project.
- "Accurate" or "proven" about the forecast. It is calibrated, on thin data,
  and the honest framing is that it is closer than uncalibrated and still
  labelled unconfident on the channel with only five videos.
- Any view count from memory. Read it off the screen at record time. The
  figures here were true on 26 Aug 2026 and exist to time the narration.
- That the pipeline is autonomous end to end. It generates autonomously and
  stops at a human. That gate is a feature; do not describe it as one you
  intend to remove.
