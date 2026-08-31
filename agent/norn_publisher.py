# agent/norn_publisher.py
"""
⚡ NornPulse: Staging & YouTube Publishing Agent (norn_publisher.py)
Norn Labs (nornlabs.ai)

Handles Human-in-the-Loop (HITL) approvals via Gmail (with video attachments)
and programmatic uploads to YouTube Shorts via the YouTube Data API v3.
"""

import os
import html as _html
import smtplib
from urllib.parse import quote
import logging
from pathlib import Path
from typing import Optional, Dict, Any
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from dotenv import load_dotenv

from agent import channels
from agent import publications
from agent import tag_selector as ts

load_dotenv()
logger = logging.getLogger("nornpulse.publisher")

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",  # needed for get_video_statistics
    # The owner-only view: impressions, average view duration, and the
    # audience retention curve. The Data API exposes none of these for
    # anyone's video, including your own, so without this scope the project
    # can only ever see how a clip did and never why.
    #
    # Adding it invalidates existing tokens: scopes are fixed at grant time,
    # so every channel has to go through the OAuth flow again once.
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]
TOKEN_PATH = Path(".credentials/youtube_token.json")


class PublishError(Exception):
    """Raised on any step of the publish flow that fails, with a clear cause."""


class NornPublisher:
    """
    Manages HITL Gmail staging notifications and YouTube Shorts API integration.
    """

    def __init__(self, channel: "channels.Channel | str | None" = None):
        # Which channel this publisher publishes to. Resolved once, at
        # construction, so a single instance can never drift between
        # channels mid-run — and so the token path, the YouTube category
        # and the size band all come from the same decision.
        self.channel = (
            channels.get_channel(channel) if channel is None or isinstance(channel, str)
            else channel
        )
        self.gmail_user = os.getenv("GMAIL_USER")
        self.gmail_password = os.getenv("GMAIL_APP_PASSWORD")
        self.notify_email = os.getenv("NOTIFY_EMAIL") or self.gmail_user
        self.client_secrets_file = "client_secrets.json"
        # Reading public statistics needs no OAuth at all. That matters
        # because this project's OAuth consent screen is in Testing, where
        # Google expires refresh tokens after 7 days — any unattended sync
        # on the OAuth path dies weekly. An API key does not expire.
        self.youtube_api_key = os.getenv("YOUTUBE_API_KEY")

    @property
    def token_path(self) -> Path:
        """This channel's OAuth token. Never shared between channels."""
        return self.channel.resolve_token_path()

    # Fields surfaced in the staging email, in the order a reviewer wants
    # them: what the clip claims, then how the system chose to treat it.
    # Anything absent from the clip dict is simply omitted rather than
    # rendered as "None", so an older/partial record still emails cleanly.
    _REVIEW_FIELDS = [
        ("social_caption", "Social caption"),
        ("hook_type", "Hook type"),
        ("hook_rank", "Hook rank (Urðr)"),
        ("start_time", "Source in"),
        ("end_time", "Source out"),
        ("crop_mode", "Crop"),
        ("motion_effect", "Motion"),
        ("color_grade", "Colour grade"),
        ("caption_language", "Caption language"),
        ("music_genre", "Music genre"),
        ("music_mood", "Music mood"),
        # Trend-generated clips have almost none of the fields above: there
        # is no source video, so no cut range, no crop and no colour grade.
        # Reviewed without these the email is nearly empty, which tells a
        # reviewer nothing about the one thing they are being asked to judge.
        ("trend_topic", "Trending topic"),
        ("trend_videos", "Trending videos carrying it"),
        ("angle", "Angle (model judgement)"),
        ("video_prompt", "Prompt given to the generator"),
        ("footage_provider", "Footage from"),
        ("hook_burned", "Burned-in hook"),
        ("forecast_p50", "Forecast reach (p50)"),
        ("forecast_range", "Forecast range (p10-p90)"),
        ("owner_retention", "This hook type's real retention"),
        ("audience_reaction", "Audience reaction (sampled frames)"),
        ("tags", "Tags"),
        # A reviewer approving a clip should see what was checked on their
        # behalf, and just as importantly what was not.
        ("rights_check", "Rights check"),
        ("rights_not_checked", "Rights check does NOT cover"),
    ]

    # A reply-based decision needs no hosting and no public callback URL,
    # so it works identically before and after the app is deployed. The
    # subject is the machine-readable part; check_approvals.py parses it
    # and treats everything above the marker line as the comment.
    REPLY_MARKER = "--- write your comment above this line ---"

    def _decision_mailto(self, clip_id: str, decision: str) -> str:
        subject = f"[NornPulse] {decision.upper()} {clip_id}"
        body = f"\n\n{self.REPLY_MARKER}\nDecision: {decision.upper()}\nClip: {clip_id}\n"
        return (f"mailto:{self.notify_email}?subject={quote(subject)}&body={quote(body)}")

    @staticmethod
    def _review_rows(clip: Dict[str, Any]) -> list[tuple[str, str]]:
        rows = []
        for key, label in NornPublisher._REVIEW_FIELDS:
            value = clip.get(key)
            if value is None or value == "" or value == []:
                continue
            # A list rendered with str() shows its Python repr, brackets and
            # quotes included, which looks like a bug in an email.
            if isinstance(value, (list, tuple)):
                value = ", ".join(str(v) for v in value)
            rows.append((label, str(value)))
        flags = [
            label for key, label in (
                ("has_subtitles", "kinetic subtitles"),
                ("has_bragi_score", "original Bragi score"),
                ("has_narration", "Mímir narration"),
            ) if clip.get(key)
        ]
        if flags:
            rows.append(("Includes", ", ".join(flags)))
        if clip.get("is_top_tier_hook"):
            rows.append(("Top-tier hook", "yes — matches Urðr's best-performing hook type"))
        return rows

    def send_gmail_staged_approval(
        self,
        clip_id: str,
        title: str,
        virality: float,
        video_path: str,
        clip: Optional[Dict[str, Any]] = None,
        thumbnail_path: Optional[str | Path] = None,
    ) -> bool:
        """
        Sends an email via Gmail containing clip metadata and the rendered
        9:16 short as an attachment.

        `clip`, if given, is the full clip record from Verðandi — the
        social caption, hook type/rank, cut range, and the crop / motion /
        colour-grade treatment Urðr's benchmarks selected. Reviewing a
        clip without those means judging the output blind to what the
        system decided and why, so they are rendered as a table in the
        body. Every field is optional: a partial record just produces a
        shorter table.

        `thumbnail_path` (Heimdall's cover) is embedded inline so the
        cover can be judged in the mail client without downloading the
        attachment; it falls back to clip["thumbnail_path"].

        Returns True on send, False on any failure — this is a
        notification, so a mail problem must never take down a pipeline
        run that has already produced a good clip.
        """
        if not self.gmail_user or not self.gmail_password:
            logger.warning("Gmail credentials missing. Skipping email notification.")
            return False

        video_path = Path(video_path)
        if not video_path.exists():
            logger.error(f"Cannot email staging alert; video path does not exist: {video_path}")
            return False

        clip = clip or {}
        if thumbnail_path is None:
            thumbnail_path = clip.get("thumbnail_path")
        thumb = Path(thumbnail_path) if thumbnail_path else None
        if thumb and not thumb.exists():
            logger.warning(f"Thumbnail not found, emailing without an inline cover: {thumb}")
            thumb = None

        rows = self._review_rows(clip)

        try:
            # mixed -> [ related -> [ alternative -> [text, html], inline image ], video ]
            outer = MIMEMultipart("mixed")
            outer["From"] = self.gmail_user
            outer["To"] = self.notify_email
            outer["Subject"] = f"🎬 [NornPulse Staged] {title} (Virality: {virality}/100)"

            related = MIMEMultipart("related")
            alternative = MIMEMultipart("alternative")

            plain = [
                "NornPulse has generated and staged a new vertical short for your review.",
                "",
                f"Clip ID:        {clip_id}",
                f"Hook title:     {title}",
                f"Virality score: {virality}/100",
            ]
            plain += [f"{label + ':':<16}{value}" for label, value in rows]
            plain += [
                "",
                "Review the attached video, then reply with your decision:",
                f"  APPROVE -> reply with subject: [NornPulse] APPROVE {clip_id}",
                f"  REJECT  -> reply with subject: [NornPulse] REJECT {clip_id}",
                "Anything you write at the top of the reply is recorded as your comment.",
                "You can also decide in the app dashboard.",
            ]
            alternative.attach(MIMEText("\n".join(plain), "plain", "utf-8"))

            # hook_title / social_caption are model-authored free text, so
            # they must be escaped before being interpolated into the HTML
            # body — an unescaped & or < would otherwise mangle the email.
            esc = _html.escape
            table = "".join(
                f'<tr><td style="padding:4px 14px 4px 0;color:#666;white-space:nowrap;">{esc(label)}</td>'
                f'<td style="padding:4px 0;color:#111;">{esc(value)}</td></tr>'
                for label, value in rows
            )
            approve_url = self._decision_mailto(clip_id, "approve")
            reject_url = self._decision_mailto(clip_id, "reject")
            cover = (
                '<img src="cid:heimdall_cover" alt="Heimdall cover" '
                'style="width:180px;border-radius:10px;display:block;margin:0 0 18px 0;">'
                if thumb else ""
            )
            html = f"""\
<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:560px;color:#111;">
  <p style="margin:0 0 4px 0;color:#666;font-size:13px;">⚡ NornPulse staged a vertical short for review</p>
  <h2 style="margin:0 0 2px 0;font-size:21px;">{esc(title)}</h2>
  <p style="margin:0 0 16px 0;color:#666;font-size:13px;">{esc(clip_id)} &middot; predicted virality <strong style="color:#111;">{virality}/100</strong></p>
  {cover}
  <table style="border-collapse:collapse;font-size:14px;">{table}</table>
  <div style="margin:22px 0 10px 0;">
    <a href="{approve_url}" style="background:#1a7f37;color:#fff;text-decoration:none;padding:10px 20px;border-radius:7px;font-size:14px;font-weight:600;display:inline-block;margin-right:8px;">✅ Approve &amp; publish</a>
    <a href="{reject_url}" style="background:#b42318;color:#fff;text-decoration:none;padding:10px 20px;border-radius:7px;font-size:14px;font-weight:600;display:inline-block;">🗑️ Reject</a>
  </div>
  <p style="margin:8px 0 0 0;font-size:12.5px;color:#666;line-height:1.5;">
    Either button opens a reply — type your comment at the top and send. Leave the subject line alone; it carries the decision.
    Comments are recorded either way, so a rejection explains itself later. You can also decide in the dashboard.
  </p>
</div>"""
            alternative.attach(MIMEText(html, "html", "utf-8"))
            related.attach(alternative)

            if thumb:
                from email.mime.image import MIMEImage
                img = MIMEImage(thumb.read_bytes())
                img.add_header("Content-ID", "<heimdall_cover>")
                img.add_header("Content-Disposition", "inline", filename=thumb.name)
                related.attach(img)

            outer.attach(related)

            part = MIMEBase("application", "octet-stream")
            part.set_payload(video_path.read_bytes())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment", filename=video_path.name)
            outer.attach(part)

            server = smtplib.SMTP("smtp.gmail.com", 587)
            server.starttls()
            server.login(self.gmail_user, self.gmail_password)
            server.sendmail(self.gmail_user, self.notify_email, outer.as_string())
            server.quit()

            logger.info(f"Successfully sent Gmail staging notification with attachment for {clip_id}.")
            return True

        except Exception as e:
            logger.error(f"Failed to send Gmail staging email: {e}")
            return False

    def _get_youtube_credentials(self):
        """
        Loads cached OAuth credentials from this channel's token path,
        refreshing if
        expired. Only falls back to the interactive browser consent flow
        (`run_local_server`) if no usable cached token exists — so a
        headless/cron/remote run doesn't require a human at a browser on
        every single publish, only once per machine.
        """
        token_path = self.token_path
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow

        creds = None
        if token_path.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
            except Exception as e:
                logger.warning(f"Cached YouTube token unreadable, will re-authenticate: {e}")
                creds = None

        if creds and creds.valid:
            return creds

        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                token_path.parent.mkdir(parents=True, exist_ok=True)
                token_path.write_text(creds.to_json())
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
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json())
        return creds

    def _tags_for(self, clip: Dict[str, Any] | None, title: str, description: str):
        """
        Grounded tags for this upload, with their provenance.

        Every failure mode here degrades to the structural tag rather than
        raising: the trending snapshot is a nice-to-have, and a clip that
        rendered successfully should never fail to publish because a
        benchmark read timed out.
        """
        if not clip:
            return list(ts.STRUCTURAL_TAGS), []
        try:
            from agent import trending_ingest as ti
            trending = ti.top_tags(limit=200)
        except Exception as e:
            logger.warning(f"No trending snapshot for tag grounding: {e}")
            trending = None
        try:
            tags, decisions = ts.select_tags(
                clip, trending=trending, extra_text=f"{title} {description}",
                profile_hints=self.channel.profile.topic_hints)
        except Exception as e:
            logger.warning(f"Tag selection failed, falling back to structural: {e}")
            return list(ts.STRUCTURAL_TAGS), []
        measured = sum(1 for d in decisions if d.level == "measured")
        logger.info(f"Tags for upload ({len(tags)}, {measured} measured): {tags}")
        return tags or list(ts.STRUCTURAL_TAGS), decisions

    def upload_to_youtube_shorts(
        self, video_path: str | Path, title: str, description: str, privacy_status: str = "private",
        thumbnail_path: str | Path | None = None, clip: Dict[str, Any] | None = None,
        source: str = "pipeline",
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

        thumbnail_path, if given (Heimdall's generated cover image), is
        set via thumbnails().set() after the upload succeeds. Setting a
        custom thumbnail via the API requires the channel to be phone-
        verified — on an unverified channel this call 403s. That failure
        is logged and swallowed rather than raised, since the video
        itself already published successfully at that point; losing the
        custom thumbnail shouldn't be treated as the whole publish
        failing.

        clip, if given, is the clip's metadata. It is used to choose tags
        that actually describe this video (see agent.tag_selector) instead
        of the fixed four this method used to send on every upload. Without
        it the structural fallback is used, because shipping tags derived
        from nothing is worse than shipping none.

        source marks whether this pipeline generated the clip or merely
        published it. External video is recorded so its forecast can be
        graded, but must not count toward NornPulse's own track record.
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
            tags, tag_decisions = self._tags_for(clip, title, description)

            body = {
                "snippet": {
                    "title": formatted_title,
                    "description": f"{description}\n\nGenerated autonomously by NornPulse (nornlabs.ai)",
                    "tags": tags,
                    "categoryId": self.channel.profile.category_id,
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

            thumbnail_set = False
            if thumbnail_path and Path(thumbnail_path).exists():
                try:
                    youtube.thumbnails().set(
                        videoId=video_id,
                        media_body=MediaFileUpload(str(thumbnail_path), mimetype="image/jpeg"),
                    ).execute()
                    thumbnail_set = True
                    logger.info(f"👁️ Set cover image (Heimdall) as custom thumbnail for {video_id}.")
                except Exception as e:
                    logger.warning(
                        f"Could not set custom thumbnail for {video_id} (channel may not be phone-verified "
                        f"for custom thumbnails): {e}"
                    )

            if clip:
                publications.record_publication(
                    clip_id=clip.get("clip_id", ""),
                    youtube_video_id=video_id,
                    channel=self.channel,
                    tags=tags,
                    decisions=tag_decisions,
                    hook_type=clip.get("hook_type", ""),
                    source=source,
                )

            return {"video_id": video_id, "url": url, "privacy_status": privacy_status,
                    "thumbnail_set": thumbnail_set, "tags": tags}

        except PublishError:
            raise
        except Exception as e:
            logger.error(f"YouTube upload failed: {e}")
            raise PublishError(f"YouTube upload failed: {e}") from e

    def _youtube_for_reading(self):
        """
        A client for public reads, preferring the API key.

        The key path needs no user consent, never expires, and works
        unattended. OAuth is only a fallback for when no key is configured
        — it still reads fine, it just cannot be scheduled reliably.
        """
        from googleapiclient.discovery import build

        if self.youtube_api_key:
            return build("youtube", "v3", developerKey=self.youtube_api_key)
        return build("youtube", "v3", credentials=self._get_youtube_credentials())

    def get_video_statistics(self, video_id: str) -> Dict[str, Any]:
        """
        Fetches current public statistics for an already-published video —
        the ground truth used to cross-validate NornPulse's predicted
        virality_score / 3s-retention against what actually happened.

        Uses the API key when one is set, so this can run on a schedule
        without a human re-authorising every week.
        """
        try:
            youtube = self._youtube_for_reading()
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