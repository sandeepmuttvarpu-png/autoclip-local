"""
Continuous 1-to-5 hour Live Stream Downloader and 2-Minute 9:16 Vertical Slicer.
Chunks massive VODs into back-to-back 1080x1920 vertical shorts via hardware NVENC.
"""

from dataclasses import dataclass
import json
import logging
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ClipMetadata:
    clip_id: str
    stream_name: str
    clip_index: int
    start_sec: float
    end_sec: float
    duration: float
    file_path: str
    status: str = "unpublished"  # "unpublished" or "published"
    story_attached: bool = False
    story_headline: Optional[str] = None
    created_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "clip_id": self.clip_id,
            "stream_name": self.stream_name,
            "clip_index": self.clip_index,
            "start_sec": round(self.start_sec, 2),
            "end_sec": round(self.end_sec, 2),
            "duration": round(self.duration, 2),
            "file_path": self.file_path,
            "status": self.status,
            "story_attached": self.story_attached,
            "story_headline": self.story_headline,
            "created_at": self.created_at,
        }


@dataclass
class StreamMetadata:
    stream_name: str
    original_video_path: str
    total_duration_sec: float
    clip_count: int
    created_at: float

    def to_dict(self) -> dict:
        return {
            "stream_name": self.stream_name,
            "original_video_path": self.original_video_path,
            "total_duration_sec": round(self.total_duration_sec, 2),
            "clip_count": self.clip_count,
            "created_at": self.created_at,
        }


class StreamSlicer:
    """Manages Twitch/YouTube stream ingestion and continuous 2-minute 9:16 slicing."""

    def __init__(self, nvenc_enabled: bool = True):
        self.nvenc_enabled = nvenc_enabled
        self.ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"
        self.ffprobe_bin = shutil.which("ffprobe") or "ffprobe"
        self.ytdlp_bin = shutil.which("yt-dlp") or "yt-dlp"

    @staticmethod
    def clean_name(name: str) -> str:
        """Sanitize filename to alphanumeric and underscores."""
        cleaned = re.sub(r"[^\w\s-]", "", name).strip().replace(" ", "_")
        return cleaned[:60] if cleaned else "stream_vod"

    def get_duration(self, video_path: Path) -> float:
        """Query duration in seconds using ffprobe."""
        cmd = [
            self.ffprobe_bin,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(video_path),
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            return float(res.stdout.strip())
        except ValueError:
            return 0.0

    def download_stream(self, url: str, output_dir: Path) -> Path:
        """Download Twitch or YouTube stream using yt-dlp."""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"Querying stream metadata for: {url}")
        # Get title first
        title_cmd = [self.ytdlp_bin, "--get-title", url]
        title_res = subprocess.run(title_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        raw_title = title_res.stdout.strip() if title_res.returncode == 0 else f"vod_{int(time.time())}"
        safe_title = self.clean_name(raw_title)

        out_template = output_dir / f"{safe_title}.%(ext)s"
        cmd = [
            self.ytdlp_bin,
            "-f",
            "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
            "--merge-output-format",
            "mp4",
            "-o",
            str(out_template),
            url,
        ]
        logger.info(f"Downloading stream '{safe_title}'...")
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"yt-dlp download failed: {res.stderr}")

        # Find downloaded file
        candidates = list(output_dir.glob(f"{safe_title}.*"))
        if not candidates:
            raise FileNotFoundError(f"Downloaded stream not found in {output_dir}")
        return candidates[0]

    def slice_stream_to_shorts(
        self,
        video_path: Path,
        segment_duration: float = 120.0,
        library_dir: Path = Path("library/unpublished"),
        max_clips: Optional[int] = None,
        progress_callback=None,
    ) -> List[ClipMetadata]:
        """
        Splits a 1-to-5 hour stream into back-to-back ~2-minute 1080x1920 (9:16) clips.
        Stores them inside a dedicated folder named after the stream.
        """
        video_path = Path(video_path).resolve()
        if not video_path.exists():
            raise FileNotFoundError(f"Video file not found: {video_path}")

        stream_name = self.clean_name(video_path.stem)
        total_duration = self.get_duration(video_path)
        if total_duration <= 0.0:
            raise ValueError(f"Could not determine valid duration for: {video_path}")

        stream_folder = Path(library_dir) / stream_name
        stream_folder.mkdir(parents=True, exist_ok=True)

        # 9:16 vertical crop formula:
        # crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=1080:1920
        # Centered crop from landscape 16:9
        vf_filter = "crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=1080:1920"

        clips: List[ClipMetadata] = []
        curr_start = 0.0
        clip_idx = 1
        now = time.time()

        total_segments = int(total_duration // segment_duration)
        if total_duration % segment_duration > 15.0:  # Include remainder if > 15s
            total_segments += 1
        if max_clips:
            total_segments = min(total_segments, max_clips)

        logger.info(
            f"Slicing '{stream_name}' ({total_duration/60.0:.1f} mins) into {total_segments} shorts of {segment_duration:.0f}s..."
        )

        while curr_start < total_duration:
            if max_clips and clip_idx > max_clips:
                break

            curr_end = min(curr_start + segment_duration, total_duration)
            clip_len = curr_end - curr_start
            min_len = min(10.0, segment_duration * 0.5)
            if clip_len < min_len:  # Skip tiny residual tails
                break

            clip_id = f"{stream_name}_clip_{clip_idx:03d}"
            clip_file = stream_folder / f"{clip_id}.mp4"
            clip_meta_file = stream_folder / f"{clip_id}.json"

            # Execute FFmpeg cut and 9:16 vertical crop
            cmd = [
                self.ffmpeg_bin,
                "-y",
                "-ss",
                f"{curr_start:.3f}",
                "-to",
                f"{curr_end:.3f}",
                "-i",
                str(video_path),
                "-vf",
                vf_filter,
            ]
            if self.nvenc_enabled:
                cmd.extend(
                    [
                        "-c:v",
                        "h264_nvenc",
                        "-preset",
                        "p6",
                        "-tune",
                        "hq",
                        "-b:v",
                        "6M",
                    ]
                )
            else:
                cmd.extend(
                    [
                        "-c:v",
                        "libx264",
                        "-preset",
                        "veryfast",
                        "-crf",
                        "22",
                    ]
                )

            cmd.extend(["-c:a", "aac", "-b:a", "128k", str(clip_file)])

            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if res.returncode != 0:
                if self.nvenc_enabled:
                    logger.warning(f"NVENC failed for clip {clip_idx}. Retrying with CPU libx264...")
                    self.nvenc_enabled = False
                    continue
                else:
                    logger.error(f"Failed to render clip {clip_idx}: {res.stderr}")

            meta = ClipMetadata(
                clip_id=clip_id,
                stream_name=stream_name,
                clip_index=clip_idx,
                start_sec=curr_start,
                end_sec=curr_end,
                duration=clip_len,
                file_path=str(clip_file.resolve()),
                status="unpublished",
                created_at=now,
            )
            # Save individual clip metadata JSON
            with open(clip_meta_file, "w", encoding="utf-8") as f:
                json.dump(meta.to_dict(), f, indent=2)

            clips.append(meta)
            if progress_callback:
                progress_callback(clip_idx, total_segments, meta)

            curr_start = curr_end
            clip_idx += 1

        # Write overall stream summary
        stream_meta = StreamMetadata(
            stream_name=stream_name,
            original_video_path=str(video_path),
            total_duration_sec=total_duration,
            clip_count=len(clips),
            created_at=now,
        )
        with open(stream_folder / "stream_meta.json", "w", encoding="utf-8") as f:
            json.dump(stream_meta.to_dict(), f, indent=2)

        logger.info(f"✓ Successfully generated {len(clips)} shorts in: {stream_folder}")
        return clips
