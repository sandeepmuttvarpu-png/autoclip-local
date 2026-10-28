"""
Story Voiceover Synthesis, Audio Ducking, and Kinetic Subtitle Fusion Engine.
Transforms silent or game clips into finished viral shorts by:
1. Synthesizing neural voiceover via Edge-TTS (Christopher, Guy, Aria, etc.)
2. Transcribing audio for exact word-level timestamps via AudioTranscriber
3. Generating animated pop-karaoke ASS subtitles + opening hook title card
4. Ducking background game audio to 10-15% and mixing voiceover at 100%
5. Burning subtitles and rendering final 1080x1920 MP4 via hardware NVENC
"""

import asyncio
from dataclasses import dataclass
import json
import logging
from pathlib import Path
import shutil
import subprocess
import time
from typing import List, Optional

import edge_tts

from autoclip.audio.transcriber import AudioTranscriber, Word
from autoclip.subtitle.ass_builder import AssSubtitleBuilder

logger = logging.getLogger(__name__)

# Popular Edge-TTS neural voices for Shorts / TikTok
VOICE_PRESETS = {
    "christopher": {
        "id": "en-US-ChristopherNeural",
        "name": "Christopher (Authoritative / Narrative Male)",
        "gender": "Male",
        "recommended": True,
    },
    "guy": {
        "id": "en-US-GuyNeural",
        "name": "Guy (Casual / Reddit Story Male)",
        "gender": "Male",
        "recommended": True,
    },
    "aria": {
        "id": "en-US-AriaNeural",
        "name": "Aria (Expressive / Dynamic Female)",
        "gender": "Female",
        "recommended": False,
    },
    "jenny": {
        "id": "en-US-JennyNeural",
        "name": "Jenny (Clear / Conversational Female)",
        "gender": "Female",
        "recommended": False,
    },
    "eric": {
        "id": "en-US-EricNeural",
        "name": "Eric (Young / Energetic Male)",
        "gender": "Male",
        "recommended": False,
    },
    "ryan": {
        "id": "en-GB-RyanNeural",
        "name": "Ryan (British Male)",
        "gender": "Male",
        "recommended": False,
    },
    "sonia": {
        "id": "en-GB-SoniaNeural",
        "name": "Sonia (British Female)",
        "gender": "Female",
        "recommended": False,
    },
}


@dataclass
class StoryRenderResult:
    final_video_path: str
    voice_audio_path: str
    ass_subtitle_path: str
    duration: float
    headline: str
    voice_id: str


class StoryNarrator:
    """Handles neural TTS generation, subtitle burning, and audio ducking."""

    def __init__(
        self,
        nvenc_enabled: bool = True,
        whisper_model: str = "base",
        whisper_device: str = "auto",
    ):
        self.nvenc_enabled = nvenc_enabled
        self.whisper_model = whisper_model
        self.whisper_device = whisper_device
        self.ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"
        self.ffprobe_bin = shutil.which("ffprobe") or "ffprobe"
        self._transcriber: Optional[AudioTranscriber] = None

    def _get_transcriber(self) -> AudioTranscriber:
        if self._transcriber is None:
            dev = self.whisper_device
            if dev == "auto":
                try:
                    import torch
                    dev = "cuda" if torch.cuda.is_available() else "cpu"
                except Exception:
                    dev = "cpu"
            compute_type = "float16" if dev == "cuda" else "int8"
            self._transcriber = AudioTranscriber(
                model_size=self.whisper_model,
                fallback_model="base",
                device=dev,
                compute_type=compute_type,
            )
        return self._transcriber

    def get_duration(self, media_path: Path) -> float:
        """Query duration in seconds using ffprobe."""
        cmd = [
            self.ffprobe_bin,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(media_path),
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            return float(res.stdout.strip())
        except ValueError:
            return 0.0

    def get_dimensions(self, video_path: Path) -> tuple:
        """Query width and height of video stream."""
        cmd = [
            self.ffprobe_bin,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=s=x:p=0",
            str(video_path),
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            w, h = map(int, res.stdout.strip().split("x"))
            return w, h
        except Exception:
            return 1080, 1920

    def has_audio_stream(self, video_path: Path) -> bool:
        """Check if video file has at least one audio track."""
        cmd = [
            self.ffprobe_bin,
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            str(video_path),
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return bool(res.stdout.strip())

    async def synthesize_voice_async(
        self,
        text: str,
        voice_id: str = "en-US-ChristopherNeural",
        output_path: Optional[Path] = None,
        rate: str = "+0%",
        pitch: str = "+0Hz",
    ) -> Path:
        """Synthesize story voiceover using Edge-TTS."""
        if output_path is None:
            output_path = Path("/tmp") / f"story_tts_{int(time.time())}.mp3"
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        comm = edge_tts.Communicate(
            text=text,
            voice=voice_id,
            rate=rate,
            pitch=pitch,
        )
        await comm.save(str(output_path))
        return output_path

    def synthesize_voice(
        self,
        text: str,
        voice_id: str = "en-US-ChristopherNeural",
        output_path: Optional[Path] = None,
        rate: str = "+0%",
        pitch: str = "+0Hz",
    ) -> Path:
        """Synchronous wrapper for synthesize_voice_async."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as pool:
                    return pool.submit(
                        asyncio.run,
                        self.synthesize_voice_async(text, voice_id, output_path, rate, pitch),
                    ).result()
            return loop.run_until_complete(
                self.synthesize_voice_async(text, voice_id, output_path, rate, pitch)
            )
        except RuntimeError:
            return asyncio.run(self.synthesize_voice_async(text, voice_id, output_path, rate, pitch))

    def generate_subtitles_with_hook_card(
        self,
        words: List[Word],
        duration: float,
        output_ass_path: Path,
        headline: Optional[str] = None,
        alignment: int = 5,
        margin_v: int = 0,
    ) -> Path:
        """
        Builds kinetic pop-karaoke ASS subtitles, optionally injecting
        a viral 3.5s opening hook title banner at the top/center.
        """
        builder = AssSubtitleBuilder(
            alignment=alignment,
            margin_v=margin_v,
        )
        builder.build_ass(
            words=words,
            clip_start=0.0,
            clip_end=duration,
            output_path=output_ass_path,
        )

        if headline:
            clean_headline = headline.strip().upper().replace('"', "")
            # Read existing ASS and inject hook banner style and dialogue
            with open(output_ass_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Add HookCard style if not already present
            hook_style = (
                "Style: HookCard,Montserrat ExtraBold,44,&H00FFFFFF&,&H000000FF&,"
                "&H00000000&,&HCC000000&,-1,0,0,0,100,100,0,0,3,10,0,8,80,80,240,1\n"
            )
            content = content.replace("[Events]\n", f"{hook_style}\n[Events]\n")

            # Insert headline dialogue event from 00:00:00.00 to 00:00:03.80
            hook_dialogue = (
                f"Dialogue: 1,0:00:00.00,0:00:03.80,HookCard,,0,0,0,,"
                f"{{\\fad(200,300)}}{clean_headline}\n"
            )
            format_line = "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            content = content.replace(format_line, f"{format_line}{hook_dialogue}")

            with open(output_ass_path, "w", encoding="utf-8") as f:
                f.write(content)

        return output_ass_path

    def fuse_story_and_render(
        self,
        background_video_path: Path,
        story_text: str,
        headline: str,
        output_video_path: Path,
        voice_id: str = "en-US-ChristopherNeural",
        bg_audio_volume: float = 0.12,  # Duck background game audio to 12%
        voice_volume: float = 1.0,
        alignment: int = 5,  # 5 = Screen Center (Middle)
        margin_v: int = 0,
        progress_callback=None,
    ) -> StoryRenderResult:
        """
        End-to-end fusion pipeline:
        1. Synthesize voiceover MP3
        2. Transcribe voiceover for word timestamps
        3. Build kinetic pop-karaoke ASS subtitle with hook card
        4. Render final video with NVENC, audio ducking, and subtitle burn-in
        """
        background_video_path = Path(background_video_path).resolve()
        output_video_path = Path(output_video_path).resolve()
        output_video_path.parent.mkdir(parents=True, exist_ok=True)

        work_dir = output_video_path.parent / f".tmp_{output_video_path.stem}"
        work_dir.mkdir(parents=True, exist_ok=True)

        voice_audio_path = work_dir / "voiceover.mp3"
        ass_path = work_dir / "subtitles.ass"

        logger.info(f"Synthesizing voiceover with '{voice_id}'...")
        if progress_callback:
            progress_callback(10, "Synthesizing neural voiceover...")
        self.synthesize_voice(
            text=story_text,
            voice_id=voice_id,
            output_path=voice_audio_path,
        )

        voice_duration = self.get_duration(voice_audio_path)
        if voice_duration <= 0.0:
            raise RuntimeError(f"Voiceover synthesis failed or empty: {voice_audio_path}")

        logger.info(f"Voiceover synthesized ({voice_duration:.1f}s). Transcribing for word timestamps...")
        if progress_callback:
            progress_callback(35, "Generating kinetic word timestamps...")

        transcriber = self._get_transcriber()
        transcript = transcriber.transcribe(voice_audio_path)
        all_words = [w for s in transcript.segments for w in s.words]

        logger.info(f"Generating ASS subtitles ({len(all_words)} words)...")
        if progress_callback:
            progress_callback(55, "Building animated pop-karaoke subtitles...")

        self.generate_subtitles_with_hook_card(
            words=all_words,
            duration=voice_duration,
            output_ass_path=ass_path,
            headline=headline,
            alignment=alignment,
            margin_v=margin_v,
        )

        # Assemble and render via FFmpeg
        logger.info("Executing FFmpeg audio ducking and subtitle burn-in...")
        if progress_callback:
            progress_callback(75, "Rendering final 9:16 short with NVENC...")

        has_bg_audio = self.has_audio_stream(background_video_path)
        escaped_ass = str(ass_path).replace("\\", "/").replace(":", r"\:")

        in_w, in_h = self.get_dimensions(background_video_path)
        if in_w > in_h:
            # Landscape -> 9:16 vertical crop centered and scaled to 1080x1920
            v_prep = "crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=1080:1920,"
        elif in_w != 1080 or in_h != 1920:
            v_prep = "scale=1080:1920,"
        else:
            v_prep = ""

        cmd = [
            self.ffmpeg_bin,
            "-y",
            # Loop background video if shorter than voiceover
            "-stream_loop",
            "-1",
            "-i",
            str(background_video_path),
            "-i",
            str(voice_audio_path),
            "-t",
            f"{voice_duration:.3f}",
        ]

        # Audio ducking and subtitle filter
        if has_bg_audio:
            filter_complex = (
                f"[0:v]{v_prep}ass='{escaped_ass}'[vout]; "
                f"[0:a]volume={bg_audio_volume:.2f}[bg]; "
                f"[1:a]volume={voice_volume:.2f}[voice]; "
                f"[bg][voice]amix=inputs=2:duration=first:dropout_transition=2[aout]"
            )
        else:
            filter_complex = (
                f"[0:v]{v_prep}ass='{escaped_ass}'[vout]; "
                f"[1:a]volume={voice_volume:.2f}[aout]"
            )

        cmd.extend(["-filter_complex", filter_complex, "-map", "[vout]", "-map", "[aout]"])

        if self.nvenc_enabled:
            cmd.extend(
                [
                    "-c:v",
                    "h264_nvenc",
                    "-preset",
                    "p6",
                    "-tune",
                    "hq",
                    "-b:v",
                    "6M",
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
                    "22",
                ]
            )

        cmd.extend(["-c:a", "aac", "-b:a", "192k", str(output_video_path)])

        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            if self.nvenc_enabled:
                logger.warning(f"NVENC render failed. Retrying with libx264 fallback: {res.stderr}")
                self.nvenc_enabled = False
                # Re-run with CPU
                return self.fuse_story_and_render(
                    background_video_path=background_video_path,
                    story_text=story_text,
                    headline=headline,
                    output_video_path=output_video_path,
                    voice_id=voice_id,
                    bg_audio_volume=bg_audio_volume,
                    voice_volume=voice_volume,
                    progress_callback=progress_callback,
                )
            raise RuntimeError(f"FFmpeg render failed: {res.stderr}")

        if progress_callback:
            progress_callback(100, "Rendering complete!")

        # Update metadata JSON for clip if in same directory
        meta_candidates = list(output_video_path.parent.glob(f"{output_video_path.stem}.json"))
        if not meta_candidates:
            # Check original clip name if output has suffix
            orig_stem = background_video_path.stem
            meta_candidates = list(output_video_path.parent.glob(f"{orig_stem}.json"))

        for m_file in meta_candidates:
            try:
                with open(m_file, "r", encoding="utf-8") as f:
                    meta_data = json.load(f)
                meta_data["story_attached"] = True
                meta_data["story_headline"] = headline
                meta_data["voice_id"] = voice_id
                meta_data["final_short_path"] = str(output_video_path)
                with open(m_file, "w", encoding="utf-8") as f:
                    json.dump(meta_data, f, indent=2)
            except Exception as e:
                logger.warning(f"Failed to update metadata {m_file}: {e}")

        logger.info(f"✓ Story short successfully rendered: {output_video_path}")
        return StoryRenderResult(
            final_video_path=str(output_video_path),
            voice_audio_path=str(voice_audio_path),
            ass_subtitle_path=str(ass_path),
            duration=voice_duration,
            headline=headline,
            voice_id=voice_id,
        )
