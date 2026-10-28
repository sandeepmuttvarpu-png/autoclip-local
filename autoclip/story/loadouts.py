"""
Editing Loadouts and Preset Configurations for AutoClip-Local.
Enables instant 1-click batch application of voice, subtitle styles, screen placement, and audio mix.
"""

from dataclasses import dataclass, asdict
from typing import Dict, List, Optional


@dataclass
class EditingLoadout:
    loadout_id: str
    name: str
    description: str
    voice_id: str
    speech_rate: str = "+5%"
    subtitle_style: str = "pop_karaoke"  # "pop_karaoke", "mrbeast_bold", "clean_minimal"
    subtitle_position: str = "lower_third"  # "center", "lower_third", "top_third"
    font_name: str = "Montserrat ExtraBold"
    font_size: int = 20
    highlight_color: str = "&H0000FFFF&"  # Vibrant Yellow
    primary_color: str = "&H00FFFFFF&"    # Pure White
    outline_color: str = "&H00000000&"    # Pure Black
    outline_width: int = 3
    bg_audio_volume: float = 0.12         # 12% stream audio ducking
    voice_volume: float = 1.0             # 100% voice audio
    hook_card_enabled: bool = True
    hook_card_duration: float = 3.5

    def get_margin_v_and_alignment(self) -> tuple[int, int]:
        """
        Returns (margin_v, alignment) for ASS subtitle positioning.
        Alignment 2 = Bottom-center (lower third)
        Alignment 5 = Middle-center
        Alignment 8 = Top-center
        """
        if self.subtitle_position == "center":
            return (0, 5)
        elif self.subtitle_position == "top_third":
            return (180, 8)
        else:  # "lower_third"
            return (160, 2)

    def to_dict(self) -> dict:
        return asdict(self)


# Built-in High-Retention Loadouts
DEFAULT_LOADOUTS: Dict[str, EditingLoadout] = {
    "loadout_1": EditingLoadout(
        loadout_id="loadout_1",
        name="Loadout 1: Viral AITA & Drama",
        description="Deep narrative voice, kinetic yellow pop subtitles in lower-third, 12% gameplay ducking.",
        voice_id="en-US-ChristopherNeural",
        speech_rate="+5%",
        subtitle_style="pop_karaoke",
        subtitle_position="lower_third",
        highlight_color="&H0000FFFF&",  # Yellow
        primary_color="&H00FFFFFF&",    # White
        font_size=20,
        bg_audio_volume=0.12,
        hook_card_enabled=True,
    ),
    "loadout_2": EditingLoadout(
        loadout_id="loadout_2",
        name="Loadout 2: Reddit Story Casual",
        description="Casual storytelling voice (Guy), center pop subtitles, 10% gameplay ducking.",
        voice_id="en-US-GuyNeural",
        speech_rate="+8%",
        subtitle_style="pop_karaoke",
        subtitle_position="center",
        highlight_color="&H0000E6FF&",  # Gold / Amber
        primary_color="&H00FFFFFF&",
        font_size=21,
        bg_audio_volume=0.10,
        hook_card_enabled=True,
    ),
    "loadout_3": EditingLoadout(
        loadout_id="loadout_3",
        name="Loadout 3: MrBeast Bold Neon",
        description="High-energy voice (Aria), high-contrast bold font with green highlights, 8% ducking.",
        voice_id="en-US-AriaNeural",
        speech_rate="+10%",
        subtitle_style="mrbeast_bold",
        subtitle_position="lower_third",
        highlight_color="&H0000FF00&",  # Neon Green
        primary_color="&H00FFFFFF&",
        font_size=22,
        outline_width=4,
        bg_audio_volume=0.08,
        hook_card_enabled=True,
    ),
    "loadout_4": EditingLoadout(
        loadout_id="loadout_4",
        name="Loadout 4: Clean Minimalist",
        description="British narrator (Ryan), clean lower-third subtitles with subtle shadow, 15% ducking.",
        voice_id="en-GB-RyanNeural",
        speech_rate="+2%",
        subtitle_style="clean_minimal",
        subtitle_position="lower_third",
        highlight_color="&H00FFFFFF&",
        primary_color="&H00E0E0E0&",
        font_size=18,
        outline_width=2,
        bg_audio_volume=0.15,
        hook_card_enabled=False,
    ),
}


class LoadoutManager:
    """Manages loadout profiles and presets."""

    def __init__(self, custom_loadouts: Optional[Dict[str, EditingLoadout]] = None):
        self.loadouts: Dict[str, EditingLoadout] = dict(DEFAULT_LOADOUTS)
        if custom_loadouts:
            self.loadouts.update(custom_loadouts)

    def get_loadout(self, loadout_id: str) -> EditingLoadout:
        return self.loadouts.get(loadout_id, self.loadouts["loadout_1"])

    def list_loadouts(self) -> List[dict]:
        return [l.to_dict() for l in self.loadouts.values()]

    def register_loadout(self, loadout: EditingLoadout):
        self.loadouts[loadout.loadout_id] = loadout
