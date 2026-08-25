# agent/heimdall_visualizer.py
"""
Heimdall Visualizer Tool (👁️ - Heimdall / Norse watchman, keeper of keenest sight)
Part of NornPulse: Autonomous Media Engine by Norn Labs (nornlabs.ai)

Heimdall composes a custom vertical cover thumbnail per clip via Gemini's
native image generation (gemini-3-pro-image, 9:16 aspect ratio), grounded
in the same Urðr music_virality_benchmark row Bragi composes its score
from — the mood/genre/energy that makes a hook_type resonate acoustically
is the same signal that should drive its visual mood, so this reuses that
grounding rather than standing up a near-duplicate ClickHouse table for
"visual style benchmarks".

Unlike Bragi's tracks, thumbnails are never cached: each one is grounded
in that specific clip's hook_title, so there's no meaningful reuse across
clips the way there is for a (genre, mood, bpm) combo.
"""

import logging
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from google import genai
from google.genai import types
from agent.api_retry import retry_on_transient

load_dotenv()
logger = logging.getLogger("nornpulse.heimdall")

IMAGE_MODEL = "gemini-3-pro-image"


class HeimdallVisualizer:
    """
    Composes an original 9:16 cover thumbnail per clip via Gemini's native
    image generation, grounded in the mood/genre/energy Urðr associates
    with the clip's hook_type (the same row Bragi grounds its score in).
    """

    def __init__(self):
        api_key = os.getenv("GEMINI_API_KEY")
        # Through the factory so this bills wherever the rest of the
        # pipeline does. Left on AI Studio it would 429 on every clip while
        # the surrounding run succeeded, degrading silently to a video with
        # no cover.
        from agent import genai_client as gc
        self.client, self.model = gc.client_for(IMAGE_MODEL, api_key=api_key)

    def _build_prompt(self, hook_title: str, genre: str, mood: str, energy_level: float) -> str:
        energy_word = (
            "high-energy, dynamic" if energy_level >= 0.7 else
            "moderate-energy" if energy_level >= 0.45 else
            "calm, understated"
        )
        return (
            f"A bold, cinematic vertical cover image evoking the theme: \"{hook_title}\". "
            f"{energy_word} visual mood, in the spirit of {mood} {genre}. "
            f"Dramatic lighting, high contrast, striking composition — no text, no watermark, "
            f"no logos, no captions. Pure visual mood suitable as a custom cover thumbnail for "
            f"a short-form vertical video."
        )

    @retry_on_transient()
    def _generate(self, prompt: str):
        """
        The raw image call, isolated so it can be retried on transient
        errors (503 "high demand", 429, timeouts) without also retrying
        the surrounding prompt-building and file-writing. A real batch
        run lost all three thumbnails to a transient 503; the caller's
        except-branch still handles permanent failure gracefully.
        """
        return self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(aspect_ratio="9:16"),
            ),
        )

    def compose_thumbnail(
        self, clip_id: str, hook_title: str, music_benchmark: dict,
        output_dir: str | Path = "output_clips",
    ) -> Optional[str]:
        """
        Composes a 9:16 cover thumbnail for clip_id, grounded in
        music_benchmark (see UrdrAnalytics.get_top_music_benchmark).
        Returns the local .jpg path, or None if generation fails — callers
        should proceed without a custom thumbnail rather than fail the
        whole clip over an image-generation glitch.
        """
        genre = music_benchmark.get("genre", "cinematic")
        mood = music_benchmark.get("mood", "neutral")
        energy_level = float(music_benchmark.get("energy_level", 0.5))
        prompt = self._build_prompt(hook_title, genre, mood, energy_level)

        try:
            logger.info(f"👁️ Heimdall composing thumbnail for {clip_id} ({genre}/{mood})...")
            response = self._generate(prompt)
            parts = response.candidates[0].content.parts if response.candidates else []
            image_part = next((p for p in parts if getattr(p, "inline_data", None)), None)
            if image_part is None:
                logger.warning(f"Gemini returned no image for clip_id '{clip_id}'.")
                return None

            output_path = Path(output_dir) / f"{clip_id}_thumb.jpg"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(image_part.inline_data.data)
            logger.info(f"✨ Heimdall composed thumbnail: {output_path.name} ({len(image_part.inline_data.data)} bytes)")
            return str(output_path)
        except Exception as e:
            logger.error(f"Heimdall thumbnail composition failed for clip_id '{clip_id}': {e}")
            return None
