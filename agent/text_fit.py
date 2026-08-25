# agent/text_fit.py
"""
⚡ NornPulse: Fitting burned-in text to the frame (text_fit.py)
Norn Labs (nornlabs.ai)

Wrapping text by counting characters is a proxy for a pixel budget, and the
two part company the moment the text is wide-glyphed or upper-case. That is
not a hypothetical: every clip in a batch of three had its title run off
both edges of the video, at 1180, 1441 and 1452 pixels inside a 1080 pixel
frame, and nothing raised — the title simply was not readable, on the one
frame that decides whether anyone keeps watching.

Both renderers burn text and both got this wrong independently. Skuld puts
a hook banner on cut clips; shortsmith burns a hook on generated ones. The
measuring belongs in one place so that fixing it once fixes it for both.

Measured with the real font at the real size. Where the longest line still
will not fit, the type shrinks rather than the line being cropped, down to
a floor below which a hook stops being a hook; below that the word is
broken, because an awkward break is legible and a cropped one is not.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Callable, List, Optional, Tuple

# Candidate fonts, in preference order. drawtext will not resolve family
# names, so these have to be concrete files.
FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/roboto/unhinted/RobotoTTF/Roboto-Black.ttf",
)


def font_file() -> Optional[str]:
    """A concrete font file that exists on this machine, or None."""
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def measurer(font_path: Optional[str], font_px: int) -> Optional[Callable[[str], float]]:
    """A width-in-pixels function for this font, or None if unmeasurable."""
    if not font_path:
        return None
    try:
        from PIL import ImageFont
        return ImageFont.truetype(font_path, font_px).getlength
    except Exception:
        return None


def fit_text(
    text: str,
    max_width_px: float,
    font_px: int,
    font_path: Optional[str] = None,
    min_font_px: int = 20,
    fallback_wrap: int = 14,
) -> Tuple[List[str], int]:
    """
    Wrap `text` to `max_width_px`, shrinking the type only if it must.

    Returns the lines and the font size they were fitted at, because the
    caller has to render with the same size that was measured — passing the
    original size back to ffmpeg would undo the whole calculation.

    Falls back to a conservative character wrap when the font cannot be
    measured, which gives a narrower block rather than a broken one.
    """
    text = " ".join((text or "").split())
    if not text:
        return [], font_px

    while True:
        width_of = measurer(font_path, font_px)
        if width_of is None:
            return textwrap.wrap(text, fallback_wrap) or [text], font_px

        lines: List[str] = []
        current = ""
        for word in text.split():
            candidate = f"{current} {word}".strip()
            if current and width_of(candidate) > max_width_px:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
        lines = lines or [text]

        if max(width_of(line) for line in lines) <= max_width_px:
            return lines, font_px
        if font_px <= min_font_px:
            return hard_break(lines, width_of, max_width_px), font_px
        # A single word wider than the frame: no wrap can save it, so shrink.
        font_px = max(min_font_px, int(font_px * 0.9))


def hard_break(lines: List[str], width_of: Callable[[str], float],
               max_width_px: float) -> List[str]:
    """
    Split any line still too wide, inside the word.

    Broken into near-equal pieces rather than greedily, because a greedy cut
    leaves an orphan — a very long word above a lone trailing letter — which
    reads as a rendering fault rather than as a long word.
    """
    out: List[str] = []
    for line in lines:
        if width_of(line) <= max_width_px or len(line) < 2:
            out.append(line)
            continue
        pieces = 2
        while pieces <= len(line):
            size = -(-len(line) // pieces)  # ceiling division
            chunks = [line[i:i + size] for i in range(0, len(line), size)]
            if all(width_of(c) <= max_width_px for c in chunks):
                out.extend(chunks)
                break
            pieces += 1
        else:
            out.append(line)
    return out
