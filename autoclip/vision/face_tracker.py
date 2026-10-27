"""
Face detection and focal center tracking using MediaPipe and OpenCV Haar cascade.
"""

from dataclasses import dataclass
import logging
import os
from pathlib import Path
from typing import List, Optional, Tuple
import cv2

logger = logging.getLogger(__name__)

MODELS_DIR = Path(__file__).parent / "models"
BLAZE_MODEL_PATH = MODELS_DIR / "blaze_face_short_range.tflite"
HAAR_MODEL_PATH = MODELS_DIR / "haarcascade_frontalface_default.xml"


@dataclass
class FaceDetection:
    timestamp: float
    center_x: float
    center_y: float
    box: Tuple[int, int, int, int]  # x, y, w, h
    confidence: float


class FaceTracker:
    """Samples video frames and extracts focal face coordinates."""

    def __init__(self, detector_type: str = "auto", min_detection_confidence: float = 0.5):
        self.detector_type = detector_type
        self.min_confidence = min_detection_confidence
        self._mp_tasks_detector = None
        self._mp_legacy_detector = None
        self._cv_haar = None
        self._initialized = False

    def _init_detector(self):
        if self._initialized:
            return
        self._initialized = True

        # 1. Attempt MediaPipe Tasks (MediaPipe 1.x)
        if self.detector_type in ("auto", "mediapipe") and BLAZE_MODEL_PATH.exists():
            try:
                import mediapipe as mp
                from mediapipe.tasks import python
                from mediapipe.tasks.python import vision

                base_options = python.BaseOptions(model_asset_path=str(BLAZE_MODEL_PATH))
                options = vision.FaceDetectorOptions(
                    base_options=base_options,
                    min_detection_confidence=self.min_confidence,
                )
                self._mp_tasks_detector = vision.FaceDetector.create_from_options(options)
                logger.info("Initialized MediaPipe Tasks FaceDetector (blaze_face).")
                return
            except Exception as e:
                logger.debug(f"MediaPipe Tasks init skipped: {e}")

        # 2. Attempt MediaPipe Legacy (MediaPipe 0.x)
        if self.detector_type in ("auto", "mediapipe"):
            try:
                import mediapipe as mp
                if hasattr(mp, "solutions") and hasattr(mp.solutions, "face_detection"):
                    self._mp_legacy_detector = mp.solutions.face_detection.FaceDetection(
                        model_selection=1,
                        min_detection_confidence=self.min_confidence,
                    )
                    logger.info("Initialized MediaPipe Solutions FaceDetection.")
                    return
            except Exception as e:
                logger.debug(f"MediaPipe Solutions init skipped: {e}")

        # 3. Attempt OpenCV Haar Cascade
        haar_candidate = None
        if HAAR_MODEL_PATH.exists():
            haar_candidate = str(HAAR_MODEL_PATH)
        elif hasattr(cv2, "data") and hasattr(cv2.data, "haarcascades"):
            cand = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
            if os.path.exists(cand):
                haar_candidate = cand

        if haar_candidate:
            try:
                cascade = cv2.CascadeClassifier(haar_candidate)
                if not cascade.empty():
                    self._cv_haar = cascade
                    logger.info(f"Initialized OpenCV Haar Cascade from {haar_candidate}.")
                    return
            except Exception as e:
                logger.debug(f"OpenCV Haar init skipped: {e}")

        logger.info("No face detector model loaded. Defaulting to center crop tracking.")

    def detect_in_frame(self, frame_bgr: cv2.Mat, timestamp: float) -> Optional[FaceDetection]:
        """Detect the primary face in a single BGR frame."""
        self._init_detector()
        h, w = frame_bgr.shape[:2]

        # Path 1: MediaPipe Tasks
        if self._mp_tasks_detector is not None:
            try:
                import mediapipe as mp
                frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
                results = self._mp_tasks_detector.detect(mp_image)
                if results and results.detections:
                    best_det = None
                    max_area = 0.0
                    for det in results.detections:
                        score = det.categories[0].score if det.categories else 1.0
                        bb = det.bounding_box
                        box_x = int(bb.origin_x)
                        box_y = int(bb.origin_y)
                        box_w = int(bb.width)
                        box_h = int(bb.height)
                        area = box_w * box_h
                        if area > max_area:
                            max_area = area
                            best_det = FaceDetection(
                                timestamp=timestamp,
                                center_x=box_x + box_w / 2.0,
                                center_y=box_y + box_h / 2.0,
                                box=(box_x, box_y, box_w, box_h),
                                confidence=score,
                            )
                    if best_det:
                        return best_det
            except Exception as e:
                logger.debug(f"MediaPipe Tasks frame processing error: {e}")

        # Path 2: MediaPipe Legacy
        if self._mp_legacy_detector is not None:
            try:
                frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                results = self._mp_legacy_detector.process(frame_rgb)
                if results and results.detections:
                    best_det = None
                    max_area = 0.0
                    for det in results.detections:
                        score = det.score[0] if det.score else 1.0
                        bb = det.location_data.relative_bounding_box
                        box_x = int(bb.xmin * w)
                        box_y = int(bb.ymin * h)
                        box_w = int(bb.width * w)
                        box_h = int(bb.height * h)
                        area = box_w * box_h
                        if area > max_area:
                            max_area = area
                            best_det = FaceDetection(
                                timestamp=timestamp,
                                center_x=box_x + box_w / 2.0,
                                center_y=box_y + box_h / 2.0,
                                box=(box_x, box_y, box_w, box_h),
                                confidence=score,
                            )
                    if best_det:
                        return best_det
            except Exception as e:
                logger.debug(f"MediaPipe Legacy frame processing error: {e}")

        # Path 3: OpenCV Haar Cascade
        if self._cv_haar is not None and not self._cv_haar.empty():
            try:
                gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
                faces = self._cv_haar.detectMultiScale(
                    gray,
                    scaleFactor=1.1,
                    minNeighbors=4,
                    minSize=(int(w * 0.05), int(h * 0.05)),
                )
                if len(faces) > 0:
                    largest = max(faces, key=lambda b: b[2] * b[3])
                    fx, fy, fw, fh = largest
                    return FaceDetection(
                        timestamp=timestamp,
                        center_x=fx + fw / 2.0,
                        center_y=fy + fh / 2.0,
                        box=(int(fx), int(fy), int(fw), int(fh)),
                        confidence=0.85,
                    )
            except Exception as e:
                logger.debug(f"OpenCV Haar detection error: {e}")

        return None

    def track_clip_focal_points(
        self,
        video_path: Path,
        start_time: float,
        end_time: float,
        sample_fps: float = 5.0,
    ) -> List[Tuple[float, float]]:
        """
        Sample frames between start_time and end_time at sample_fps,
        returning a list of (timestamp, center_x) coordinates.
        """
        video_path = Path(video_path)
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise RuntimeError(f"Could not open video file: {video_path}")

        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1920)
        default_center = width / 2.0

        sample_interval = 1.0 / sample_fps
        current_time = start_time
        focal_points: List[Tuple[float, float]] = []
        last_known_cx = default_center

        while current_time <= end_time:
            frame_num = int(current_time * fps)
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
            ret, frame = cap.read()
            if not ret or frame is None:
                focal_points.append((current_time, last_known_cx))
                current_time += sample_interval
                continue

            det = self.detect_in_frame(frame, current_time)
            if det is not None:
                last_known_cx = det.center_x
            focal_points.append((current_time, last_known_cx))
            current_time += sample_interval

        cap.release()
        return focal_points
