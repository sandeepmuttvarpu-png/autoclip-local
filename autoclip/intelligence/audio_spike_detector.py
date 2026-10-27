"""
Audio Spike & Hype Moment Detector (RMS Energy Z-Score Analyzer).
Detects high-energy spikes (screaming, team celebrations, hype plays, laughing) in gaming and podcast streams.
"""

from dataclasses import dataclass
import logging
from pathlib import Path
import shutil
import subprocess
from typing import List, Optional, Tuple
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class HypeMoment:
    start_time: float
    end_time: float
    peak_intensity: float  # Z-score: std deviations above baseline
    peak_time: float
    duration: float
    peak_db: float

    def to_dict(self) -> dict:
        return {
            "start_time": round(self.start_time, 2),
            "end_time": round(self.end_time, 2),
            "duration": round(self.duration, 2),
            "peak_intensity": round(self.peak_intensity, 2),
            "peak_time": round(self.peak_time, 2),
            "peak_db": round(self.peak_db, 2),
        }


class AudioSpikeDetector:
    """
    Ingests audio and computes sliding-window RMS decibels to detect sustained
    acoustic energy spikes >= 2.5 std deviations above rolling baseline for >= 3 seconds.
    """

    def __init__(
        self,
        window_sec: float = 1.0,
        hop_sec: float = 0.25,
        threshold_std: float = 2.5,
        min_consecutive_sec: float = 3.0,
        rolling_baseline_window_sec: float = 60.0,
    ):
        self.window_sec = window_sec
        self.hop_sec = hop_sec
        self.threshold_std = threshold_std
        self.min_consecutive_sec = min_consecutive_sec
        self.rolling_baseline_window_sec = rolling_baseline_window_sec

    @staticmethod
    def _extract_pcm_wav(media_path: Path, output_wav: Path) -> Path:
        """Extract 16kHz mono 16-bit PCM WAV using FFmpeg."""
        ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"
        cmd = [
            ffmpeg_bin,
            "-y",
            "-i",
            str(media_path),
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(output_wav),
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"FFmpeg audio extraction failed: {res.stderr}")
        return output_wav

    def detect_spikes_from_array(
        self,
        samples: np.ndarray,
        sample_rate: int,
    ) -> List[HypeMoment]:
        """
        Process raw 1D audio sample array and detect sustained hype spikes.
        """
        if len(samples) == 0:
            return []

        # Convert to float32 in range [-1.0, 1.0]
        if np.issubdtype(samples.dtype, np.integer):
            max_val = float(np.iinfo(samples.dtype).max)
            audio = samples.astype(np.float32) / max_val
        else:
            audio = samples.astype(np.float32)

        window_len = int(self.window_sec * sample_rate)
        hop_len = int(self.hop_sec * sample_rate)

        if len(audio) < window_len:
            return []

        # Sliding window RMS and dB
        rms_values = []
        timestamps = []

        for start in range(0, len(audio) - window_len + 1, hop_len):
            frame = audio[start : start + window_len]
            rms = np.sqrt(np.mean(frame ** 2) + 1e-12)
            db = 20.0 * np.log10(max(rms, 1e-5))
            rms_values.append(db)
            timestamps.append(start / float(sample_rate))

        db_arr = np.array(rms_values, dtype=np.float32)
        ts_arr = np.array(timestamps, dtype=np.float32)

        # Compute rolling baseline and robust standard deviation (Median + MAD)
        # Using Median Absolute Deviation prevents loud bursts from corrupting the baseline
        frames_in_baseline = max(10, int(self.rolling_baseline_window_sec / self.hop_sec))
        half_w = frames_in_baseline // 2

        z_scores = np.zeros_like(db_arr)
        for i in range(len(db_arr)):
            b_start = max(0, i - half_w)
            b_end = min(len(db_arr), i + half_w)
            local_window = db_arr[b_start:b_end]
            local_median = float(np.median(local_window))
            # 1.4826 * MAD equals normal standard deviation for Gaussian distributions
            local_mad = float(np.median(np.abs(local_window - local_median)))
            local_std = max(1.4826 * local_mad, 0.5)  # floor at 0.5 dB to prevent divide-by-zero
            z_scores[i] = (db_arr[i] - local_median) / local_std

        # Find contiguous runs of frames where z_score >= threshold_std
        min_frames = int(round(self.min_consecutive_sec / self.hop_sec))
        spike_mask = z_scores >= self.threshold_std

        runs: List[Tuple[int, int]] = []
        in_run = False
        run_start = 0

        for idx, is_spike in enumerate(spike_mask):
            if is_spike and not in_run:
                in_run = True
                run_start = idx
            elif not is_spike and in_run:
                in_run = False
                if (idx - run_start) >= min_frames:
                    runs.append((run_start, idx - 1))

        if in_run and (len(spike_mask) - run_start) >= min_frames:
            runs.append((run_start, len(spike_mask) - 1))

        # Merge runs that are separated by <= 2.0 seconds
        merge_gap_frames = int(2.0 / self.hop_sec)
        merged_runs: List[Tuple[int, int]] = []
        for r_start, r_end in runs:
            if not merged_runs:
                merged_runs.append((r_start, r_end))
            else:
                last_start, last_end = merged_runs[-1]
                if r_start - last_end <= merge_gap_frames:
                    merged_runs[-1] = (last_start, r_end)
                else:
                    merged_runs.append((r_start, r_end))

        # Construct HypeMoment objects with pre/post padding
        moments: List[HypeMoment] = []
        for r_start, r_end in merged_runs:
            raw_start = ts_arr[r_start]
            raw_end = ts_arr[r_end] + self.window_sec

            # Add 2s lead-in and 2s trail-out for natural video flow
            padded_start = max(0.0, raw_start - 2.0)
            padded_end = min(len(audio) / float(sample_rate), raw_end + 2.0)

            run_z = z_scores[r_start : r_end + 1]
            run_db = db_arr[r_start : r_end + 1]
            max_idx = np.argmax(run_z)
            peak_z = float(run_z[max_idx])
            peak_t = float(ts_arr[r_start + max_idx])
            peak_db = float(run_db[max_idx])

            moments.append(
                HypeMoment(
                    start_time=padded_start,
                    end_time=padded_end,
                    peak_intensity=peak_z,
                    peak_time=peak_t,
                    duration=padded_end - padded_start,
                    peak_db=peak_db,
                )
            )

        # Sort by peak intensity descending
        moments.sort(key=lambda m: m.peak_intensity, reverse=True)
        return moments

    def detect_spikes(self, media_path: Path) -> List[HypeMoment]:
        """Ingest audio from video/audio path and return detected hype moments."""
        from scipy.io import wavfile

        media_path = Path(media_path)
        temp_wav = None

        if media_path.suffix.lower() == ".wav":
            target_wav = media_path
        else:
            temp_wav = media_path.with_suffix(".tmp_spike.wav")
            target_wav = self._extract_pcm_wav(media_path, temp_wav)

        try:
            sr, data = wavfile.read(str(target_wav))
            # If stereo, average channels
            if data.ndim > 1:
                data = np.mean(data, axis=1)
            return self.detect_spikes_from_array(data, sr)
        finally:
            if temp_wav and temp_wav.exists():
                try:
                    temp_wav.unlink()
                except OSError:
                    pass
