"""
Twitch / Gaming stacked vertical layout generator (Facecam Top 35% + Gameplay Bottom 65%).
Produces 1080x1920 vertical shorts optimized for gaming VODs and esports highlights.
"""

from dataclasses import dataclass
import logging
from pathlib import Path
import shutil
import subprocess
from typing import Optional, Tuple
import cv2

from autoclip.vision.face_tracker import FaceTracker

logger = logging.getLogger(__name__)


@dataclass
class StackedCropGeometry:
    face_crop_w: int
    face_crop_h: int
    face_crop_x: int
    face_crop_y: int
    game_crop_w: int
    game_crop_h: int
    game_crop_x: int
    game_crop_y: int
    top_scale_w: int = 1080
    top_scale_h: int = 672    # 35% of 1920
    bottom_scale_w: int = 1080
    bottom_scale_h: int = 1248 # 65% of 1920 (672 + 1248 = 1920)

    def to_filtergraph(self, ass_subtitles_path: Optional[Path] = None) -> str:
        """
        Builds FFmpeg filtergraph string:
        [0:v]crop=w:h:x:y,scale=1080:672[top];
        [0:v]crop=w:h:x:y,scale=1080:1248[bottom];
        [top][bottom]vstack[outv]
        """
        top_filter = (
            f"[0:v]crop={self.face_crop_w}:{self.face_crop_h}:{self.face_crop_x}:{self.face_crop_y},"
            f"scale={self.top_scale_w}:{self.top_scale_h}[top]"
        )
        bottom_filter = (
            f"[0:v]crop={self.game_crop_w}:{self.game_crop_h}:{self.game_crop_x}:{self.game_crop_y},"
            f"scale={self.bottom_scale_w}:{self.bottom_scale_h}[bottom]"
        )

        if ass_subtitles_path and Path(ass_subtitles_path).exists():
            resolved = str(Path(ass_subtitles_path).resolve()).replace("\\", "/")
            esc_ass = resolved.replace(":", "\\:").replace("'", "\\'")
            stack_filter = f"[top][bottom]vstack[stacked]; [stacked]ass='{esc_ass}'[outv]"
        else:
            stack_filter = "[top][bottom]vstack[outv]"

        return f"{top_filter}; {bottom_filter}; {stack_filter}"


class StackedGamingCropper:
    """Detects streamer facecam and central gameplay to construct stacked 9:16 layout."""

    def __init__(self, face_detector_type: str = "auto"):
        self.tracker = FaceTracker(detector_type=face_detector_type)

    @staticmethod
    def _make_even(val: int) -> int:
        return val if val % 2 == 0 else val + 1

    def solve_geometry(
        self,
        video_path: Path,
        start_time: float,
        end_time: float,
        sample_fps: float = 4.0,
    ) -> StackedCropGeometry:
        """Analyze video frames to find webcam location and central action frame."""
        video_path = Path(video_path)
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video: {video_path}")

        W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1920)
        H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1080)
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

        # Sample frames to locate streamer face
        sample_interval = 1.0 / sample_fps
        curr_t = start_time
        detected_faces = []

        while curr_t <= end_time:
            frame_idx = int(curr_t * fps)
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = cap.read()
            if ret and frame is not None:
                det = self.tracker.detect_in_frame(frame, curr_t)
                if det is not None:
                    detected_faces.append(det)
            curr_t += sample_interval

        cap.release()

        # 1. Solve Upper Crop (Streamer Facecam -> 1080x672)
        target_top_aspect = 1080.0 / 672.0  # ~1.607
        if detected_faces:
            # Average face position
            avg_cx = sum(d.center_x for d in detected_faces) / len(detected_faces)
            avg_cy = sum(d.center_y for d in detected_faces) / len(detected_faces)
            avg_w = sum(d.box[2] for d in detected_faces) / len(detected_faces)
            avg_h = sum(d.box[3] for d in detected_faces) / len(detected_faces)

            # Frame size around face (approx 2.6x face height)
            face_h = int(min(H * 0.55, max(avg_h * 2.6, 260)))
            face_w = int(round(face_h * target_top_aspect))
            if face_w > W:
                face_w = W
                face_h = int(round(face_w / target_top_aspect))

            face_x = int(max(0, min(avg_cx - face_w / 2.0, W - face_w)))
            face_y = int(max(0, min(avg_cy - face_h / 2.0, H - face_h)))
        else:
            # Default to top-left corner (common streamer webcam location)
            face_h = int(H * 0.45)
            face_w = int(round(face_h * target_top_aspect))
            face_x = 0
            face_y = 0

        # 2. Solve Lower Crop (Central Gameplay -> 1080x1248)
        target_bottom_aspect = 1080.0 / 1248.0  # ~0.865
        game_h = H
        game_w = int(round(game_h * target_bottom_aspect))
        if game_w > W:
            game_w = W
            game_h = int(round(game_w / target_bottom_aspect))

        # Center on screen middle (W/2, H/2)
        game_x = int(max(0, min((W / 2.0) - (game_w / 2.0), W - game_w)))
        game_y = int(max(0, min((H / 2.0) - (game_h / 2.0), H - game_h)))

        # Ensure all dimensions are strictly even for H.264 / NVENC
        geom = StackedCropGeometry(
            face_crop_w=self._make_even(face_w),
            face_crop_h=self._make_even(face_h),
            face_crop_x=self._make_even(face_x),
            face_crop_y=self._make_even(face_y),
            game_crop_w=self._make_even(game_w),
            game_crop_h=self._make_even(game_h),
            game_crop_x=self._make_even(game_x),
            game_crop_y=self._make_even(game_y),
            top_scale_w=1080,
            top_scale_h=672,
            bottom_scale_w=1080,
            bottom_scale_h=1248,
        )

        logger.info(
            f"Solved Stacked Gaming Geometry: Facecam={geom.face_crop_w}x{geom.face_crop_h} at ({geom.face_crop_x},{geom.face_crop_y}) -> "
            f"Gameplay={geom.game_crop_w}x{geom.game_crop_h} at ({geom.game_crop_x},{geom.game_crop_y})"
        )
        return geom

    def render_stacked_clip(
        self,
        input_video: Path,
        start_time: float,
        end_time: float,
        output_video: Path,
        ass_subtitles: Optional[Path] = None,
        use_nvenc: bool = True,
        video_bitrate: str = "8M",
    ) -> Path:
        """Render stacked gaming vertical short directly via FFmpeg with NVENC."""
        input_video = Path(input_video).resolve()
        output_video = Path(output_video).resolve()
        output_video.parent.mkdir(parents=True, exist_ok=True)

        geom = self.solve_geometry(input_video, start_time, end_time)
        filtergraph = geom.to_filtergraph(ass_subtitles_path=ass_subtitles)

        ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"
        cmd = [
            ffmpeg_bin,
            "-y",
            "-ss",
            f"{start_time:.3f}",
            "-to",
            f"{end_time:.3f}",
            "-i",
            str(input_video),
            "-filter_complex",
            filtergraph,
            "-map",
            "[outv]",
            "-map",
            "0:a?",
        ]

        if use_nvenc:
            cmd.extend(
                [
                    "-c:v",
                    "h264_nvenc",
                    "-preset",
                    "p6",
                    "-tune",
                    "hq",
                    "-b:v",
                    video_bitrate,
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

        cmd.extend(["-c:a", "aac", "-b:a", "192k", str(output_video)])

        logger.info(f"Rendering stacked gaming clip to {output_video.name}...")
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            if use_nvenc:
                logger.warning("NVENC failed for stacked render. Retrying with CPU libx264...")
                return self.render_stacked_clip(
                    input_video,
                    start_time,
                    end_time,
                    output_video,
                    ass_subtitles=ass_subtitles,
                    use_nvenc=False,
                    video_bitrate=video_bitrate,
                )
            raise RuntimeError(f"Stacked gaming FFmpeg render failed: {res.stderr}")

        return output_video
