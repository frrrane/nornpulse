#!/usr/bin/env python3
"""
Record the screen segments for the demo, one video file per beat.

    python demo_capture.py                                  # against localhost
    python demo_capture.py --url https://nornpulse.nornlabs.ai

Scripted rather than hand-recorded, because the product keeps changing.
A demo captured by hand today is stale next week and nobody wants to redo
it; captured this way, re-running the command the day before submitting
produces footage of whatever the product is by then.

Playwright records real video of the browser session — smooth scrolling and
chart animation included — rather than stitching screenshots, which look
like a slideshow. Each beat gets its own browser context so it gets its own
file, which is what the assembler needs in order to cut them against
narration.

Chrome is driven through the installed browser (`channel="chrome"`) because
Playwright's own bundled chromium is not downloaded in this environment.
"""

import argparse
import pathlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

from demo_beats import BEATS

VIEWPORT = {"width": 1920, "height": 1080}
# Streamlit is slow to first paint and slower still against a cold Cloud Run
# instance, so the settle timeout is generous. A beat that starts recording
# before the page has content produces a video of an empty background, which
# is worse than a failure because it looks like it worked.
SETTLE_TIMEOUT_MS = 120_000

# Any of these means Streamlit has painted something real.
FIRST_PAINT = "[data-testid='stMetric'], h1, [data-testid='stDataFrame']"


def find_content_start(video: Path, fps_probe: float = 4.0,
                       min_bright_px: int = 6000) -> float:
    """
    Seconds of blank lead-in at the start of a recording.

    Streamlit takes about ten seconds to paint even warm — websocket
    handshake, then a rerun — and Playwright starts recording the moment the
    context opens. Every beat therefore begins with a flat expanse of
    background colour. Rather than guess a fixed offset, sample the frames
    and find the first one with real content in it, which also copes with a
    cold Cloud Run instance taking a minute.
    """
    import subprocess, tempfile
    from PIL import Image

    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(video), "-vf", f"fps={fps_probe}",
             f"{tmp}/f_%05d.png"], check=True, capture_output=True)
        frames = sorted(pathlib.Path(tmp).glob("f_*.png"))
        for i, f in enumerate(frames):
            # Counting distinct colours does not work: compression noise on a
            # flat background clears any low threshold, and the loading
            # spinner clears a high one. The app is bone text on a dark
            # ground, so the reliable signal is how many genuinely bright
            # pixels exist — a painted screen has tens of thousands, an empty
            # one has almost none.
            with Image.open(f) as im:
                grey = im.convert("L")
                bright = sum(n for value, n in grey.getcolors(256) if value > 150)
            if bright >= min_bright_px:
                return i / fps_probe
    return 0.0


def write_slate(out: Path, beat, duration: float) -> bool:
    """
    A placeholder card for a beat that has to be filmed by hand.

    Deliberately ugly and legible: it names the beat and the shot, so a
    rough cut assembled from captures shows exactly what is still missing
    and for how long, instead of a gap someone has to remember.
    """
    from agent import text_fit

    font = text_fit.font_file()
    face = f"fontfile={font}:" if font else ""

    def line(text, y, size, colour="white"):
        safe = (text.replace("\\", "").replace(":", "\\:")
                    .replace("'", "’").replace("%", "\\%"))
        return (f"drawtext={face}text='{safe}':fontcolor={colour}:fontsize={size}"
                f":x=(w-text_w)/2:y={y}")

    # Wrapped on word boundaries. A fixed-width slice splits "full-frame"
    # into "full-fr" and "ame", which makes the one card whose whole job is
    # to be read at a glance the hardest thing in the cut to read.
    import textwrap
    wrapped = textwrap.wrap(beat.manual, 58)[:3]
    filters = [
        line("SHOOT THIS BY HAND", 380, 64, "0xE85D4A"),
        line(beat.key.upper(), 480, 96),
    ] + [line(w, 640 + n * 52, 38, "0xBBBBBB") for n, w in enumerate(wrapped)]

    out.unlink(missing_ok=True)
    try:
        subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "lavfi",
             "-i", f"color=c=0x14161C:s={VIEWPORT['width']}x{VIEWPORT['height']}"
                   f":d={duration:.2f}:r=25",
             "-vf", ",".join(filters),
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-y", str(out)],
            check=True, capture_output=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"     ❌ slate failed: {e.stderr.decode('utf-8', 'replace')[:120]}")
        return False


def run_actions(page, actions, verbose=False):
    for verb, arg in actions:
        if verb == "wait":
            time.sleep(float(arg))
        elif verb == "scroll":
            page.mouse.wheel(0, int(arg))
        elif verb == "click":
            try:
                page.click(arg, timeout=15_000)
            except Exception as e:
                # A missing control is a demo-script problem, not a crash:
                # the rest of the beat is still worth recording.
                print(f"     ⚠️  could not click {arg!r}: {str(e)[:70]}")
        elif verb == "settle":
            try:
                page.wait_for_selector(arg, timeout=SETTLE_TIMEOUT_MS)
            except Exception:
                print(f"     ⚠️  {arg!r} never appeared; recording anyway")
        else:
            raise ValueError(f"Unknown action verb {verb!r}")
        if verbose:
            print(f"     · {verb} {arg}")


def capture(url: str, out_dir: Path, only: str | None = None,
            headed: bool = False, verbose: bool = False) -> int:
    from playwright.sync_api import sync_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    beats = [b for b in BEATS if not only or b.key == only]
    if not beats:
        print(f"❌ No beat named {only!r}. Known: {[b.key for b in BEATS]}")
        return 1

    failures = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=not headed)
        for i, beat in enumerate(beats, 1):
            if beat.manual:
                # A manual beat is a terminal, an inbox, or a Short playing —
                # nothing a browser can film. Recording it anyway would
                # produce half a minute of a static page that looks like a
                # successful capture, so a slate is written instead: the
                # assembler gets a placeholder of the right length, and the
                # gap is visible in the rough cut rather than discovered on
                # the last day.
                final = out_dir / f"{beat.key}.mp4"
                dur = max(beat.min_seconds, len(beat.narration.split()) / 2.5)
                print(f"  [{i}/{len(beats)}] {beat.key:12} MANUAL — {beat.manual[:52]}")
                if not write_slate(final, beat, dur):
                    failures += 1
                    continue
                print(f"     🎬 {final.name}  ({dur:.0f}s slate — film this by hand)")
                continue

            target = url.rstrip("/") + beat.page
            print(f"  [{i}/{len(beats)}] {beat.key:12} {target}")
            raw_dir = out_dir / f".raw_{beat.key}"
            if raw_dir.exists():
                shutil.rmtree(raw_dir)

            # One context per beat so each recording is its own file.
            context = browser.new_context(
                viewport=VIEWPORT,
                record_video_dir=str(raw_dir),
                record_video_size=VIEWPORT,
                device_scale_factor=1,
            )
            page = context.new_page()
            started = time.time()
            try:
                page.goto(target, wait_until="domcontentloaded", timeout=SETTLE_TIMEOUT_MS)
                # Wait for the app to actually paint before the beat's own
                # actions begin, so min_seconds measures visible time rather
                # than loading time.
                try:
                    page.wait_for_selector(FIRST_PAINT, timeout=SETTLE_TIMEOUT_MS)
                except Exception:
                    print("     ⚠️  app never painted; recording anyway")
                content_at = time.time()
                run_actions(page, beat.actions, verbose=verbose)
                # Hold the frame until the beat is at least as long as the
                # narration that will play over it.
                remaining = beat.min_seconds - (time.time() - content_at)
                if remaining > 0:
                    time.sleep(remaining)
            except Exception as e:
                print(f"     ❌ {str(e)[:100]}")
                failures += 1
            finally:
                # The video is only flushed to disk when the context closes.
                context.close()

            produced = sorted(raw_dir.glob("*.webm"))
            if not produced:
                print("     ❌ no video written")
                failures += 1
                continue
            raw = produced[0]
            lead_in = find_content_start(raw)
            final = out_dir / f"{beat.key}.mp4"
            final.unlink(missing_ok=True)
            # One re-encode: trim the blank prefix and produce the mp4 the
            # assembler wants, rather than webm now and mp4 again later.
            subprocess.run(
                ["ffmpeg", "-v", "error", "-ss", f"{lead_in:.2f}", "-i", str(raw),
                 "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                 "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-y", str(final)],
                check=True, capture_output=True)
            shutil.rmtree(raw_dir, ignore_errors=True)
            print(f"     ✅ {final.name}  ({final.stat().st_size / 1e6:.1f} MB, "
                  f"trimmed {lead_in:.1f}s lead-in)")
        browser.close()
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://localhost:8501",
                    help="base URL of the running app")
    ap.add_argument("--out", default="demo_build/capture")
    ap.add_argument("--beat", default=None, help="capture only this beat")
    ap.add_argument("--headed", action="store_true",
                    help="show the browser while recording")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    print(f"🎥 Capturing {len(BEATS) if not args.beat else 1} beat(s) from {args.url}")
    rc = capture(args.url, Path(args.out), only=args.beat,
                 headed=args.headed, verbose=args.verbose)
    print("done" if rc == 0 else "finished with failures")
    return rc


if __name__ == "__main__":
    sys.exit(main())
