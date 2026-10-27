"""Virality scoring and narrative segmentation module."""

from autoclip.intelligence.prompt import VIRALITY_SYSTEM_PROMPT, build_user_prompt
from autoclip.intelligence.segmenter import ClipCandidate, ViralitySegmenter

__all__ = [
    "VIRALITY_SYSTEM_PROMPT",
    "build_user_prompt",
    "ClipCandidate",
    "ViralitySegmenter",
]
