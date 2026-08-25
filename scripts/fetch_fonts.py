#!/usr/bin/env python3
"""
Fetch the display typefaces the renderer offers, from Google Fonts.

    python scripts/fetch_fonts.py            # download anything missing
    python scripts/fetch_fonts.py --list     # show what is wanted and held
    python scripts/fetch_fonts.py --force    # re-download everything

Why fonts are bundled rather than assumed
-----------------------------------------
ffmpeg's drawtext cannot resolve a family name — it needs a path to a file.
libass can resolve names, and that is worse: when a name does not resolve it
substitutes something else and renders happily, so a container missing a
face produces a lighter, wrong-looking caption with nothing logged. This
project has already been bitten by that once.

So a face has to exist as a *file*, in both the workstation and the
container, or a render looks one way locally and another way deployed. Arial
Black was rejected as the banner default for exactly this reason: it is
Monotype, present on this workstation and absent from the image.

Everything here is OFL licensed, which permits redistribution provided the
licence travels with the font. The licence file is fetched alongside each
family for that reason.

Downloaded from the google/fonts repository, which is the canonical source
and serves the actual files rather than a webfont subset — a subset would
be missing glyphs the moment a title used one.
"""

from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path
from typing import Dict, NamedTuple

FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
RAW_BASE = "https://github.com/google/fonts/raw/main"


class Face(NamedTuple):
    """One typeface, and where it lives upstream."""

    filename: str
    path: str          # relative to RAW_BASE
    licence: str       # the family's licence file, fetched alongside
    note: str


# Chosen for one job: a hook banner competing with moving video, read in
# about a second, on a phone. All are heavy or condensed display faces —
# a text face at 64px is still a text face.
FACES: Dict[str, Face] = {
    "Anton": Face(
        "Anton-Regular.ttf", "ofl/anton/Anton-Regular.ttf", "ofl/anton/OFL.txt",
        "Very heavy condensed sans. The closest freely-licensed face to the "
        "Impact look the format's conventions expect."),
    "Bebas Neue": Face(
        "BebasNeue-Regular.ttf", "ofl/bebasneue/BebasNeue-Regular.ttf",
        "ofl/bebasneue/OFL.txt",
        "Tall condensed caps. Fits long titles on one line where a wide "
        "face would wrap to three."),
    "Archivo Black": Face(
        "ArchivoBlack-Regular.ttf", "ofl/archivoblack/ArchivoBlack-Regular.ttf",
        "ofl/archivoblack/OFL.txt",
        "Wide and very heavy. Closest to Arial Black without the licence."),
    "Oswald Bold": Face(
        "Oswald-Bold.ttf", "ofl/oswald/Oswald%5Bwght%5D.ttf", "ofl/oswald/OFL.txt",
        "Condensed, slightly lighter. Reads as editorial rather than meme."),
}


def held() -> Dict[str, Path]:
    """Which faces are already on disk."""
    return {name: FONT_DIR / face.filename
            for name, face in FACES.items()
            if (FONT_DIR / face.filename).exists()}


def fetch(name: str, face: Face, force: bool = False) -> bool:
    target = FONT_DIR / face.filename
    if target.exists() and not force:
        print(f"  · {name:16} already held")
        return True

    FONT_DIR.mkdir(parents=True, exist_ok=True)
    url = f"{RAW_BASE}/{face.path}"
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read()
    except Exception as e:
        print(f"  ✗ {name:16} {str(e)[:70]}")
        return False

    # A repository that has moved a file serves an HTML error page with a
    # 200, which would land on disk as a font nothing can open.
    if len(data) < 10_000 or data[:1] in (b"<", b"{"):
        print(f"  ✗ {name:16} did not look like a font ({len(data)} bytes)")
        return False

    target.write_bytes(data)

    # The licence travels with the font: OFL permits redistribution only
    # while it does.
    licence_target = FONT_DIR / f"OFL-{name.replace(' ', '')}.txt"
    try:
        with urllib.request.urlopen(f"{RAW_BASE}/{face.licence}", timeout=60) as r:
            licence_target.write_bytes(r.read())
    except Exception as e:
        print(f"  ⚠️  {name}: font saved but its licence did not download ({e}). "
              f"Redistribution needs it — fetch it before shipping.")

    print(f"  ✓ {name:16} {len(data) / 1024:.0f} KB")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="show what is wanted and held")
    ap.add_argument("--force", action="store_true", help="re-download everything")
    args = ap.parse_args()

    if args.list:
        have = held()
        print(f"📁 {FONT_DIR}")
        for name, face in FACES.items():
            mark = "✓" if name in have else "·"
            print(f"  {mark} {name:16} {face.note}")
        print(f"\n{len(have)}/{len(FACES)} held")
        return 0

    print(f"⬇️  Fetching display faces into {FONT_DIR}")
    failures = sum(not fetch(n, f, args.force) for n, f in FACES.items())
    print(f"\n{len(FACES) - failures}/{len(FACES)} available."
          + ("" if not failures else " Missing faces fall back to the next candidate."))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
