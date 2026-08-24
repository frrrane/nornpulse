# Demo video script — 3:00 hard cap

Devpost requires ≤3 minutes. Every claim below is checkable on the live demo;
nothing here is a figure that would need a caveat if a judge paused the video.

Record at 1920×1080. Have `nornpulse.nornlabs.ai` **already warm** before you
start — Cloud Run scales to zero and a cold start is ~62s. Either hit it once a
minute beforehand or set `--min-instances=1` for the day.

---

## 0:00 – 0:18 · The hook

**On screen:** Home page, the two caption metrics side by side.

> "Here are two numbers from the same dataset.
>
> Captions on a channel with a hundred thousand subscribers: **plus
> thirty-four percent reach**. The same captions on a channel with under a
> hundred subscribers: **minus four percent**.
>
> Same decision. Opposite answer."

*Beat. Let the two tiles sit.*

## 0:18 – 0:40 · The problem

**On screen:** slow scroll of the Home page.

> "Almost every piece of short-form advice you have ever been given — post at
> the weekend, always caption, open with a shock stat — was measured on
> channels that already have an audience. Then it gets sold to channels that
> don't.
>
> At the wrong scale it isn't just weaker. It reverses, because the thing that
> made it work — a subscriber base feeding browse traffic — doesn't exist yet.
>
> NornPulse reads every creative decision inside the size band of the channel
> actually publishing it. Four and a half billion videos, banded."

## 0:40 – 1:20 · What it does

**On screen:** Create page → pick a source → generate. Cut the wait.

> "Six agents. Urðr reads the history out of ClickHouse. Verðandi decides
> where to cut and what the hook is. Skuld renders vertical. Bragi scores it,
> Heimdall makes the cover, Mímir narrates.
>
> But the interesting part isn't that it produces a clip. It's this —"

**On screen:** open the provenance panel on a finished clip.

> "— every decision, labelled. Three measured, four assumed, one model
> judgement. The hook is measured: three thousand nine hundred median views
> for this size band, n equals nine thousand one hundred. The framing is a
> seeded prior — the public dataset has no visual features, so there's nothing
> to ground it against, and it says so rather than pretending."

## 1:20 – 2:05 · The part that matters

**On screen:** Intelligence page → benchmark vs reality panel.

> "A tool that only audits other people's advice is doing half the job. So we
> pointed it at ourselves.
>
> The benchmark says a channel this size gets two and a half thousand views.
> Our two real channels get thirteen, and three hundred and forty-three. The
> population median is roughly our best-ever video, not a typical one.
>
> Here's why. The public dataset is a **crawl**. It only contains videos that
> were discoverable enough to be crawled. A channel posting into the void
> isn't in it — so banding by size doesn't remove survivorship bias, because
> the population inside the band is filtered too."

**On screen:** a clip card showing calibrated vs uncalibrated forecast.

> "So forecasts get calibrated against the channel's own history. Uncalibrated,
> two thousand four hundred. Calibrated, three hundred and five. Actual median:
> three hundred and forty-three.
>
> Both numbers stay on screen. The gap is the finding."

## 2:05 – 2:40 · Closing the loop

**On screen:** the approval email, then the outcomes table with real views.

> "Nothing publishes itself. Every clip goes to a human by email — approve or
> reject, with a comment that goes back into the record.
>
> The forecast is written down *before* it publishes. Then real statistics sync
> back, and it gets graded against what actually happened. It can be wrong in
> public, which is the whole point."

## 2:40 – 3:00 · Close

**On screen:** Home page, the thesis line.

> "Most AI tools present everything they output with identical confidence.
> This one tells you which parts it measured, which it assumed, and which it
> guessed — and when the sample is too thin, it refuses to answer.
>
> NornPulse. It's live, it's read-only, and every chart on it is running
> against the real warehouse right now."

**End card:** `nornpulse.nornlabs.ai`

---

## Shot list to capture beforehand

- [ ] Home page, full scroll, warm instance
- [ ] Create page mid-generation (progress visible) — then cut
- [ ] A provenance panel expanded, showing all three levels
- [ ] Intelligence → benchmark vs reality panel
- [ ] A clip card with calibrated + uncalibrated forecast
- [ ] The HITL approval email, in a real inbox
- [ ] Outcomes table with non-zero actual views

## Do not say

- Anything implying Norn Labs is a company — it's an independent project.
- "Accurate" or "proven" about the forecast. It is calibrated, on thin data,
  and the honest framing is that it is 24× closer than uncalibrated and still
  labelled unconfident on the channel with only five videos.
- Any view count from memory. Read it off the screen at record time.
