"""
Exponential Moving Average (EMA) 9:16 vertical crop solver.
"""

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import List, Tuple
import cv2

from autoclip.vision.face_tracker import FaceTracker

logger = logging.getLogger(__name__)


@dataclass
class CropWindow:
    crop_w: int
    crop_h: int
    crop_x: int
    crop_y: int = 0
    is_dynamic: bool = False

    def to_ffmpeg_filter(self) -> str:
        """Generate FFmpeg crop filter string."""
        return f"crop={self.crop_w}:{self.crop_h}:{self.crop_x}:{self.crop_y}"


class VideoReframer:
    """Calculates smoothed 9:16 crop coordinates around the active speaker."""

    def __init__(self, ema_alpha: float = 0.15, face_detector_type: str = "auto"):
        self.ema_alpha = ema_alpha
        self.tracker = FaceTracker(detector_type=face_detector_type)

    @staticmethod
    def get_video_dimensions(video_path: Path) -> Tuple[int, int]:
        """Read native width and height of video."""
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video: {video_path}")
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1920)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1080)
        cap.release()
        return width, height

    def compute_smoothed_crop(
        self,
        video_path: Path,
        start_time: float,
        end_time: float,
        sample_fps: float = 5.0,
    ) -> CropWindow:
        """
        Samples focal face centers, smooths via EMA, and calculates the optimal 9:16 crop window.
        """
        width, height = self.get_video_dimensions(video_path)

        # 9:16 vertical aspect calculation
        # crop_w = height * (9 / 16)
        crop_w = int(round(height * (9.0 / 16.0)))
        # Ensure even dimension for H.264 / NVENC compatibility
        if crop_w % 2 != 0:
            crop_w += 1
        crop_h = height
        if crop_h % 2 != 0:
            crop_h -= 1

        # If original video is already narrower than 9:16, clamp crop_w to width
        if crop_w > width:
            crop_w = width if width % 2 == 0 else width - 1

        # Track focal points
        focal_points = self.tracker.track_clip_focal_points(
            video_path,
            start_time=start_time,
            end_time=end_time,
            sample_fps=sample_fps,
        )

        if not focal_points:
            # Default center crop
            default_cx = width / 2.0
            cx_clamped = int(max(0, min(default_cx - crop_w / 2.0, width - crop_w)))
            return CropWindow(crop_w=crop_w, crop_h=crop_h, crop_x=cx_clamped, crop_y=0)

        # Apply EMA smoothing: C_smooth(t) = alpha * C(t) + (1 - alpha) * C_smooth(t-1)
        smoothed_centers: List[float] = []
        c_smooth = focal_points[0][1]

        for _, raw_cx in focal_points:
            c_smooth = (self.ema_alpha * raw_cx) + ((1.0 - self.ema_alpha) * c_smooth)
            smoothed_centers.append(c_smooth)

        # For ultra-smooth production shorts without jitter or jarring camera swings,
        # compute the robust median smoothed focal point across the clip duration.
        smoothed_centers.sort()
        median_center = smoothed_centers[len(smoothed_centers) // 2]

        # Calculate crop_x = clamp(C_smooth - crop_w / 2, 0, width - crop_w)
        raw_crop_x = median_center - (crop_w / 2.0)
        crop_x = int(round(max(0.0, min(raw_crop_x, float(width - crop_w)))))
        # Ensure even offset
        if crop_x % 2 != 0:
            crop_x = max(0, crop_x - 1)

        logger.info(
            f"Computed 9:16 crop for [{start_time:.1f}s -> {end_time:.1f}s]: "
            f"w={crop_w}, h={crop_h}, x={crop_x} (median focal center={median_center:.1f})"
        )

        return CropWindow(crop_w=crop_w, crop_h=crop_h, crop_x=crop_x, crop_y=0)
