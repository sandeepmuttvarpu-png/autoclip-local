"""
Hardware-accelerated NVENC rendering and FFmpeg assembly pipeline with progress tracking.
"""

from dataclasses import dataclass
import logging
from pathlib import Path
import re
import shutil
import subprocess
from typing import Optional
from tqdm import tqdm

logger = logging.getLogger(__name__)


@dataclass
class RenderOptions:
    crop_w: int = 608
    crop_h: int = 1080
    crop_x: int = 656
    crop_y: int = 0
    target_width: int = 1080
    target_height: int = 1920
    use_nvenc: bool = True
    nvenc_preset: str = "p6"
    nvenc_tune: str = "hq"
    video_bitrate: str = "8M"
    max_rate: str = "12M"
    buf_size: str = "16M"
    audio_bitrate: str = "192k"


class FFmpegRenderer:
    """Manages high-throughput hardware video clipping and subtitle burning."""

    def __init__(self, ffmpeg_bin: str = "ffmpeg"):
        self.ffmpeg_bin = shutil.which(ffmpeg_bin) or ffmpeg_bin

    @staticmethod
    def _escape_filter_path(path: Path) -> str:
        """Escape path for FFmpeg filter arguments (colons and backslashes)."""
        resolved = str(path.resolve()).replace("\\", "/")
        # Escape colons and single quotes for ffmpeg filtergraph
        return resolved.replace(":", "\\:").replace("'", "\\'")

    def render_clip(
        self,
        input_video: Path,
        start_time: float,
        end_time: float,
        output_video: Path,
        ass_subtitles: Optional[Path] = None,
        options: Optional[RenderOptions] = None,
        show_progress: bool = True,
    ) -> Path:
        """
        Render a 9:16 vertical short with burned kinetic subtitles using NVENC.
        Falls back to libx264 if NVENC is unavailable.
        """
        opts = options or RenderOptions()
        input_video = Path(input_video).resolve()
        output_video = Path(output_video).resolve()
        output_video.parent.mkdir(parents=True, exist_ok=True)

        clip_duration = max(0.1, end_time - start_time)

        # Build video filter chain: crop -> scale to 1080:1920 -> ass overlay
        filters = [
            f"crop={opts.crop_w}:{opts.crop_h}:{opts.crop_x}:{opts.crop_y}",
            f"scale={opts.target_width}:{opts.target_height}",
        ]
        if ass_subtitles and Path(ass_subtitles).exists():
            esc_ass = self._escape_filter_path(Path(ass_subtitles))
            filters.append(f"ass='{esc_ass}'")

        vf_str = ",".join(filters)

        # Assemble encoding parameters
        def build_cmd(use_nvenc: bool):
            cmd = [
                self.ffmpeg_bin,
                "-y",
                "-ss",
                f"{start_time:.3f}",
                "-to",
                f"{end_time:.3f}",
                "-i",
                str(input_video),
                "-vf",
                vf_str,
            ]
            if use_nvenc:
                cmd.extend(
                    [
                        "-c:v",
                        "h264_nvenc",
                        "-preset",
                        opts.nvenc_preset,
                        "-tune",
                        opts.nvenc_tune,
                        "-b:v",
                        opts.video_bitrate,
                        "-maxrate",
                        opts.max_rate,
                        "-bufsize",
                        opts.buf_size,
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
                        "20",
                    ]
                )
            cmd.extend(
                [
                    "-c:a",
                    "aac",
                    "-b:a",
                    opts.audio_bitrate,
                    str(output_video),
                ]
            )
            return cmd

        # Try NVENC first if requested
        primary_nvenc = opts.use_nvenc
        cmd = build_cmd(primary_nvenc)

        logger.info(f"Rendering clip [{start_time:.1f}s -> {end_time:.1f}s] to {output_video.name}...")
        success = self._run_ffmpeg_with_progress(cmd, clip_duration, show_progress=show_progress)

        if not success and primary_nvenc:
            logger.warning("NVENC encoding encountered an issue. Retrying with CPU libx264...")
            fallback_cmd = build_cmd(False)
            success = self._run_ffmpeg_with_progress(fallback_cmd, clip_duration, show_progress=show_progress)

        if not success:
            raise RuntimeError(f"FFmpeg rendering failed for {output_video}")

        return output_video

    def _run_ffmpeg_with_progress(
        self, cmd: list, duration: float, show_progress: bool = True
    ) -> bool:
        """Execute FFmpeg process while parsing stderr for time progress."""
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            universal_newlines=True,
        )

        time_regex = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")
        last_sec = 0.0

        pbar = None
        if show_progress:
            pbar = tqdm(
                total=round(duration, 1),
                unit="s",
                desc="Encoding",
                dynamic_ncols=True,
                leave=False,
            )

        stderr_output = []
        try:
            for line in process.stderr:
                stderr_output.append(line)
                match = time_regex.search(line)
                if match:
                    hours, mins, secs = match.groups()
                    current_sec = (int(hours) * 3600) + (int(mins) * 60) + float(secs)
                    delta = current_sec - last_sec
                    if delta > 0 and pbar:
                        pbar.update(min(delta, duration - pbar.n))
                        last_sec = current_sec

            process.wait()
            if pbar:
                pbar.close()

            if process.returncode != 0:
                full_err = "".join(stderr_output[-25:])
                logger.error(f"FFmpeg exited with code {process.returncode}:\n{full_err}")
                return False
            return True

        except Exception as e:
            if pbar:
                pbar.close()
            process.kill()
            logger.error(f"Error during FFmpeg execution: {e}")
            return False
