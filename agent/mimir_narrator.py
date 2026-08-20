# agent/mimir_narrator.py
"""
Mímir Narrator Tool (🗣️ - Mímir / Norse being of wisdom and counsel)
Part of NornPulse: Autonomous Media Engine by Norn Labs (nornlabs.ai)

Mímir generates an AI voiceover via Gemini's native TTS
(gemini-3.1-flash-tts-preview) for two situations, both handled by the
same narrate() call — only the script text and the reason differ:

  1. Fill silence — vision-mode clips (no transcript) have no dialogue to
     draw from, so Verðandi passes the clip's own hook_title as the script.
  2. Enhance — a transcript exists, but the clip's sliced audio measured
     too quiet to reliably follow (see
     skuld_renderer.measure_audio_mean_volume /
     NARRATION_FALLBACK_VOLUME_THRESHOLD_DB). Verðandi passes the actual
     transcript text for that window as the script, so Mímir reads real
     dialogue back clearly rather than substituting a synthetic line.
"""

import logging
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
logger = logging.getLogger("nornpulse.mimir")

TTS_MODEL = "gemini-3.1-flash-tts-preview"
# The model returns raw PCM (no container) — confirmed live: mono, 16-bit
# signed little-endian, 24kHz. FFmpeg is told this shape explicitly via
# -f s16le -ar 24000 -ac 1 rather than guessing from a file extension.
PCM_SAMPLE_RATE = 24000
PCM_CHANNELS = 1


class MimirNarrator:
    """Generates a spoken-word narration track via Gemini TTS."""

    def __init__(self):
        api_key = os.getenv("GEMINI_API_KEY")
        self.client = genai.Client(api_key=api_key)

    def _pick_voice(self, energy_level: float) -> str:
        """
        Picks a prebuilt Gemini voice by energy_level (from the same
        music_virality_benchmarks row Bragi/Heimdall ground their own
        output in) — an approximate mapping, not a validated pairing;
        Gemini's prebuilt voices aren't formally categorized by "energy".
        """
        if energy_level >= 0.7:
            return "Puck"  # brighter, more energetic prebuilt voice
        if energy_level >= 0.45:
            return "Kore"  # neutral, clear
        return "Charon"  # calmer, lower register

    def narrate(
        self, clip_id: str, script_text: str, energy_level: float = 0.5,
        output_dir: str | Path = "output_clips",
    ) -> Optional[str]:
        """
        Generates a narration track for script_text and writes it as a
        WAV file at {output_dir}/{clip_id}_narration.wav. Returns the
        local path, or None if generation fails — callers should render
        without narration rather than fail the whole clip over it.
        """
        script_text = (script_text or "").strip()
        if not script_text:
            logger.warning(f"No script text to narrate for clip_id '{clip_id}'.")
            return None

        voice_name = self._pick_voice(energy_level)
        try:
            logger.info(f"🗣️ Mímir narrating clip_id '{clip_id}' (voice={voice_name}): \"{script_text[:80]}\"")
            response = self.client.models.generate_content(
                model=TTS_MODEL,
                contents=script_text,
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice_name)
                        )
                    ),
                ),
            )
            parts = response.candidates[0].content.parts if response.candidates else []
            audio_part = next((p for p in parts if getattr(p, "inline_data", None)), None)
            if audio_part is None:
                logger.warning(f"Gemini returned no audio for clip_id '{clip_id}'.")
                return None

            pcm_path = Path(output_dir) / f"{clip_id}_narration.pcm"
            wav_path = Path(output_dir) / f"{clip_id}_narration.wav"
            pcm_path.parent.mkdir(parents=True, exist_ok=True)
            pcm_path.write_bytes(audio_part.inline_data.data)

            import subprocess
            result = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-f", "s16le", "-ar", str(PCM_SAMPLE_RATE), "-ac", str(PCM_CHANNELS),
                    "-i", str(pcm_path),
                    str(wav_path),
                ],
                capture_output=True, text=True,
            )
            pcm_path.unlink(missing_ok=True)
            if result.returncode != 0:
                logger.error(f"Failed to wrap Mímir's raw PCM into a WAV for '{clip_id}': {result.stderr}")
                return None

            logger.info(f"✨ Mímir composed narration: {wav_path.name}")
            return str(wav_path)
        except Exception as e:
            logger.error(f"Mímir narration failed for clip_id '{clip_id}': {e}")
            return None
