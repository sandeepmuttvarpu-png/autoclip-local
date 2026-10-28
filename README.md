# 🎬 AutoClip-Local

> **100% Local, GPU-Accelerated Video Clipping & Viral Shorts Repurposing Engine.**  
> *An open-source, private, zero-cloud alternative to Opus Clip and Submagic, engineered for local workstations with NVIDIA GPUs.*

[![Python: 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://python.org)
[![CUDA: Accelerated](https://img.shields.io/badge/CUDA-Accelerated-green.svg)](https://developer.nvidia.com/cuda-toolkit)
[![NVENC: Hardware Encoding](https://img.shields.io/badge/NVENC-H.264%20Fast-76B900.svg)](https://developer.nvidia.com/video-encode-decode-gpu-support-matrix)
[![Privacy: 100% Local](https://img.shields.io/badge/Privacy-100%25%20Local%20(No%20APIs)-brightgreen)](#)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

---

## ⚡ Core Capabilities & Studio Workflow

### 1. Continuous 1–5 Hour Stream Slicing (`StreamSlicer`)
- Ingest massive 1-to-5 hour live streams directly from Twitch / YouTube (via `yt-dlp`) or local MP4/MKV recordings.
- Chunks continuous footage into back-to-back **2-minute 9:16 vertical shorts** (1080x1920) using hardware NVENC.
- Organizes clips into dedicated stream folders under the **Unpublished Vault** (`library/unpublished/<stream_name>/`).

### 2. Story & Neural Voiceover Fusion Studio (`StoryNarrator`)
- Recreates the high-retention viral style of [HarryTalks YouTube Shorts](https://youtube.com/shorts/H3spw3wzw24):
  - **Drama & AITA Narratives:** Automatically generates gripping 250–350 word first-person stories via local LLM (Sandwich.cpp / llama-server) or curated templates.
  - **Neural Voice Synthesis:** High-fidelity voiceover via Edge-TTS (`Christopher`, `Guy`, `Aria`, `Jenny`, `Eric`, `Ryan`) with zero cloud API keys or fees.
  - **Audio Ducking:** Automatically ducks background game audio to **10–15%** volume while boosting the narrative voiceover to **100%**.
  - **Kinetic Karaoke Subtitles:** Generates animated `.ass` subtitles with word-by-word scaling pops (`\fscx115\fscy115`) and bright yellow highlights (`\c&H0000FFFF&`).
  - **Opening Hook Card:** Renders a 3.5-second top banner card with the viral headline to immediately capture viewer retention.

### 3. Multi-Channel YouTube Scheduler & Smart Stagger (`AccountManager`)
- **Supports $\ge 3$ Independent Channels:** Manage separate OAuth credentials and channel switcher configurations.
- **Strict Duplicate Prevention:** When a short is scheduled, it is automatically migrated from `library/unpublished/<stream>/` to `library/published/<stream>/` with an auditable publication receipt.
- **1-Click Smart Stagger:** Distributes 100+ clips across your connected channels over a 7-day period during peak viewer hours (12:00, 17:00, 21:00 UTC).

### 4. Interactive Web Studio Dashboard
- Modern dark-mode responsive dashboard (FastAPI + Tailwind CSS + Lucide Icons).
- Byte-range HTTP 206 streaming for smooth video scrubbing.
- Live system telemetry (NVIDIA RTX 5050 Mobile NVENC, Whisper CUDA, Edge-TTS Neural).

---

## 📦 Repository Layout

```text
autoclip-local/
├── Dockerfile                  # CUDA 12.2 + FFmpeg NVENC container definition
├── docker-compose.yml          # One-click GPU passthrough deployment
├── pyproject.toml              # Build definition and CLI packaging
├── requirements.txt            # Core dependencies
├── library/
│   ├── unpublished/            # Vault of sliced clips awaiting story & schedule
│   └── published/              # Archived clips scheduled/published to YouTube
├── raw_streams/                # Storage for ingested 1-5 hr stream VODs
├── autoclip/
│   ├── cli.py                  # CLI commands (studio, slice, fuse-story, publish)
│   ├── config.py               # Hardware detection and config defaults
│   ├── stream/
│   │   └── slicer.py           # 1-to-5 hour stream download & 2-min vertical slicer
│   ├── story/
│   │   ├── generator.py        # Local LLM viral story generation & curated fallbacks
│   │   └── narrator.py         # TTS synthesis, audio ducking, and ASS fusion
│   ├── publisher/
│   │   ├── account_manager.py  # Multi-channel manager & Smart Stagger scheduler
│   │   └── youtube_scheduler.py# Resumable scheduled YouTube upload client
│   ├── web/
│   │   ├── app.py              # FastAPI backend API
│   │   └── templates/
│   │       └── index.html      # Complete interactive Studio UI
│   ├── audio/
│   │   └── transcriber.py      # faster-whisper with word-level alignment
│   ├── intelligence/
│   │   ├── prompt.py           # Few-shot viral hook scoring prompts
│   │   ├── segmenter.py        # Local LLM virality ranking & JSON parser
│   │   └── audio_spike_detector.py # RMS energy Z-score hype moment detector
│   ├── vision/
│   │   ├── face_tracker.py     # MediaPipe & OpenCV face tracking
│   │   ├── reframer.py         # EMA 9:16 vertical crop solver
│   │   └── gaming_stacked_crop.py # Top 35% webcam + Bottom 65% gameplay stacked cropper
└── tests/
    ├── test_smoke.py           # Reframing, subtitles, and NVENC render tests
    ├── test_addons.py          # Publisher, stacked cropper, and hype detector tests
    └── test_studio.py          # Studio slicing, story fusion, scheduler, and API tests
```

---

## 🚀 Quickstart Guide

### Option A: Local Python Workstation

```bash
# 1. Clone & create virtual environment
git clone https://github.com/your-username/autoclip-local.git
cd autoclip-local
python3 -m venv .venv
source .venv/bin/activate

# 2. Install package in editable mode
pip install -e .

# 3. Launch the Studio Dashboard
autoclip studio --port 8501
```
Open your browser at **`http://localhost:8501`**.

---

### Option B: Docker with NVIDIA GPU Passthrough

```bash
# Build and launch with docker-compose
docker compose up -d

# View container logs
docker compose logs -f autoclip-studio
```
The studio dashboard is accessible at **`http://localhost:8501`**.

---

## 🛠️ CLI Workflows

### 1. Slice a 1–5 hr Stream into 2-Minute Shorts
```bash
# Download and slice Twitch or YouTube VOD
autoclip slice "https://www.youtube.com/watch?v=..." --duration 120

# Slice a local stream recording
autoclip slice "/path/to/stream.mp4" --duration 120 --output-dir library/unpublished
```

### 2. Fuse Story, Voiceover, & Subtitles
```bash
autoclip fuse-story library/unpublished/my_stream/my_stream_clip_001.mp4 \
  --category aita \
  --voice en-US-ChristopherNeural
```

### 3. Schedule YouTube Short
```bash
autoclip publish library/unpublished/my_stream/my_stream_clip_001.mp4 \
  --title "AITA For Exposing My Fiancée's Secret Account? #Shorts" \
  --publish-at "2026-10-29T18:00:00Z"
```

---

## 🧪 Testing & Verification

Run the full automated test suite (18 unit, integration, and studio tests):
```bash
pytest -v
```

---

## 📄 License
MIT License. Free and open source for creators and developers.
