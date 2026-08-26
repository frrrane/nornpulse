# agent/norn_cron.py
"""
⚡ NornPulse: Scheduled staging (norn_cron.py)
Norn Labs (nornlabs.ai)

Manages automated ingestion, AI segment selection via Gemini, Skuld rendering,
metadata generation, and Gmail/HITL staging dispatch.

Status: kept deliberately, not yet wired
------------------------------------------
Nothing imports this today. It was written against `daemon.py`, which has
since been deleted, and an over-engineering audit flagged the whole module
as dead — correctly, on the evidence. It survives because scheduled
*staging* is still wanted: the piece worth automating is putting a clip in
front of a human on a timer, not publishing on one.

The distinction matters and is the reason this is unwired rather than
running. Everything here stops at `send_gmail_staged_approval`; nothing in
it uploads. A scheduler that stages is a scheduler that fills a review
queue, which is safe to be wrong. A scheduler that publishes is not, and
this project's rule is that a pipeline stage gets automated only once its
output is being approved consistently in human review — which the trend
loop's output is not yet.

The class name still says Daemon and the old flow it orchestrates predates
the trend loop, so wiring this up means rewriting the body against
`trend_publish.py --stage`, not calling it as it stands.
"""

import os
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("nornpulse.cron")

class NornCronDaemon:
    """
    Core orchestrator that runs the end-to-end NornPulse pipeline.
    """

    def __init__(self, output_dir: str | Path = "output_clips"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.gemini_api_key = os.getenv("GEMINI_API_KEY")

    def generate_ai_metadata(self, clip_id: str, transcript_snippet: str) -> Dict[str, Any]:
        """
        Uses Gemini to generate viral YouTube Shorts titles, descriptions, and tags.
        Falls back to rule-based defaults if the API key is missing or fails.
    """
        default_metadata = {
            "title": f"The AI Reality Check #{clip_id} #Shorts",
            "description": "Generated autonomously by NornPulse (nornlabs.ai). Real-time insights and tech breakdowns.",
            "tags": ["AI", "Tech", "NornPulse", "Shorts"]
        }

        if not self.gemini_api_key or self.gemini_api_key.startswith("your_"):
            logger.warning("Valid Gemini API key missing. Using fallback metadata.")
            return default_metadata

        try:
            import google.generativeai as genai
            genai.configure(api_key=self.gemini_api_key)
            # Using current flash model reference
            model = genai.GenerativeModel("gemini-2.5-flash")
            
            prompt = (
                f"Based on this video transcript snippet, generate a viral YouTube Short title (under 60 characters with emojis), "
                f"a compelling description, and 4 comma-separated tags.\n\n"
                f"Transcript snippet:\n{transcript_snippet}\n\n"
                f"Return ONLY valid JSON with keys: 'title', 'description', 'tags' (list of strings)."
            )
            
            response = model.generate_content(prompt)
            clean_text = response.text.strip().replace("```json", "").replace("```", "")
            data = json.loads(clean_text)
            return {
                "title": data.get("title", default_metadata["title"]),
                "description": data.get("description", default_metadata["description"]),
                "tags": data.get("tags", default_metadata["tags"])
            }
        except Exception as e:
            logger.error(f"Failed to generate AI metadata via Gemini: {e}")
            return default_metadata

    def run_pipeline_iteration(self, video_path: str | Path, transcript_path: str | Path) -> Optional[str]:
        """
        Executes a full pipeline iteration: reads transcript, renders short,
        generates AI metadata, saves sidecar JSON, and dispatches via Gmail publisher.
        """
        from agent.skuld_renderer import SkuldRenderer
        from agent.norn_publisher import NornPublisher

        video_path = Path(video_path)
        transcript_path = Path(transcript_path)

        if not video_path.exists() or not transcript_path.exists():
            logger.error("Input video or transcript path does not exist.")
            return None

        logger.info("🚀 Starting automated NornPulse pipeline iteration...")
        
        # Read transcript text
        transcript_text = transcript_path.read_text(encoding="utf-8")
        
        # For test/demo purposes, pick a high-impact window or dynamic slice
        clip_id = f"clip_{os.urandom(2).hex()}"
        start_time = "00:00"
        end_time = "00:15"
        
        # 1. Render Short via Skuld
        renderer = SkuldRenderer(output_dir=self.output_dir)
        render_res = renderer.render_vertical_short(
            input_video_path=video_path,
            start_time=start_time,
            end_time=end_time,
            clip_id=clip_id,
            crop_mode="center_crop",
            hook_banner_text="THE AI REALITY CHECK",
            transcript_text=transcript_text
        )

        rendered_mp4 = Path(render_res["output_video_path"])

        # 2. Generate AI Metadata
        logger.info("🧠 Consulting Gemini for high-retention metadata...")
        metadata = self.generate_ai_metadata(clip_id, transcript_text[:500])

        # 3. Save Sidecar JSON Metadata for UI and Publisher sync
        sidecar_path = self.output_dir / f"{clip_id}_metadata.json"
        with open(sidecar_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)

        # 4. Dispatch Gmail HITL Staging Notification
        publisher = NornPublisher()
        success = publisher.send_gmail_staged_approval(
            clip_id=clip_id,
            title=metadata["title"],
            virality=94.5, # Mock score or dynamic score from Urðr analytics
            video_path=rendered_mp4
        )

        if success:
            logger.info(f"✨ Pipeline iteration complete. Staged {clip_id} successfully!")
            return clip_id
        else:
            logger.error("Pipeline finished rendering, but Gmail notification failed.")
            return None

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    cron = NornCronDaemon()
    # Test execution using sample assets if available
    sample_vid = Path("sample_data/sample_source.mp4")
    sample_sub = Path("sample_data/sample_transcript.txt")
    if sample_vid.exists() and sample_sub.exists():
        cron.run_pipeline_iteration(sample_vid, sample_sub)
    else:
        print("Place a sample source video and transcript in sample_data/ to test via cron.")