# 🎬 AutoClip-Local

> **100% Local, GPU-Accelerated Video Clipping & Viral Shorts Repurposing Engine.**  
> *An open-source, private, zero-cloud alternative to Opus Clip and Submagic, engineered for local workstations with NVIDIA GPUs.*

[![Python: 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://python.org)
[![CUDA: Accelerated](https://img.shields.io/badge/CUDA-Accelerated-green.svg)](https://developer.nvidia.com/cuda-toolkit)
[![NVENC: Hardware Encoding](https://img.shields.io/badge/NVENC-H.264%20Fast-76B900.svg)](https://developer.nvidia.com/video-encode-decode-gpu-support-matrix)
[![Privacy: 100% Local](https://img.shields.io/badge/Privacy-100%25%20Local%20(No%20APIs)-brightgreen)](#)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

---

## ⚡ The Physical Problem & Why We Built This

Services like Opus Clip and Submagic charge monthly subscriptions, upload your private raw footage to third-party cloud datacenters, and take several minutes per video. Meanwhile, local consumer workstations with modern NVIDIA GPUs (e.g. RTX 40/50 series) sit idle with dedicated hardware **NVENC** silicon capable of encoding 4K video at 300+ frames per second.

**AutoClip-Local** runs 100% on your local machine with zero external API calls:
1. **Audio Transcription:** Uses `faster-whisper` (`large-v3` / `medium.en`) on CUDA to extract word-level timestamps with sub-millisecond precision.
2. **Semantic Virality & Hook Scoring:** Feeds windowed transcripts into your local LLM (running via **Sandwich.cpp** or `llama-server` at `http://127.0.0.1:8080/v1`) using few-shot structured JSON schemas to identify high-retention 30–60 second narrative hooks.
3. **Vision Active Speaker Tracking & EMA 9:16 Reframing:** Samples video frames, detects facial focal coordinates with MediaPipe / OpenCV, and applies an Exponential Moving Average (EMA) filter:
   $$C_{\text{smooth}}(t) = \alpha C_t + (1 - \alpha) C_{\text{smooth}}(t - 1), \quad \alpha \approx 0.15$$
   Dynamically reframing landscape 16:9 into centered 9:16 vertical shorts without jitter.
4. **Kinetic Word-Level Pop Subtitles:** Generates styled `.ass` (Advanced SubStation Alpha) subtitle tracks with word-level karaoke pop scaling (`\fscx115\fscy115`) and vibrant yellow highlights (`\c&H0000FFFF&`).
5. **Hardware NVENC Rendering:** Burns crops and kinetic subtitles in a single-pass hardware-accelerated FFmpeg pipeline utilizing `h264_nvenc`, rendering 60-second 1080x1920 shorts in under 3 seconds.

---

## 📦 Architecture & Repository Layout

```text
autoclip-local/
├── pyproject.toml              # Build definition and CLI packaging
├── requirements.txt            # Core dependencies
├── autoclip/
│   ├── cli.py                  # CLI entrypoint (autoclip run / scan)
│   ├── config.py               # Hardware detection and config defaults
│   ├── audio/
│   │   └── transcriber.py      # faster-whisper with word-level alignment
│   ├── publisher/
│   │   └── youtube_scheduler.py# Resumable scheduled YouTube Shorts publisher
│   ├── intelligence/
│   │   ├── prompt.py           # Few-shot viral hook scoring prompts
│   │   ├── segmenter.py        # Local LLM chat completion & JSON parser
│   │   └── audio_spike_detector.py # RMS energy Z-score hype moment detector
│   ├── vision/
│   │   ├── face_tracker.py     # MediaPipe & OpenCV face tracking
│   │   ├── reframer.py         # EMA 9:16 vertical crop solver
│   │   ├── gaming_stacked_crop.py # Top 35% webcam + Bottom 65% gameplay stacked cropper
│   │   └── models/             # Bundled offline face detection models
│   ├── subtitle/
│   │   └── ass_builder.py      # Kinetic word-by-word ASS pop karaoke
│   └── render/
│       └── ffmpeg_pipe.py      # Hardware NVENC FFmpeg pipeline with tqdm
└── tests/
    ├── test_smoke.py           # End-to-end synthetic video & NVENC verification
    └── test_addons.py          # YouTube scheduler, gaming crop & audio spike tests
```

---

## 🚀 Quickstart

### 1. Installation

```bash
cd ~/dev/autoclip-local

# Create environment (Python 3.10 - 3.12 recommended)
uv venv .venv --python 3.11
source .venv/bin/activate

# Install dependencies and CLI
uv pip install -e .
```

### 2. Verify Hardware Acceleration

```bash
autoclip --help
```

### 3. Extract Viral Shorts from Long-Form Video

```bash
# Run full pipeline: extracts top 3 viral hooks and renders 1080x1920 MP4s with burned subtitles
autoclip run input_podcast.mp4 --out-dir ./output_shorts --top-k 3

# With custom local model endpoint (e.g. Sandwich.cpp) and strict virality score threshold
autoclip run input_podcast.mp4 \
  --llm-url http://127.0.0.1:8080/v1 \
  --min-score 85 \
  --top-k 5

# Dry-run: inspect transcription and virality candidate scores without rendering
autoclip scan input_podcast.mp4 --json

# Gaming stacked 9:16 layout (Facecam Top 35% + Gameplay Bottom 65%)
autoclip gaming gameplay.mp4 --start 120.0 --end 165.0

# Detect screaming, hype plays, and team cheering moments via audio RMS energy spikes
autoclip detect-hype gameplay.mp4 --threshold-std 2.5 --min-duration 3.0

# Schedule automatic upload to YouTube Shorts
autoclip publish output_shorts/clip_01.mp4 \
  --title "Why 8GB VRAM is Enough for 120B Models" \
  --publish-at "2026-10-28T18:00:00Z" \
  --tags "AI,Hardware,Shorts"
```
autoclip scan input_podcast.mp4 --json
```

---

## 🧪 Testing & Verification

Run the end-to-end smoke test suite:

```bash
pytest -v tests/test_smoke.py
```

All 4 test phases execute in ~3 seconds:
- `test_reframer_aspect_ratio`: Verifies 9:16 vertical crop math and boundary clamping.
- `test_kinetic_ass_builder`: Verifies word-level ASS pop karaoke tags and relative time offsets.
- `test_segmenter_json_parsing`: Verifies robust JSON schema extraction with markdown fallbacks.
- `test_end_to_end_nvenc_render`: Generates a synthetic 16:9 test video, tracks focal points, generates subtitles, and renders an output MP4 at **1080x1920 (9:16)** via `h264_nvenc`.

---

## 📄 License

MIT License. Designed and built by [Sandeep Muttvarpu](https://github.com/sandeepmuttvarpu-png).
