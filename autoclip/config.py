"""
Configuration defaults and hardware capability discovery for AutoClip-Local.
"""

from dataclasses import dataclass, field
import os
from pathlib import Path
import shutil
import subprocess


@dataclass
class HardwareConfig:
    device: str = "cuda"
    compute_type: str = "float16"
    has_nvenc: bool = False
    nvenc_preset: str = "p6"
    nvenc_tune: str = "hq"
    video_bitrate: str = "8M"
    max_rate: str = "12M"
    buf_size: str = "16M"
    audio_bitrate: str = "192k"


@dataclass
class AutoClipConfig:
    # Local LLM Server endpoint (OpenAI compatible)
    llm_url: str = os.getenv("AUTOCLIP_LLM_URL", "http://127.0.0.1:8080/v1")
    llm_model: str = os.getenv("AUTOCLIP_LLM_MODEL", "sandwich-model")
    llm_api_key: str = os.getenv("AUTOCLIP_API_KEY", "sandwich")

    # Transcription settings
    whisper_model: str = os.getenv("AUTOCLIP_WHISPER_MODEL", "large-v3")
    whisper_fallback_model: str = "medium.en"
    whisper_device: str = "cuda"
    whisper_compute_type: str = "float16"

    # Virality & Segmentation
    min_clip_duration: float = 30.0  # seconds
    max_clip_duration: float = 65.0  # seconds
    min_virality_score: int = 70
    top_k_clips: int = 3

    # Vision Reframing (9:16 target)
    target_width: int = 1080
    target_height: int = 1920
    sample_fps: float = 5.0
    ema_alpha: float = 0.15  # Smoothing factor: C_smooth(t) = a*C(t) + (1-a)*C_smooth(t-1)
    face_detector_type: str = "auto"  # "mediapipe", "yunet", or "haar"

    # Subtitle Styling
    font_name: str = "Montserrat ExtraBold"
    font_fallback: str = "Arial Black"
    font_size: int = 75
    subtitle_margin_v: int = 190
    highlight_color_ass: str = "&H0000FFFF"  # Bright Yellow (BBGGRR in ASS)
    primary_color_ass: str = "&H00FFFFFF"    # Pure White
    outline_color_ass: str = "&H00000000"    # Black Outline
    max_words_per_line: int = 4

    hardware: HardwareConfig = field(default_factory=HardwareConfig)

    def detect_environment(self) -> None:
        """Probe system hardware for CUDA and NVENC support."""
        try:
            import ctranslate2
            cuda_supported = ctranslate2.get_cuda_device_count() > 0
        except Exception:
            cuda_supported = False

        if not cuda_supported:
            self.whisper_device = "cpu"
            self.whisper_compute_type = "int8"
            self.hardware.device = "cpu"
        else:
            self.whisper_device = "cuda"
            self.whisper_compute_type = "float16"
            self.hardware.device = "cuda"

        # Check NVENC encoder availability in FFmpeg
        ffmpeg_bin = shutil.which("ffmpeg")
        if ffmpeg_bin:
            try:
                res = subprocess.run(
                    [ffmpeg_bin, "-encoders"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=5,
                )
                self.hardware.has_nvenc = "h264_nvenc" in res.stdout
            except Exception:
                self.hardware.has_nvenc = False
        else:
            self.hardware.has_nvenc = False


default_config = AutoClipConfig()
default_config.detect_environment()
