"""Vision, face detection, active speaker tracking, and 9:16 vertical reframing."""

from autoclip.vision.face_tracker import FaceTracker, FaceDetection
from autoclip.vision.reframer import VideoReframer, CropWindow
from autoclip.vision.gaming_stacked_crop import StackedGamingCropper, StackedCropGeometry

__all__ = [
    "FaceTracker",
    "FaceDetection",
    "VideoReframer",
    "CropWindow",
    "StackedGamingCropper",
    "StackedCropGeometry",
]
