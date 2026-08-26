#!/usr/bin/env python3
"""
Narrate the beats and cut them into the demo film.

    python demo_capture.py --url https://nornpulse.nornlabs.ai   # footage
    python demo_assemble.py                                      # voice + cut

Two stages rather than one, because they fail for unrelated reasons and
have very different costs. Capture needs a warm app and a browser and takes
minutes; narration is a TTS bill and is worth caching. Splitting them means
a re-cut after an edit to one line does not re-record eight browser
sessions, and a failed capture does not throw away the voice track.

Each beat becomes one segment whose length is its narration, floored at the
beat's own `min_seconds`. Video is held on its last frame if the narration
outruns the footage, rather than the footage being sped up — a demo that
subtly accelerates reads as nervous.

The hard three-minute cap is checked *before* the expensive work, not
after. Going over is a disqualification, and finding out at the end of a
ten-minute render is finding out too late.
"""

import argparse
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

from demo_beats import BEATS, estimated_runtime_sec

load_dotenv()

CAP_SEC = 180.0
FPS = 25
# Narration sits slightly ahead of the picture: a beat that cuts the instant
# the last word lands feels clipped, and a held frame is cheap.
TAIL_PAD_SEC = 0.6


def duration_of(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True).stdout.strip()
    return float(out or 0.0)


def narrate_beats(out_dir: Path, force: bool = False) -> dict:
    """
    One WAV per beat, cached by key.

    A missing track is not fatal here the way it is in a clip: the segment
    still gets built, silent, and the assembler reports which beats have no
    voice so they can be re-run rather than silently shipping a mute demo.
    """
    from agent.mimir_narrator import MimirNarrator

    out_dir.mkdir(parents=True, exist_ok=True)
    narrator = MimirNarrator()
    tracks = {}
    for beat in BEATS:
        wav = out_dir / f"{beat.key}_narration.wav"
        if wav.exists() and not force:
            print(f"  {beat.key:12} cached  ({duration_of(wav):.1f}s)")
            tracks[beat.key] = wav
            continue
        try:
            got = narrator.narrate(
                clip_id=beat.key, script_text=beat.narration,
                energy_level=0.55, output_dir=str(out_dir))
        except Exception as e:
            print(f"  {beat.key:12} ❌ {str(e)[:70]}")
            continue
        if not got:
            print(f"  {beat.key:12} ❌ no audio returned")
            continue
        tracks[beat.key] = Path(got)
        print(f"  {beat.key:12} ✅ {duration_of(Path(got)):.1f}s")
    return tracks


def build_segment(video: Path, audio: Path | None, out: Path,
                  min_seconds: float) -> float:
    """
    One beat as a single file, video held to the length of its narration.

    `tpad=stop_mode=clone` freezes the final frame rather than looping the
    clip: a five-second capture under a twenty-second line would otherwise
    play four times, which reads as a glitch rather than a still.
    """
    speech = duration_of(audio) if audio else 0.0
    target = max(min_seconds, speech + TAIL_PAD_SEC)

    # Every input first, then every output option. ffmpeg binds an option to
    # whichever file follows it, so a -vf sitting between two -i flags is
    # read as an input option and rejected.
    cmd = ["ffmpeg", "-v", "error", "-i", str(video)]
    if audio:
        cmd += ["-i", str(audio)]
    else:
        # Silence still gets a real stream, so every segment carries audio
        # and the concat has nothing to reconcile.
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]

    vf = (f"tpad=stop_mode=clone:stop_duration={target:.2f},"
          f"trim=0:{target:.2f},setpts=PTS-STARTPTS,fps={FPS}")
    cmd += ["-vf", vf, "-map", "0:v", "-map", "1:a"]
    if audio:
        cmd += ["-af", f"apad=pad_dur={target:.2f},atrim=0:{target:.2f}"]
    cmd += ["-t", f"{target:.2f}", "-c:v", "libx264", "-preset", "medium",
            "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000",
            "-y", str(out)]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        # The command is long and the reason is in stderr; raising without it
        # leaves a wall of argv and no diagnosis.
        raise RuntimeError(
            f"segment build failed for {video.name}: "
            f"{e.stderr.decode('utf-8', 'replace')[-400:]}") from None
    return target


def assemble(capture_dir: Path, work_dir: Path, out_path: Path,
             skip_narration: bool = False, force: bool = False) -> int:
    missing = [b.key for b in BEATS if not (capture_dir / f"{b.key}.mp4").exists()]
    if missing:
        print(f"❌ No captured footage for: {', '.join(missing)}")
        print(f"   Run: python demo_capture.py --url <app>")
        return 1

    work_dir.mkdir(parents=True, exist_ok=True)
    tracks = {} if skip_narration else narrate_beats(work_dir, force=force)

    print("\n🎞️  Building segments...")
    segments, total = [], 0.0
    silent = []
    for beat in BEATS:
        seg = work_dir / f"seg_{beat.key}.mp4"
        audio = tracks.get(beat.key)
        if audio is None:
            silent.append(beat.key)
        length = build_segment(capture_dir / f"{beat.key}.mp4", audio, seg,
                               beat.min_seconds)
        segments.append(seg)
        total += length
        print(f"  {beat.key:12} {length:5.1f}s  "
              f"{'(no narration)' if audio is None else ''}")

    print(f"\n⏱️  {total:.0f}s total ({int(total // 60)}:{int(total % 60):02d}), "
          f"cap {CAP_SEC:.0f}s")
    if total > CAP_SEC:
        print(f"❌ Over the cap by {total - CAP_SEC:.0f}s. Trim narration in "
              f"demo_beats.py — the cap is a disqualification, not a guideline.")
        return 1

    listing = work_dir / "segments.txt"
    listing.write_text(
        "\n".join(f"file '{s.resolve()}'" for s in segments), encoding="utf-8")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Re-encode on concat rather than stream-copy: the segments already agree
    # on codec and rate, but a captured beat and a generated slate do not
    # always agree on timebase, and a copy-concat drifts audio when they
    # disagree.
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
         "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
         "-y", str(out_path)],
        check=True, capture_output=True)

    print(f"\n✅ {out_path}  ({out_path.stat().st_size / 1e6:.1f} MB, "
          f"{duration_of(out_path):.0f}s)")
    if silent:
        print(f"⚠️  no narration on: {', '.join(silent)} — re-run to retry TTS")
    manual = [b.key for b in BEATS if b.manual]
    if manual:
        print(f"🎬 still slates, not footage: {', '.join(manual)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--capture", default="demo_build/capture")
    ap.add_argument("--work", default="demo_build/audio")
    ap.add_argument("--out", default="demo_build/nornpulse_demo.mp4")
    ap.add_argument("--no-narration", action="store_true",
                    help="cut the footage silent, without calling TTS")
    ap.add_argument("--force", action="store_true",
                    help="re-narrate even where a cached track exists")
    args = ap.parse_args()

    # Checked before anything expensive runs.
    est = estimated_runtime_sec()
    print(f"📝 {len(BEATS)} beats, ~{est:.0f}s estimated narration "
          f"(cap {CAP_SEC:.0f}s)")
    if est > CAP_SEC:
        print(f"❌ The script is already over the cap. Trim demo_beats.py first.")
        return 1

    return assemble(Path(args.capture), Path(args.work), Path(args.out),
                    skip_narration=args.no_narration, force=args.force)


if __name__ == "__main__":
    sys.exit(main())
