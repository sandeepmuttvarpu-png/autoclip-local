"""
Audio extraction and faster-whisper ASR with word-level timestamps.
"""

from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import subprocess
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Word:
    word: str
    start: float
    end: float
    probability: float = 1.0

    def to_dict(self) -> dict:
        return {
            "word": self.word,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "probability": round(self.probability, 3),
        }


@dataclass
class Segment:
    id: int
    start: float
    end: float
    text: str
    words: List[Word] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "text": self.text.strip(),
            "words": [w.to_dict() for w in self.words],
        }


@dataclass
class Transcript:
    text: str
    segments: List[Segment] = field(default_factory=list)
    duration: float = 0.0
    language: str = "en"

    def to_dict(self) -> dict:
        return {
            "text": self.text.strip(),
            "duration": round(self.duration, 3),
            "language": self.language,
            "segments": [s.to_dict() for s in self.segments],
        }

    def save_json(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    @classmethod
    def load_json(cls, path: Path) -> "Transcript":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        segments = []
        for s in data.get("segments", []):
            words = [
                Word(
                    word=w["word"],
                    start=w["start"],
                    end=w["end"],
                    probability=w.get("probability", 1.0),
                )
                for w in s.get("words", [])
            ]
            segments.append(
                Segment(
                    id=s["id"],
                    start=s["start"],
                    end=s["end"],
                    text=s["text"],
                    words=words,
                )
            )
        return cls(
            text=data.get("text", ""),
            segments=segments,
            duration=data.get("duration", 0.0),
            language=data.get("language", "en"),
        )


class AudioTranscriber:
    """High-throughput faster-whisper transcriber with word-level alignment."""

    def __init__(
        self,
        model_size: str = "large-v3",
        fallback_model: str = "medium.en",
        device: str = "cuda",
        compute_type: str = "float16",
    ):
        self.model_size = model_size
        self.fallback_model = fallback_model
        self.device = device
        self.compute_type = compute_type
        if self.device == "cpu" and self.compute_type in ("float16", "bfloat16"):
            self.compute_type = "int8"
        self._model = None

    def _load_model(self):
        if self._model is not None:
            return self._model

        from faster_whisper import WhisperModel

        try:
            logger.info(
                f"Loading faster-whisper model '{self.model_size}' on {self.device} ({self.compute_type})..."
            )
            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
            )
        except Exception as e:
            logger.warning(
                f"Failed to load '{self.model_size}' on {self.device}: {e}. Falling back to '{self.fallback_model}'..."
            )
            try:
                self._model = WhisperModel(
                    self.fallback_model,
                    device=self.device,
                    compute_type=self.compute_type,
                )
            except Exception as e_inner:
                logger.warning(
                    f"CUDA fallback failed: {e_inner}. Loading '{self.fallback_model}' on CPU (int8)..."
                )
                self.device = "cpu"
                self.compute_type = "int8"
                self._model = WhisperModel(
                    self.fallback_model,
                    device="cpu",
                    compute_type="int8",
                )
        return self._model

    @staticmethod
    def extract_audio(video_path: Path, output_wav: Path) -> Path:
        """Extract a 16kHz mono 16-bit PCM WAV stream using FFmpeg."""
        video_path = Path(video_path)
        output_wav = Path(output_wav)
        output_wav.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(output_wav),
        ]
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg audio extraction failed: {result.stderr}")
        return output_wav

    def transcribe(
        self,
        media_path: Path,
        language: Optional[str] = None,
        vad_filter: bool = True,
    ) -> Transcript:
        """Transcribe an audio or video file with word-level timestamps."""
        media_path = Path(media_path)
        temp_wav = None

        if media_path.suffix.lower() not in [".wav", ".mp3", ".flac", ".ogg", ".m4a"]:
            temp_wav = media_path.with_suffix(".tmp_extract.wav")
            audio_target = self.extract_audio(media_path, temp_wav)
        else:
            audio_target = media_path

        try:
            model = self._load_model()
            try:
                segments_gen, info = model.transcribe(
                    str(audio_target),
                    beam_size=5,
                    word_timestamps=True,
                    vad_filter=vad_filter,
                    language=language,
                )
                structured_segments = []
                full_text_parts = []
                seg_id = 0

                for seg in segments_gen:
                    words = []
                    if seg.words:
                        for w in seg.words:
                            words.append(
                                Word(
                                    word=w.word,
                                    start=w.start,
                                    end=w.end,
                                    probability=getattr(w, "probability", 1.0),
                                )
                            )
                    structured_segments.append(
                        Segment(
                            id=seg_id,
                            start=seg.start,
                            end=seg.end,
                            text=seg.text,
                            words=words,
                        )
                    )
                    full_text_parts.append(seg.text)
                    seg_id += 1
            except RuntimeError as run_err:
                if self.device != "cpu":
                    logger.warning(
                        f"Whisper inference error on {self.device} ({run_err}). Automatically falling back to CPU (int8)..."
                    )
                    from faster_whisper import WhisperModel
                    self.device = "cpu"
                    self.compute_type = "int8"
                    self._model = WhisperModel(
                        self.fallback_model or self.model_size,
                        device="cpu",
                        compute_type="int8",
                    )
                    segments_gen, info = self._model.transcribe(
                        str(audio_target),
                        beam_size=5,
                        word_timestamps=True,
                        vad_filter=vad_filter,
                        language=language,
                    )
                    structured_segments = []
                    full_text_parts = []
                    seg_id = 0
                    for seg in segments_gen:
                        words = []
                        if seg.words:
                            for w in seg.words:
                                words.append(
                                    Word(
                                        word=w.word,
                                        start=w.start,
                                        end=w.end,
                                        probability=getattr(w, "probability", 1.0),
                                    )
                                )
                        structured_segments.append(
                            Segment(
                                id=seg_id,
                                start=seg.start,
                                end=seg.end,
                                text=seg.text,
                                words=words,
                            )
                        )
                        full_text_parts.append(seg.text)
                        seg_id += 1
                else:
                    raise

            return Transcript(
                text=" ".join(full_text_parts).strip(),
                segments=structured_segments,
                duration=info.duration if info else 0.0,
                language=info.language if info else "en",
            )
        finally:
            if temp_wav and temp_wav.exists():
                try:
                    os.remove(temp_wav)
                except OSError:
                    pass
