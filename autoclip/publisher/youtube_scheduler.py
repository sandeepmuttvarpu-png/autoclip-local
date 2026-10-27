"""
YouTube scheduled publishing client for AutoClip-Local shorts.
Uploads shorts with scheduled release dates using the Google YouTube Data API v3.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import time
from typing import List, Optional

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_DIR = Path.home() / ".config" / "autoclip"
DEFAULT_CLIENT_SECRETS = DEFAULT_CONFIG_DIR / "youtube_client_secret.json"
DEFAULT_TOKEN_PATH = DEFAULT_CONFIG_DIR / "youtube_token.json"
YOUTUBE_UPLOAD_SCOPE = ["https://www.googleapis.com/auth/youtube.upload"]


@dataclass
class ScheduledUploadResult:
    video_id: str
    url: str
    title: str
    publish_at: str
    privacy_status: str = "private"

    def to_dict(self) -> dict:
        return {
            "video_id": self.video_id,
            "url": self.url,
            "title": self.title,
            "publish_at": self.publish_at,
            "privacy_status": self.privacy_status,
        }


class YouTubePublisher:
    """Manages YouTube Data API authentication and scheduled video uploads."""

    def __init__(
        self,
        client_secrets_file: Path = DEFAULT_CLIENT_SECRETS,
        token_file: Path = DEFAULT_TOKEN_PATH,
    ):
        self.client_secrets_file = Path(client_secrets_file).expanduser()
        self.token_file = Path(token_file).expanduser()
        self._service = None

    def get_service(self):
        """Authenticate and return the YouTube v3 Resource object."""
        if self._service is not None:
            return self._service

        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
        except ImportError as e:
            raise ImportError(
                "Missing Google API libraries. Please install via: "
                "pip install google-api-python-client google-auth-oauthlib google-auth-httplib2"
            ) from e

        creds = None
        if self.token_file.exists():
            try:
                creds = Credentials.from_authorized_user_file(
                    str(self.token_file), YOUTUBE_UPLOAD_SCOPE
                )
            except Exception as e:
                logger.warning(f"Failed to load cached credentials from {self.token_file}: {e}")

        # Refresh or run auth flow
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                logger.info("Refreshing expired YouTube OAuth2 token...")
                creds.refresh(Request())
            else:
                if not self.client_secrets_file.exists():
                    raise FileNotFoundError(
                        f"YouTube OAuth client secrets file not found at: {self.client_secrets_file}\n"
                        "To enable YouTube uploads:\n"
                        "1. Create a project in Google Cloud Console.\n"
                        "2. Enable YouTube Data API v3.\n"
                        "3. Download OAuth 2.0 Desktop Client JSON.\n"
                        f"4. Save to: {self.client_secrets_file}"
                    )

                logger.info(f"Initiating OAuth flow using {self.client_secrets_file}...")
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self.client_secrets_file), YOUTUBE_UPLOAD_SCOPE
                )
                creds = flow.run_local_server(port=0)

            # Cache the token
            self.token_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.token_file, "w", encoding="utf-8") as token_out:
                token_out.write(creds.to_json())
            logger.info(f"Saved refreshed YouTube token to {self.token_file}")

        self._service = build("youtube", "v3", credentials=creds)
        return self._service

    @staticmethod
    def format_publish_at(dt_or_iso: str) -> str:
        """
        Validate and format timestamp into RFC 3339 / ISO 8601 UTC string:
        e.g., 2026-10-28T18:00:00Z
        """
        dt_or_iso = dt_or_iso.strip()
        # Parse if given date or datetime string
        try:
            if "T" in dt_or_iso:
                clean_str = dt_or_iso.replace("Z", "+00:00")
                parsed = datetime.fromisoformat(clean_str)
            else:
                parsed = datetime.strptime(dt_or_iso, "%Y-%m-%d %H:%M:%S")
            # Convert to UTC
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            else:
                parsed = parsed.astimezone(timezone.utc)
            return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception as e:
            raise ValueError(
                f"Invalid publish_at timestamp format '{dt_or_iso}'. "
                f"Must be ISO 8601 UTC, e.g. '2026-10-28T18:00:00Z'. Error: {e}"
            )

    def upload_scheduled_short(
        self,
        video_path: Path,
        title: str,
        description: str,
        publish_at: str,
        tags: Optional[List[str]] = None,
        category_id: str = "28",  # 28 = Science & Technology
        notify_subscribers: bool = True,
    ) -> ScheduledUploadResult:
        """
        Resumable upload of a video to YouTube with scheduled publish time.
        In YouTube v3 API, scheduled videos must have privacyStatus='private'.
        """
        from googleapiclient.http import MediaFileUpload
        from googleapiclient.errors import HttpError

        video_path = Path(video_path).resolve()
        if not video_path.exists():
            raise FileNotFoundError(f"Video file to upload does not exist: {video_path}")

        rfc3339_publish_at = self.format_publish_at(publish_at)

        # Standard shorts tags
        effective_tags = list(tags or [])
        if "shorts" not in [t.lower() for t in effective_tags]:
            effective_tags.append("shorts")
        if "#shorts" not in title.lower() and "#shorts" not in description.lower():
            description = f"{description}\n\n#Shorts #AutoClip"

        body = {
            "snippet": {
                "title": title[:100],  # YouTube title limit
                "description": description[:5000],
                "tags": effective_tags,
                "categoryId": category_id,
            },
            "status": {
                "privacyStatus": "private",  # REQUIRED by YouTube for publishAt scheduling
                "publishAt": rfc3339_publish_at,
                "selfDeclaredMadeForKids": False,
            },
        }

        service = self.get_service()
        # 4MB chunks for resumable upload
        media = MediaFileUpload(
            str(video_path),
            mimetype="video/mp4",
            chunksize=4 * 1024 * 1024,
            resumable=True,
        )

        request = service.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
            notifySubscribers=notify_subscribers,
        )

        logger.info(
            f"Beginning resumable upload of '{video_path.name}' scheduled for {rfc3339_publish_at}..."
        )

        response = None
        error = None
        retry_count = 0
        max_retries = 5

        while response is None:
            try:
                status, response = request.next_chunk()
                if status:
                    progress = int(status.progress() * 100)
                    logger.info(f"Upload progress: {progress}%")
            except HttpError as e:
                if e.resp.status in [500, 502, 503, 504]:
                    retry_count += 1
                    if retry_count > max_retries:
                        raise
                    sleep_sec = 2 ** retry_count
                    logger.warning(f"HTTP {e.resp.status}. Retrying in {sleep_sec}s...")
                    time.sleep(sleep_sec)
                else:
                    raise

        video_id = response.get("id")
        watch_url = f"https://youtu.be/{video_id}"
        logger.info(f"✓ Video uploaded successfully: {watch_url} (Scheduled: {rfc3339_publish_at})")

        return ScheduledUploadResult(
            video_id=video_id,
            url=watch_url,
            title=title,
            publish_at=rfc3339_publish_at,
            privacy_status="private",
        )
