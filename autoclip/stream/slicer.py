"""
High-Throughput Continuous Stream Slicer and 2-Minute 9:16 Vertical Cropper.
Supports direct HTTP seek-slicing via yt-dlp (zero 20GB download bottleneck) and local MP4/MKV files.
"""

from dataclasses import dataclass
import json
import logging
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import List, Optional, Tuple

from autoclip.core.job_queue import job_queue

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
    status: str = "unpublished"
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
    """Slices 1-to-5 hour streams into back-to-back 2-minute 9:16 vertical shorts."""

    def __init__(self, nvenc_enabled: bool = True):
        self.nvenc_enabled = nvenc_enabled
        self.ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"
        self.ffprobe_bin = shutil.which("ffprobe") or "ffprobe"
        self.ytdlp_bin = shutil.which("yt-dlp") or "yt-dlp"

    @staticmethod
    def clean_name(name: str) -> str:
        cleaned = re.sub(r"[^\w\s-]", "", name).strip().replace(" ", "_")
        return cleaned[:60] if cleaned else "stream_vod"

    def get_duration(self, video_source: str) -> float:
        """Query duration in seconds using ffprobe."""
        cmd = [
            self.ffprobe_bin,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            video_source,
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            return float(res.stdout.strip())
        except ValueError:
            return 0.0

    def get_stream_urls(self, url: str) -> Tuple[str, str, float]:
        """
        Extract direct streaming URLs and title via yt-dlp without downloading to disk.
        Returns (video_url, safe_title, duration_sec).
        """
        logger.info(f"Extracting direct stream metadata for: {url}")
        cmd = [
            self.ytdlp_bin,
            "--no-warnings",
            "--dump-json",
            url,
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"Failed to query stream info via yt-dlp: {res.stderr}")

        info = json.loads(res.stdout)
        raw_title = info.get("title", f"stream_{int(time.time())}")
        safe_title = self.clean_name(raw_title)
        duration = float(info.get("duration", 0.0))

        # Get direct stream URL
        g_cmd = [
            self.ytdlp_bin,
            "-f",
            "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
            "-g",
            url,
        ]
        g_res = subprocess.run(g_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if g_res.returncode != 0 or not g_res.stdout.strip():
            raise RuntimeError(f"Could not resolve direct media URL: {g_res.stderr}")

        direct_urls = g_res.stdout.strip().splitlines()
        direct_url = direct_urls[0]  # First is video/merged
        return direct_url, safe_title, duration

    def slice_stream_to_shorts(
        self,
        source: Optional[str] = None,
        video_path: Optional[Path] = None,
        is_url: bool = False,
        segment_duration: float = 120.0,
        library_dir: Path = Path("library/unpublished"),
        max_clips: Optional[int] = None,
        job_id: Optional[str] = None,
    ) -> List[ClipMetadata]:
        """
        Slices stream into sequential 2-minute 9:16 (1080x1920) clips.
        Stores them inside library/unpublished/<stream_name>/clip_XXX.mp4
        """
        source_input = str(source or video_path)
        if is_url:
            if job_id:
                job_queue.update_progress(job_id, 5.0, "Resolving direct stream URL via yt-dlp...")
            media_input, stream_name, total_duration = self.get_stream_urls(source_input)
            if total_duration <= 0.0:
                total_duration = self.get_duration(media_input)
        else:
            p = Path(source_input).resolve()
            if not p.exists():
                raise FileNotFoundError(f"Source video file not found: {p}")
            media_input = str(p)
            stream_name = self.clean_name(p.stem)
            total_duration = self.get_duration(media_input)

        if total_duration <= 0.0:
            raise ValueError(f"Could not determine valid duration for: {source}")

        stream_folder = Path(library_dir) / stream_name
        stream_folder.mkdir(parents=True, exist_ok=True)

        # 9:16 vertical crop formula:
        # crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=1080:1920
        vf_filter = "crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=1080:1920"

        clips: List[ClipMetadata] = []
        curr_start = 0.0
        clip_idx = 1
        now = time.time()

        total_segments = int(total_duration // segment_duration)
        if total_duration % segment_duration > 15.0:
            total_segments += 1
        if max_clips:
            total_segments = min(total_segments, max_clips)

        logger.info(
            f"Slicing '{stream_name}' ({total_duration/60.0:.1f} mins) into {total_segments} shorts of {segment_duration:.0f}s..."
        )

        while curr_start < total_duration:
            if max_clips and clip_idx > max_clips:
                break

            actual_duration = min(segment_duration, total_duration - curr_start)
            min_threshold = min(15.0, segment_duration * 0.5)
            if actual_duration < min_threshold:  # Skip tiny end slivers
                break

            clip_id = f"{stream_name}_clip_{clip_idx:03d}"
            output_file = stream_folder / f"{clip_id}.mp4"

            progress_pct = (clip_idx / total_segments) * 100.0
            stage_msg = f"Slicing Clip {clip_idx}/{total_segments} ({curr_start/60.0:.1f}m - {(curr_start+actual_duration)/60.0:.1f}m)"

            if job_id:
                job_queue.update_progress(
                    job_id=job_id,
                    progress=progress_pct,
                    stage=stage_msg,
                    details={
                        "stream_name": stream_name,
                        "current_clip": clip_idx,
                        "total_clips": total_segments,
                    },
                )

            # Fast Seek with input seek (-ss before -i) for instant streaming seek
            cmd = [
                self.ffmpeg_bin,
                "-y",
                "-ss",
                f"{curr_start:.2f}",
                "-i",
                media_input,
                "-t",
                f"{actual_duration:.2f}",
                "-vf",
                vf_filter,
            ]

            if self.nvenc_enabled:
                cmd.extend([
                    "-c:v", "h264_nvenc",
                    "-preset", "p5",
                    "-b:v", "6M",
                ])
            else:
                cmd.extend([
                    "-c:v", "libx264",
                    "-preset", "veryfast",
                    "-crf", "22",
                ])

            cmd.extend([
                "-c:a", "aac",
                "-b:a", "192k",
                "-movflags", "+faststart",
                str(output_file),
            ])

            logger.info(f"Encoding clip {clip_idx}/{total_segments} -> {output_file.name}")
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

            if res.returncode != 0:
                if self.nvenc_enabled:
                    logger.warning(f"NVENC failed. Retrying with libx264 CPU fallback: {res.stderr}")
                    self.nvenc_enabled = False
                    # Retry with CPU
                    cpu_cmd = [
                        self.ffmpeg_bin, "-y",
                        "-ss", f"{curr_start:.2f}",
                        "-i", media_input,
                        "-t", f"{actual_duration:.2f}",
                        "-vf", vf_filter,
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
                        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                        str(output_file),
                    ]
                    res = subprocess.run(cpu_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

                if res.returncode != 0:
                    logger.error(f"Failed to slice clip {clip_id}: {res.stderr}")
                    curr_start += segment_duration
                    clip_idx += 1
                    continue

            meta = ClipMetadata(
                clip_id=clip_id,
                stream_name=stream_name,
                clip_index=clip_idx,
                start_sec=curr_start,
                end_sec=curr_start + actual_duration,
                duration=actual_duration,
                file_path=str(output_file),
                status="unpublished",
                created_at=now,
            )

            # Write clip JSON sidecar
            meta_path = stream_folder / f"{clip_id}.json"
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta.to_dict(), f, indent=2)

            clips.append(meta)
            curr_start += segment_duration
            clip_idx += 1

        # Write stream summary index and stream_meta
        summary_data = {
            "stream_name": stream_name,
            "original_video_path": media_input,
            "total_duration_sec": total_duration,
            "clip_count": len(clips),
            "clips": [c.to_dict() for c in clips],
            "created_at": now,
        }
        for summary_filename in ["stream_meta.json", "stream_summary.json"]:
            summary_path = stream_folder / summary_filename
            with open(summary_path, "w", encoding="utf-8") as f:
                json.dump(summary_data, f, indent=2)

        if job_id:
            job_queue.update_progress(
                job_id=job_id,
                progress=100.0,
                stage="Slicing Complete",
                details={"stream_name": stream_name, "total_clips": len(clips)},
            )
            job_queue.mark_completed(job_id=job_id, result={
                "stream_name": stream_name,
                "clips_count": len(clips),
                "clips": [c.to_dict() for c in clips],
            })

        return clips
