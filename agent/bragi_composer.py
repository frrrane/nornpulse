# agent/bragi_composer.py
"""
Bragi Composer Tool (🎵 - Bragi / Norse god of poetry and music)
Part of NornPulse: Autonomous Media Engine by Norn Labs (nornlabs.ai)

Bragi composes original background scores for generated shorts via
Google's Lyria 3 (lyria-3-clip-preview, ~29s stereo MP3 per call), grounded
in Urðr's ClickHouse-tracked correlation between musical attributes
(genre, mood, bpm, energy) and global YouTube Shorts virality per hook
type — the same "reason over real historical data, don't guess" pattern
Verðandi already applies to hook_type selection.

Tracks are cached on disk keyed by (genre, mood, bpm), so repeated hook
types across a session reuse the same composed track instead of paying
for a fresh Lyria call every time.
"""

import base64
import hashlib
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from google import genai
from agent.api_retry import retry_on_transient

load_dotenv()
logger = logging.getLogger("nornpulse.bragi")

# Fixed ~29-30s stereo MP3 per call — Skuld trims/loops it to the clip's
# actual duration at render time (see skuld_renderer.render_vertical_short).
LYRIA_MODEL = "lyria-3-clip-preview"


class BragiComposer:
    """
    Composes original Lyria background scores, grounded in Urðr's
    music_virality_benchmarks ClickHouse table, and caches them on disk by
    (genre, mood, bpm) so repeated hook types reuse the same track instead
    of paying for a fresh Lyria call every time.
    """

    def __init__(self, cache_dir: str | Path = "output_clips/.bragi_cache"):
        api_key = os.getenv("GEMINI_API_KEY")
        self.client = genai.Client(api_key=api_key)
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_key(self, genre: str, mood: str, bpm: int) -> str:
        raw = f"{genre}|{mood}|{bpm}".lower()
        return hashlib.sha1(raw.encode()).hexdigest()[:16]

    def _build_prompt(self, genre: str, mood: str, bpm: int, energy_level: float) -> str:
        energy_word = (
            "high-energy" if energy_level >= 0.7 else
            "mid-energy" if energy_level >= 0.45 else
            "low-energy"
        )
        return (
            f"A {energy_word} instrumental {genre} track, {mood} mood, {bpm} BPM. "
            f"No vocals, no spoken words, no singing — purely instrumental background "
            f"music suitable for underscoring a short-form vertical video. Consistent, "
            f"loopable energy throughout, no jarring silences or drops."
        )

    @retry_on_transient()
    def _compose(self, prompt: str):
        """
        The raw Lyria call, isolated so a transient failure (503 "high
        demand", 429, timeout) is retried rather than silently costing
        the clip its score. Permanent failures still fall through to the
        caller's except-branch, which renders the clip without music.
        """
        return self.client.interactions.create(model=LYRIA_MODEL, input=prompt)

    def compose_track(
        self, hook_type: str, music_benchmark: Dict[str, Any], force_regenerate: bool = False,
    ) -> Optional[str]:
        """
        Composes (or reuses a cached) background track for the given
        hook_type, grounded in the ClickHouse music_virality_benchmark row
        selected for it (see UrdrAnalytics.get_top_music_benchmark).
        Returns the local mp3 path, or None if Lyria generation fails —
        callers should render without music rather than fail the whole
        clip over a music glitch.
        """
        genre = music_benchmark.get("genre", "ambient electronic")
        mood = music_benchmark.get("mood", "neutral")
        bpm = int(music_benchmark.get("bpm", 100))
        energy_level = float(music_benchmark.get("energy_level", 0.5))

        cache_key = self._cache_key(genre, mood, bpm)
        cache_path = self.cache_dir / f"{cache_key}.mp3"
        if cache_path.exists() and not force_regenerate:
            logger.info(f"🎵 Bragi cache hit for {hook_type} ({genre}/{mood}/{bpm}bpm) -> {cache_path.name}")
            return str(cache_path)

        prompt = self._build_prompt(genre, mood, bpm, energy_level)
        try:
            logger.info(f"🎵 Bragi composing new track for {hook_type} ({genre}/{mood}/{bpm}bpm) via Lyria...")
            interaction = self._compose(prompt)
            if not interaction.output_audio or not interaction.output_audio.data:
                logger.warning(f"Lyria returned no audio for hook_type '{hook_type}'.")
                return None
            audio_bytes = base64.b64decode(interaction.output_audio.data)
            cache_path.write_bytes(audio_bytes)
            logger.info(f"✨ Bragi composed and cached track: {cache_path.name} ({len(audio_bytes)} bytes)")
            return str(cache_path)
        except Exception as e:
            logger.error(f"Bragi/Lyria composition failed for hook_type '{hook_type}': {e}")
            return None
