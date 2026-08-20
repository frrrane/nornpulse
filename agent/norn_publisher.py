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
from typing import Optional
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("nornpulse.publisher")

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

            # Attach the video file
            with open(video_path, "rb") as f:
                part = MIMEBase('application', 'octet-stream')
                part.set_payload(f.read())
                encoders.encode_base64(part)
                part.add_header('Content-Disposition', f"attachment; filename= {video_path.name}")
                msg.attach(part)

            # Connect to Gmail SMTP server
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

    def upload_to_youtube_shorts(self, video_path: str | Path, title: str, description: str) -> Optional[str]:
        """
        Publishes a vertical video to YouTube as a Short using the YouTube Data API v3.
        """
        video_path = Path(video_path)
        if not video_path.exists():
            raise FileNotFoundError(f"Video file not found: {video_path}")

        if not Path(self.client_secrets_file).exists():
            logger.error(f"Google OAuth client secrets file not found at {self.client_secrets_file}")
            return None

        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaFileUpload

            SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
            flow = InstalledAppFlow.from_client_secrets_file(self.client_secrets_file, SCOPES)
            credentials = flow.run_local_server(port=8080)
            
            youtube = build("youtube", "v3", credentials=credentials)
            formatted_title = f"{title} #Shorts" if "#Shorts" not in title else title
            
            body = {
                "snippet": {
                    "title": formatted_title,
                    "description": f"{description}\n\nGenerated autonomously by NornPulse (nornlabs.ai)",
                    "tags": ["AI", "NornPulse", "Shorts", "Tech"],
                    "categoryId": "28"
                },
                "status": {
                    "privacyStatus": "public",
                    "selfDeclaredMadeForKids": False
                }
            }

            media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True, mimetype="video/mp4")
            request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

            response = None
            while response is None:
                status, response = request.next_chunk()
                if status:
                    logger.info(f"Uploaded {int(status.progress() * 100)}%.")

            video_id = response.get("id")
            logger.info(f"✨ Successfully published YouTube Short! Video ID: {video_id}")
            return video_id

        except Exception as e:
            logger.error(f"YouTube upload failed: {e}")
            return None