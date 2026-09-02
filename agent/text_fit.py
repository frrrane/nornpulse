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

import logging
import textwrap
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Faces bundled with the project, fetched by scripts/fetch_fonts.py. These
# come first because they are the only ones guaranteed to exist in both the
# workstation and the container: a system font that is present in one and
# absent in the other renders one way locally and another way deployed, and
# libass substitutes silently rather than complaining.
BUNDLED_FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"

# Selectable by name, heaviest first. All OFL, all display faces — a text
# face at 64px is still a text face, and a hook banner competes with moving
# video behind it.
DISPLAY_FACES = {
    "Anton": "Anton-Regular.ttf",
    "Archivo Black": "ArchivoBlack-Regular.ttf",
    "Bebas Neue": "BebasNeue-Regular.ttf",
    "Oswald Bold": "Oswald-Bold.ttf",
}

# Fallbacks, in preference order. drawtext will not resolve family names, so
# these have to be concrete files. Bundled faces first, then black system
# weights, then bold, then regular: the banner shipped without specifying
# any file at all for a while, fell back to drawtext's default regular face,
# and a reviewer asked for "a more engaging font". Several paths per system
# face because the same font sits in different places on a workstation and
# in the container.
FONT_CANDIDATES = tuple(
    str(BUNDLED_FONT_DIR / name) for name in DISPLAY_FACES.values()
) + (
    "/usr/share/fonts/chromeos/roboto/Roboto-Black.ttf",
    "/usr/share/fonts/truetype/roboto/unhinted/RobotoTTF/Roboto-Black.ttf",
    "/usr/share/fonts/truetype/roboto/Roboto-Black.ttf",
    "/usr/share/fonts/chromeos/monotype/ariblk.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
)


def strip_emoji(text: str) -> str:
    """
    Drop characters no burned-in text path can draw.

    Neither libass nor ffmpeg's drawtext renders colour emoji — libass
    cannot read CBDT tables and drawtext draws a hollow box — and the same
    strings are handed to TTS, which reads them out as their names.

    So emoji live in the YouTube title, which is plain metadata and where
    every video that has actually travelled on these channels has them,
    and are removed at the three places text becomes pixels or speech.
    The cut is at U+2190 because everything above it in practice is arrows,
    dingbats and emoji, while accented Latin and punctuation sit below.
    """
    return " ".join("".join(c for c in (text or "") if ord(c) < 0x2190).split())


# Colour emoji glyph source for the one exception to strip_emoji: a
# trailing decorative run (see split_trailing_emoji) is composited as an
# image instead of stripped, since libass and drawtext still cannot draw
# it directly.
#
# Not bundled like DISPLAY_FACES -- the Dockerfile already apt-installs
# fonts-noto-color-emoji (it went in for glyph coverage before this
# existed, with a comment already anticipating a PNG-overlay path), so the
# container candidate comes first. The ChromeOS path covers a workstation
# dev box, verified present via `fc-list` there. Both paths were confirmed
# against the real font, not assumed: the container's exact apt-installed
# file was pulled out of the same base image (python:3.11-slim-bookworm)
# and Pillow rendered real coloured pixels from it before this shipped.
EMOJI_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",   # Debian/apt (container)
    "/usr/share/fonts/chromeos/noto/NotoColorEmoji.ttf",   # ChromeOS workstation
)

# The same cutoff strip_emoji uses, so "what gets composited" and "what
# still gets dropped" never disagree about what counts as an emoji.
_EMOJI_CUT = 0x2190


def emoji_font_file() -> Optional[str]:
    """The colour-emoji font, or None if neither candidate is on disk."""
    for candidate in EMOJI_FONT_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def _is_emoji_token(token: str) -> bool:
    """Whether every character in token is outside the plain-text range."""
    return bool(token) and all(ord(c) >= _EMOJI_CUT for c in token)


def split_trailing_emoji(text: str) -> Tuple[str, str]:
    """
    Split off a trailing run of emoji-only tokens, e.g.
    "Cats are back 🐱🎉" -> ("Cats are back", "🐱🎉").

    Only a *trailing* run is recognised: it is how titles in this pipeline
    actually carry decorative emoji -- a model appends them, it does not
    scatter them mid-sentence -- and detecting it this way sidesteps the
    much harder general case of an emoji glued mid-word, or one sitting
    between plain words that would then have to reflow around a gap.
    Anything outside this shape is still dropped by strip_emoji downstream,
    the same as before this existed.
    """
    words = (text or "").split()
    cut = len(words)
    while cut > 0 and _is_emoji_token(words[cut - 1]):
        cut -= 1
    return " ".join(words[:cut]), "".join(words[cut:])


# NotoColorEmoji ships exactly one embedded colour bitmap strike --
# FreeType raises "invalid pixel size" for every other size, confirmed
# against both the ChromeOS workstation font and the exact file the
# Dockerfile apt-installs. So a glyph always renders at this fixed size
# first and gets resized afterward to whatever a caller actually needs --
# the standard way a bitmap-strike font is used at an arbitrary scale.
EMOJI_STRIKE_PX = 109


def emoji_glyph(emoji_text: str, target_px: int):
    """
    Render emoji_text (e.g. "🦜🍲") as a tightly-cropped RGBA image sized
    to stand about target_px tall, and the advance width a caller should
    reserve beside it at that size.

    Returns (image, advance_width) or None -- when no emoji font is on
    disk, emoji_text is empty, or it renders to nothing. A caller treats
    None as "leave this off the frame", the same "worst acceptable outcome
    is the bare clip" degrade used everywhere else text gets burned in.
    """
    font_path = emoji_font_file()
    if not font_path or not emoji_text:
        return None
    try:
        from PIL import Image, ImageDraw, ImageFont
        font = ImageFont.truetype(font_path, EMOJI_STRIKE_PX)
        advance = font.getlength(emoji_text)
        bbox = font.getbbox(emoji_text, mode="RGBA")
        if not bbox or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            return None
        left, top, right, bottom = bbox
        img = Image.new("RGBA", (right - left, bottom - top), (0, 0, 0, 0))
        ImageDraw.Draw(img).text((-left, -top), emoji_text, font=font, embedded_color=True)

        scale = target_px / EMOJI_STRIKE_PX
        if abs(scale - 1.0) > 0.01:
            new_size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
            img = img.resize(new_size, Image.LANCZOS)
            advance *= scale
        return img, advance
    except Exception:
        logger.warning("Could not render an emoji glyph; leaving it off the frame.",
                        exc_info=True)
        return None


def font_file(preferred: Optional[str] = None) -> Optional[str]:
    """
    A concrete font file that exists on this machine, or None.

    `preferred` names a face from DISPLAY_FACES. An unknown or missing name
    falls through to the candidate order rather than raising: a typo in a
    channel profile should cost the choice, not the render — but it is
    logged, because silently rendering in a different face is exactly the
    failure this whole arrangement exists to avoid.
    """
    if preferred:
        filename = DISPLAY_FACES.get(preferred)
        if filename:
            path = BUNDLED_FONT_DIR / filename
            if path.exists():
                return str(path)
            logger.warning(
                f"Font {preferred!r} is not on disk. Run "
                f"scripts/fetch_fonts.py to download it; falling back.")
        else:
            logger.warning(
                f"Unknown font {preferred!r}. Known: "
                f"{', '.join(sorted(DISPLAY_FACES))}. Falling back.")

    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def available_faces() -> Dict[str, str]:
    """The display faces actually present, by name."""
    return {name: str(BUNDLED_FONT_DIR / filename)
            for name, filename in DISPLAY_FACES.items()
            if (BUNDLED_FONT_DIR / filename).exists()}


def ink_extents(lines: List[str], font_path: Optional[str], font_px: int,
                line_height: int) -> Optional[Tuple[int, int]]:
    """
    Where the drawn glyphs actually sit, as (offset from the draw origin to
    the top of the ink, total ink height).

    Needed because a line box is not the ink in it. drawtext's y is the top
    of the text box, and a line box at 1.25x the point size leaves roughly a
    quarter of its height as leading below the glyphs — so text placed by
    line box alone sits high, with all the slack collecting underneath. A
    reviewer measured that as the title not being centred: 25px above,
    47px below.

    Returns None when the font cannot be measured, so a caller can fall
    back to line-box positioning rather than guessing an offset.
    """
    if not font_path or not lines:
        return None
    try:
        from PIL import ImageFont
        font = ImageFont.truetype(font_path, font_px)
    except Exception:
        return None

    # getbbox is relative to the same origin drawtext uses, so the numbers
    # are directly comparable.
    first = font.getbbox(lines[0])
    last = font.getbbox(lines[-1])
    top = first[1]
    bottom = (len(lines) - 1) * line_height + last[3]
    return top, max(1, bottom - top)


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
