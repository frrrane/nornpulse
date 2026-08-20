# agent/norn_publisher.py
"""
⚡ NornPulse: Staging & YouTube Publishing Agent (norn_publisher.py)
Norn Labs (nornlabs.ai)

Handles Human-in-the-Loop (HITL) approvals via Gmail (with video attachments)
and programmatic uploads to YouTube Shorts via the YouTube Data API v3.
"""

import os
import smtplib
import logging
from pathlib import Path
from typing import Optional, Dict, Any
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("nornpulse.publisher")

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",  # needed for get_video_statistics
]
TOKEN_PATH = Path(".credentials/youtube_token.json")


class PublishError(Exception):
    """Raised on any step of the publish flow that fails, with a clear cause."""


class NornPublisher:
    """
    Manages HITL Gmail staging notifications and YouTube Shorts API integration.
    """

    def __init__(self):
        self.gmail_user = os.getenv("GMAIL_USER")
        self.gmail_password = os.getenv("GMAIL_APP_PASSWORD")
        self.notify_email = os.getenv("NOTIFY_EMAIL") or self.gmail_user
        self.client_secrets_file = "client_secrets.json"

    def send_gmail_staged_approval(self, clip_id: str, title: str, virality: float, video_path: str) -> bool:
        """
        Sends an email via Gmail containing clip metadata and the rendered 9:16 short as an attachment.
        """
        if not self.gmail_user or not self.gmail_password:
            logger.warning("Gmail credentials missing. Skipping email notification.")
            return False

        video_path = Path(video_path)
        if not video_path.exists():
            logger.error(f"Cannot email staging alert; video path does not exist: {video_path}")
            return False

        try:
            msg = MIMEMultipart()
            msg['From'] = self.gmail_user
            msg['To'] = self.notify_email
            msg['Subject'] = f"🎬 [NornPulse Staged] {title} (Virality: {virality}/100)"

            body = (
                f"NornPulse has generated and staged a new vertical short for your review.\n\n"
                f"• Clip ID: {clip_id}\n"
                f"• Hook Title: {title}\n"
                f"• Virality Score: {virality}/100\n\n"
                f"Review the attached video file. If approved, you can execute the upload script or deploy via the app dashboard."
            )
            msg.attach(MIMEText(body, 'plain'))

            with open(video_path, "rb") as f:
                part = MIMEBase('application', 'octet-stream')
                part.set_payload(f.read())
                encoders.encode_base64(part)
                part.add_header('Content-Disposition', f"attachment; filename= {video_path.name}")
                msg.attach(part)

            server = smtplib.SMTP('smtp.gmail.com', 587)
            server.starttls()
            server.login(self.gmail_user, self.gmail_password)
            server.sendmail(self.gmail_user, self.notify_email, msg.as_string())
            server.quit()

            logger.info(f"Successfully sent Gmail staging notification with attachment for {clip_id}.")
            return True

        except Exception as e:
            logger.error(f"Failed to send Gmail staging email: {e}")
            return False

    def _get_youtube_credentials(self):
        """
        Loads cached OAuth credentials from TOKEN_PATH, refreshing if
        expired. Only falls back to the interactive browser consent flow
        (`run_local_server`) if no usable cached token exists — so a
        headless/cron/remote run doesn't require a human at a browser on
        every single publish, only once per machine.
        """
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow

        creds = None
        if TOKEN_PATH.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
            except Exception as e:
                logger.warning(f"Cached YouTube token unreadable, will re-authenticate: {e}")
                creds = None

        if creds and creds.valid:
            return creds

        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
                TOKEN_PATH.write_text(creds.to_json())
                return creds
            except Exception as e:
                logger.warning(f"Failed to refresh cached YouTube token, re-authenticating: {e}")

        if not Path(self.client_secrets_file).exists():
            raise PublishError(
                f"Google OAuth client secrets file not found at '{self.client_secrets_file}'. "
                f"Download it from Google Cloud Console (APIs & Services -> Credentials) "
                f"and place it at the repo root."
            )

        # Interactive fallback — only reached on first-ever auth or a
        # revoked/corrupted token. This requires a local browser and an
        # open port 8080, so it will not work unattended on a remote host.
        flow = InstalledAppFlow.from_client_secrets_file(self.client_secrets_file, SCOPES)
        creds = flow.run_local_server(port=8080)
        TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_PATH.write_text(creds.to_json())
        return creds

    def upload_to_youtube_shorts(
        self, video_path: str | Path, title: str, description: str, privacy_status: str = "private"
    ) -> Dict[str, Any]:
        """
        Publishes a vertical video to YouTube as a Short using the YouTube
        Data API v3. privacy_status must be 'private', 'unlisted', or
        'public' — defaults to 'private' so nothing goes live by accident.
        'private' videos are visible only to accounts explicitly added as
        viewers in YouTube Studio, the closest equivalent YouTube has to
        internal testing. Returns a dict with video_id, url, and
        privacy_status on success. Raises PublishError with a specific
        cause on failure.
        """
        if privacy_status not in ("private", "unlisted", "public"):
            raise PublishError(f"Invalid privacy_status '{privacy_status}'; must be private, unlisted, or public.")

        video_path = Path(video_path)
        if not video_path.exists():
            raise PublishError(f"Video file not found: {video_path}")

        try:
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaFileUpload

            credentials = self._get_youtube_credentials()
            youtube = build("youtube", "v3", credentials=credentials)
            formatted_title = f"{title} #Shorts" if "#Shorts" not in title else title

            body = {
                "snippet": {
                    "title": formatted_title,
                    "description": f"{description}\n\nGenerated autonomously by NornPulse (nornlabs.ai)",
                    "tags": ["AI", "NornPulse", "Shorts", "Tech"],
                    "categoryId": "28",
                },
                "status": {
                    "privacyStatus": privacy_status,
                    "selfDeclaredMadeForKids": False,
                },
            }

            media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True, mimetype="video/mp4")
            request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

            response = None
            while response is None:
                status, response = request.next_chunk()
                if status:
                    logger.info(f"Uploaded {int(status.progress() * 100)}%.")

            video_id = response.get("id")
            if not video_id:
                raise PublishError(f"YouTube API returned no video id. Raw response: {response}")

            url = f"https://youtube.com/shorts/{video_id}"
            privacy_status = response.get("status", {}).get("privacyStatus", "unknown")
            logger.info(f"✨ Successfully published YouTube Short! {url} (privacy: {privacy_status})")
            return {"video_id": video_id, "url": url, "privacy_status": privacy_status}

        except PublishError:
            raise
        except Exception as e:
            logger.error(f"YouTube upload failed: {e}")
            raise PublishError(f"YouTube upload failed: {e}") from e

    def get_video_statistics(self, video_id: str) -> Dict[str, Any]:
        """
        Fetches current public statistics for an already-published video —
        the ground truth used to cross-validate NornPulse's predicted
        virality_score / 3s-retention against what actually happened.
        """
        try:
            from googleapiclient.discovery import build

            credentials = self._get_youtube_credentials()
            youtube = build("youtube", "v3", credentials=credentials)
            response = youtube.videos().list(part="statistics,status", id=video_id).execute()

            items = response.get("items", [])
            if not items:
                raise PublishError(f"No video found for id '{video_id}' (deleted, private, or wrong account?).")

            stats = items[0].get("statistics", {})
            return {
                "video_id": video_id,
                "view_count": int(stats.get("viewCount", 0)),
                "like_count": int(stats.get("likeCount", 0)),
                "comment_count": int(stats.get("commentCount", 0)),
            }
        except PublishError:
            raise
        except Exception as e:
            logger.error(f"Failed to fetch statistics for {video_id}: {e}")
            raise PublishError(f"Failed to fetch statistics for {video_id}: {e}") from e