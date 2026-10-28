"""
Kinetic word-by-word ASS (Advanced SubStation Alpha) subtitle generator.
Produces viral pop-karaoke subtitles (uppercase, center-bottom, scale pop, bright yellow highlight).
"""

from pathlib import Path
from typing import List
import re

from autoclip.audio.transcriber import Word


class AssSubtitleBuilder:
    """Builds animated .ass subtitle tracks with word-level kinetic popping."""

    def __init__(
        self,
        font_name: str = "Montserrat ExtraBold",
        font_size: int = 80,
        alignment: int = 5,  # 5 = Middle-Center (dead center of screen for 9:16 Shorts)
        margin_v: int = 0,   # Centered on vertical axis
        highlight_color: str = "&H0000FFFF&",  # Yellow in ASS BGR format
        primary_color: str = "&H00FFFFFF&",    # Pure White
        outline_color: str = "&H00000000&",    # Pure Black
        max_words_per_line: int = 3,           # 3 words per line is punchy for center display
    ):
        self.font_name = font_name
        self.font_size = font_size
        self.alignment = alignment
        self.margin_v = margin_v
        self.highlight_color = highlight_color
        self.primary_color = primary_color
        self.outline_color = outline_color
        self.max_words_per_line = max_words_per_line

    @staticmethod
    def _format_time(seconds: float) -> str:
        """Format seconds into ASS timestamp format: H:MM:SS.cs"""
        seconds = max(0.0, seconds)
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        centis = int(round((seconds - int(seconds)) * 100))
        if centis >= 100:
            centis = 99
        return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"

    @staticmethod
    def _clean_word(word: str) -> str:
        """Strip extra punctuation while preserving apostrophes."""
        w = word.strip().upper()
        # Clean up any ASS curly braces
        w = re.sub(r"[{}]", "", w)
        return w

    def build_ass(
        self,
        words: List[Word],
        clip_start: float,
        clip_end: float,
        output_path: Path,
    ) -> Path:
        """
        Generates an animated ASS file for words within [clip_start, clip_end].
        Timestamps are shifted relative to clip_start (00:00:00.00).
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # 1. Filter words within bounds
        relevant: List[Word] = []
        for w in words:
            # Overlap check
            if w.end >= clip_start and w.start <= clip_end:
                # Clamp boundaries
                w_start = max(0.0, w.start - clip_start)
                w_end = max(w_start + 0.05, min(clip_end - clip_start, w.end - clip_start))
                clean_text = self._clean_word(w.word)
                if clean_text:
                    relevant.append(
                        Word(
                            word=clean_text,
                            start=w_start,
                            end=w_end,
                            probability=w.probability,
                        )
                    )

        # 2. Chunk words into 3–4 words per line
        chunks: List[List[Word]] = []
        current_chunk: List[Word] = []

        for w in relevant:
            current_chunk.append(w)
            if len(current_chunk) >= self.max_words_per_line:
                chunks.append(current_chunk)
                current_chunk = []
        if current_chunk:
            chunks.append(current_chunk)

        # 3. Generate ASS header
        header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{self.font_name},{self.font_size},{self.primary_color},&H000000FF&,{self.outline_color},&H80000000&,-1,0,0,0,100,100,2,0,1,6,3,{self.alignment},60,60,{self.margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

        events = []

        # 4. Generate kinetic events
        for chunk in chunks:
            chunk_start = chunk[0].start
            chunk_end = chunk[-1].end

            # For each word in the chunk, generate a time-slice event where that word is highlighted
            for i, active_w in enumerate(chunk):
                w_start = active_w.start
                # The word stays highlighted until next word starts, or until chunk ends
                w_end = chunk[i + 1].start if (i + 1 < len(chunk)) else chunk_end
                if w_end <= w_start:
                    w_end = w_start + 0.15

                # Build the line text with the active word popped & colored
                line_parts = []
                for j, target_w in enumerate(chunk):
                    if j == i:
                        # Active popped word: scale pop to 115% and bright yellow
                        popped = f"{{\\fscx115\\fscy115\\c{self.highlight_color}}}{target_w.word}{{\\fscx100\\fscy100\\c{self.primary_color}}}"
                        line_parts.append(popped)
                    else:
                        line_parts.append(target_w.word)

                text_content = " ".join(line_parts)
                start_str = self._format_time(w_start)
                end_str = self._format_time(w_end)
                event_line = f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{text_content}"
                events.append(event_line)

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(header)
            f.write("\n".join(events))
            f.write("\n")

        return output_path
