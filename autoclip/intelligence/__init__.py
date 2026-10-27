"""Virality scoring and narrative segmentation module."""

from autoclip.intelligence.prompt import VIRALITY_SYSTEM_PROMPT, build_user_prompt
from autoclip.intelligence.segmenter import ClipCandidate, ViralitySegmenter
from autoclip.intelligence.audio_spike_detector import AudioSpikeDetector, HypeMoment

__all__ = [
    "VIRALITY_SYSTEM_PROMPT",
    "build_user_prompt",
    "ClipCandidate",
    "ViralitySegmenter",
    "AudioSpikeDetector",
    "HypeMoment",
]
