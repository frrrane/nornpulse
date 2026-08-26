#!/usr/bin/env python3
"""
Check a rendered clip's burned-in captions against its own spoken audio.

    python scripts/verify_captions.py output_clips/clip_x_9x16.mp4

Why this exists
---------------
Caption desync was diagnosed twice from timestamps alone and both readings
were wrong. The first said the boundary chunks were being compressed; the
second said the centiseconds were malformed. Both were real defects, both
were fixed, and neither was the thing the reviewer was seeing.

The reviewer was comparing the words on screen against the words in their
ears, and that comparison was never made in the pipeline. It is cheap: ask
the transcription model what the clip's audio actually says, line the
result up against the .ass, and print the disagreements.

This does not gate a render. It is a check to run before staging clips for
a human, because a human rejecting a clip is the expensive way to learn
that the captions are a phrase out of step.

It costs one model call per clip and needs the same credentials as the
pipeline.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

load_dotenv()

# How far a caption may sit from the audio of the same words before it is
# worth reporting. Below this, transcription timestamps are themselves the
# larger source of error.
TOLERANCE_SEC = 0.75


def ass_captions(path: Path) -> list[tuple[float, float, str]]:
    """(start, end, text) for every Dialogue line, styling stripped."""
    out = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.startswith("Dialogue:"):
            continue
        fields = line.split(",", 9)
        if len(fields) < 10:
            continue

        def sec(stamp: str) -> float:
            h, m, s = stamp.split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)

        text = re.sub(r"\{[^}]*\}", "", fields[9]).strip()
        if text:
            out.append((sec(fields[1]), sec(fields[2]), text))
    return sorted(out)


def spoken(video: Path) -> list[tuple[float, str]]:
    """What the clip's audio actually says, per phrase, in clip-relative seconds."""
    from google.genai import types

    from agent import genai_client as gc

    client, model = gc.client_for("gemini-3.6-flash", api_key=os.getenv("GEMINI_API_KEY"))
    response = client.models.generate_content(
        model=model,
        contents=types.Content(role="user", parts=[
            types.Part.from_bytes(data=video.read_bytes(), mime_type="video/mp4"),
            types.Part(text=(
                "Transcribe ONLY the spoken audio of this short video. Give a "
                "timestamp in seconds from the start of THIS clip for each "
                "phrase, one per line, formatted exactly as: [S.SS] words\n"
                "Ignore any text burned into the picture — it may be wrong, "
                "and reading it instead of listening defeats the purpose.")),
        ]),
    )
    out = []
    for line in (response.text or "").splitlines():
        m = re.match(r"\s*\[(\d+(?:\.\d+)?)\]\s*(.+)", line)
        if m:
            out.append((float(m.group(1)), m.group(2).strip()))
    return out


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z']+", text.lower()) if len(w) > 2}


def compare(captions, heard) -> list[str]:
    """
    One complaint per heard phrase whose words are not on screen when spoken.

    Matched on word overlap rather than string equality: the two
    transcriptions are independent, so they disagree on punctuation and the
    odd word even when the timing is perfect.
    """
    problems = []
    for at, phrase in heard:
        want = _words(phrase)
        if not want:
            continue
        # Strictly on screen, or within TOLERANCE_SEC of being so.
        #
        # Captions sit shoulder to shoulder with small gaps between them, and
        # the two transcriptions are independent, so a phrase whose heard
        # time lands in a gap is normal rather than missing. Without this,
        # correct captions were reported as absent: measured at 0.12s and
        # 0.63s from their audio, well inside anything a viewer would notice.
        showing = next((c for c in captions if c[0] - 0.01 <= at < c[1] + 0.01), None)
        if showing is None:
            near = [c for c in captions
                    if c[0] - TOLERANCE_SEC <= at <= c[1] + TOLERANCE_SEC]
            showing = next((c for c in near if want & _words(c[2])), None)
            if showing is not None:
                continue
            if near:
                showing = near[0]
            else:
                problems.append(
                    f"  {at:6.2f}s  heard {phrase[:44]!r} — no caption within "
                    f"{TOLERANCE_SEC:.2f}s")
                continue
        if want & _words(showing[2]):
            continue
        # Wrong caption showing. Say where the right one actually is, since
        # that distinguishes a lag from a caption that is simply absent.
        elsewhere = next(
            (c for c in captions if want & _words(c[2])), None)
        where = (f"it appears at {elsewhere[0]:.2f}s ({elsewhere[0] - at:+.2f}s)"
                 if elsewhere else "those words are captioned nowhere")
        problems.append(
            f"  {at:6.2f}s  heard {phrase[:40]!r}\n"
            f"           shown {showing[2][:40]!r} — {where}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", help="rendered clip (…_9x16.mp4)")
    ap.add_argument("--subs", default=None,
                    help="the .ass file (default: alongside the video)")
    args = ap.parse_args()

    video = Path(args.video)
    if not video.exists():
        print(f"❌ no such file: {video}")
        return 1
    subs = Path(args.subs) if args.subs else video.with_name(
        video.name.replace("_9x16.mp4", "_subs.ass"))
    if not subs.exists():
        print(f"❌ no subtitle file at {subs}")
        return 1

    captions = ass_captions(subs)
    print(f"🎬 {video.name}\n📝 {len(captions)} captions in {subs.name}")
    print("👂 transcribing the clip's own audio...")
    heard = spoken(video)
    if not heard:
        print("⚠️  no audio transcribed; cannot verify.")
        return 1
    print(f"   {len(heard)} phrases heard\n")

    problems = compare(captions, heard)
    if not problems:
        print(f"✅ every heard phrase is on screen when it is spoken.")
        return 0
    print(f"❌ {len(problems)} of {len(heard)} phrases mismatched:")
    print("\n".join(problems))
    return 1


if __name__ == "__main__":
    sys.exit(main())
