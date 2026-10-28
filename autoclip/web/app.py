"""
AutoClip-Local Studio FastAPI Application.
Complete local-first dashboard for stream slicing, story generation,
neural voiceover fusion, audio ducking, and multi-account YouTube scheduling.
"""

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import shutil
import time
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from autoclip.config import AutoClipConfig, default_config
from autoclip.stream.slicer import StreamSlicer
from autoclip.story.generator import StoryGenerator, StoryRequest
from autoclip.story.narrator import StoryNarrator, VOICE_PRESETS
from autoclip.publisher.account_manager import AccountManager

logger = logging.getLogger(__name__)

# Base directories
BASE_DIR = Path(__file__).resolve().parent.parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
LIBRARY_UNPUBLISHED = BASE_DIR / "library" / "unpublished"
LIBRARY_PUBLISHED = BASE_DIR / "library" / "published"
RAW_STREAMS_DIR = BASE_DIR / "raw_streams"

LIBRARY_UNPUBLISHED.mkdir(parents=True, exist_ok=True)
LIBRARY_PUBLISHED.mkdir(parents=True, exist_ok=True)
RAW_STREAMS_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="AutoClip-Local Studio",
    description="Local-First GPU-Accelerated Video Repurposing & Multi-Account YouTube Scheduler",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Shared singletons
slicer = StreamSlicer(nvenc_enabled=True)
story_gen = StoryGenerator()
narrator = StoryNarrator(nvenc_enabled=True)
account_mgr = AccountManager(
    library_unpublished=LIBRARY_UNPUBLISHED,
    library_published=LIBRARY_PUBLISHED,
)

# Background job tracker
jobs_lock = {}


# Pydantic Schemas
class SliceStreamRequest(BaseModel):
    source_type: str = "url"  # "url" or "local_file"
    url: Optional[str] = None
    local_path: Optional[str] = None
    segment_duration: float = 120.0  # 2 minutes default
    max_clips: Optional[int] = None


class GenerateStoryRequest(BaseModel):
    category: str = "aita"  # "aita", "revenge", "drama", "custom"
    custom_prompt: Optional[str] = None
    target_duration_sec: float = 120.0


class RenderStoryRequest(BaseModel):
    stream_name: str
    clip_id: str
    story_text: str
    headline: str
    voice_id: str = "en-US-ChristopherNeural"
    bg_audio_volume: float = 0.12
    voice_volume: float = 1.0


class SingleScheduleRequest(BaseModel):
    stream_name: str
    clip_id: str
    account_id: str
    publish_at: str
    title: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = None


class StaggerScheduleRequest(BaseModel):
    stream_name: str
    account_ids: List[str]
    start_date: Optional[str] = None
    days: int = 7


class RegisterAccountRequest(BaseModel):
    account_id: str
    account_name: str
    client_secret_json: Optional[str] = None
    token_json: Optional[str] = None


# Helpers
def _get_stream_info(folder_path: Path, status: str) -> dict:
    stream_name = folder_path.name
    meta_path = folder_path / "stream_meta.json"
    meta = {}
    if meta_path.exists():
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            pass

    clips = []
    for c_file in sorted(folder_path.glob("*_clip_*.mp4")):
        clip_id = c_file.stem
        c_meta_file = folder_path / f"{clip_id}.json"
        c_meta = {
            "clip_id": clip_id,
            "stream_name": stream_name,
            "file_path": str(c_file),
            "status": status,
            "story_attached": False,
            "story_headline": None,
            "duration": 120.0,
            "created_at": c_file.stat().st_mtime,
        }
        if c_meta_file.exists():
            try:
                with open(c_meta_file, "r", encoding="utf-8") as mf:
                    loaded = json.load(mf)
                    c_meta.update(loaded)
            except Exception:
                pass
        clips.append(c_meta)

    return {
        "stream_name": stream_name,
        "status": status,
        "clip_count": len(clips),
        "total_duration_sec": meta.get("total_duration_sec", len(clips) * 120.0),
        "created_at": meta.get("created_at", folder_path.stat().st_mtime),
        "clips": clips,
    }


# Routes
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    """Render the main Studio Dashboard."""
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/api/status")
def get_system_status():
    """System health check, GPU status, and library counters."""
    unpub_count = len(list(LIBRARY_UNPUBLISHED.glob("*/*_clip_*.mp4")))
    pub_count = len(list(LIBRARY_PUBLISHED.glob("*/*_clip_*.mp4")))
    unpub_streams = len([d for d in LIBRARY_UNPUBLISHED.iterdir() if d.is_dir()])
    pub_streams = len([d for d in LIBRARY_PUBLISHED.iterdir() if d.is_dir()])
    accounts = account_mgr.list_accounts()

    cuda_avail = False
    device_name = "CPU Only"
    try:
        import torch
        cuda_avail = torch.cuda.is_available()
        if cuda_avail:
            device_name = torch.cuda.get_device_name(0)
    except ImportError:
        nv_smi = shutil.which("nvidia-smi")
        if nv_smi:
            try:
                res = subprocess.run(
                    [nv_smi, "--query-gpu=name", "--format=csv,noheader"],
                    stdout=subprocess.PIPE,
                    text=True,
                    timeout=2,
                )
                if res.returncode == 0 and res.stdout.strip():
                    cuda_avail = True
                    device_name = res.stdout.strip().splitlines()[0]
            except Exception:
                pass

    return {
        "status": "online",
        "cuda_available": cuda_avail,
        "gpu_name": device_name,
        "nvenc_enabled": slicer.nvenc_enabled,
        "unpublished_clips_count": unpub_count,
        "published_clips_count": pub_count,
        "unpublished_streams_count": unpub_streams,
        "published_streams_count": pub_streams,
        "accounts_count": len(accounts),
    }


@app.get("/api/streams")
def list_all_streams():
    """List all stream folders and clips in Unpublished and Published sections."""
    unpublished = []
    for d in sorted(LIBRARY_UNPUBLISHED.iterdir()):
        if d.is_dir() and not d.name.startswith("."):
            info = _get_stream_info(d, "unpublished")
            if info["clip_count"] > 0:
                unpublished.append(info)

    published = []
    for d in sorted(LIBRARY_PUBLISHED.iterdir()):
        if d.is_dir() and not d.name.startswith("."):
            info = _get_stream_info(d, "published")
            if info["clip_count"] > 0:
                published.append(info)

    return {
        "unpublished": unpublished,
        "published": published,
    }


@app.post("/api/streams/slice")
def slice_stream_endpoint(req: SliceStreamRequest, background_tasks: BackgroundTasks):
    """Download and slice stream into 2-minute 9:16 shorts."""
    if req.source_type == "url":
        if not req.url:
            raise HTTPException(status_code=400, detail="Missing stream URL")
        try:
            downloaded = slicer.download_stream(req.url, RAW_STREAMS_DIR)
            source_file = downloaded
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Download failed: {e}")
    else:
        if not req.local_path:
            raise HTTPException(status_code=400, detail="Missing local file path")
        source_file = Path(req.local_path)
        if not source_file.exists():
            raise HTTPException(status_code=404, detail=f"File not found: {source_file}")

    try:
        clips = slicer.slice_stream_to_shorts(
            video_path=source_file,
            segment_duration=req.segment_duration,
            library_dir=LIBRARY_UNPUBLISHED,
            max_clips=req.max_clips,
        )
        return {
            "status": "success",
            "stream_name": clips[0].stream_name if clips else source_file.stem,
            "clips_count": len(clips),
            "clips": [c.to_dict() for c in clips],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Slicing failed: {e}")


@app.get("/api/clips/{section}/{stream_name}/{clip_id}/video")
def stream_clip_video(section: str, stream_name: str, clip_id: str, request: Request):
    """
    Stream clip video with HTTP 206 Partial Content range support
    for smooth scrubbing in browser HTML5 player.
    """
    target_dir = LIBRARY_PUBLISHED if section == "published" else LIBRARY_UNPUBLISHED
    clip_path = target_dir / stream_name / f"{clip_id}.mp4"
    if not clip_path.exists():
        raise HTTPException(status_code=404, detail=f"Clip not found: {clip_path}")

    file_size = clip_path.stat().st_size
    range_header = request.headers.get("range")

    if not range_header:
        return FileResponse(
            str(clip_path),
            media_type="video/mp4",
            headers={"Accept-Ranges": "bytes"},
        )

    # Parse Range: bytes=start-end
    try:
        range_str = range_header.replace("bytes=", "").strip()
        parts = range_str.split("-")
        start = int(parts[0]) if parts[0] else 0
        end = int(parts[1]) if len(parts) > 1 and parts[1] else file_size - 1
        start = max(0, min(start, file_size - 1))
        end = max(start, min(end, file_size - 1))
        chunk_size = (end - start) + 1

        def iterfile():
            with open(clip_path, "rb") as f:
                f.seek(start)
                remaining = chunk_size
                while remaining > 0:
                    read_len = min(64 * 1024, remaining)
                    data = f.read(read_len)
                    if not data:
                        break
                    remaining -= len(data)
                    yield data

        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(chunk_size),
            "Content-Type": "video/mp4",
        }
        return StreamingResponse(iterfile(), status_code=206, headers=headers)
    except Exception as e:
        logger.warning(f"Error handling range request: {e}")
        return FileResponse(str(clip_path), media_type="video/mp4")


@app.get("/api/story/voices")
def get_voices():
    """Return available Edge-TTS neural voices."""
    return list(VOICE_PRESETS.values())


@app.post("/api/story/generate")
def generate_story_endpoint(req: GenerateStoryRequest):
    """Generate viral story script and headline via local LLM or fallback."""
    story_req = StoryRequest(
        category=req.category,
        custom_prompt=req.custom_prompt,
        target_duration_sec=req.target_duration_sec,
    )
    result = story_gen.generate_story(story_req)
    return result.to_dict()


@app.post("/api/story/render")
def render_story_endpoint(req: RenderStoryRequest):
    """
    Fuse story voiceover, kinetic pop subtitles, and ducked audio onto 9:16 background.
    """
    unpub_stream = LIBRARY_UNPUBLISHED / req.stream_name
    clip_file = unpub_stream / f"{req.clip_id}.mp4"
    if not clip_file.exists():
        raise HTTPException(status_code=404, detail=f"Unpublished clip file not found: {clip_file}")

    # Output to rendered file
    output_file = unpub_stream / f"{req.clip_id}_story.mp4"

    try:
        render_res = narrator.fuse_story_and_render(
            background_video_path=clip_file,
            story_text=req.story_text,
            headline=req.headline,
            output_video_path=output_file,
            voice_id=req.voice_id,
            bg_audio_volume=req.bg_audio_volume,
            voice_volume=req.voice_volume,
        )

        # Replace original raw cut with story-fused version so it's ready for publishing!
        backup_file = unpub_stream / f"{req.clip_id}_raw.mp4"
        if not backup_file.exists():
            shutil.copy(str(clip_file), str(backup_file))
        shutil.move(str(output_file), str(clip_file))

        return {
            "status": "success",
            "clip_id": req.clip_id,
            "stream_name": req.stream_name,
            "headline": render_res.headline,
            "duration": render_res.duration,
            "voice_id": render_res.voice_id,
            "video_path": str(clip_file),
        }
    except Exception as e:
        logger.error(f"Render failed: {e}")
        raise HTTPException(status_code=500, detail=f"Render failed: {e}")


@app.get("/api/accounts")
def get_accounts():
    """List all registered YouTube channels."""
    accounts = account_mgr.list_accounts()
    return [a.to_dict() for a in accounts]


@app.post("/api/accounts/register")
def register_account_endpoint(req: RegisterAccountRequest):
    """Add or update a YouTube channel account profile."""
    acc = account_mgr.register_account(
        account_id=req.account_id,
        account_name=req.account_name,
        client_secret_json=req.client_secret_json,
        token_json=req.token_json,
    )
    return acc.to_dict()


@app.post("/api/schedule/single")
def schedule_single_endpoint(req: SingleScheduleRequest):
    """
    Schedule a single clip for a specific channel and time.
    Moves clip from Unpublished to Published immediately to prevent duplicate posting.
    """
    try:
        res = account_mgr.schedule_and_publish_clip(
            stream_name=req.stream_name,
            clip_id=req.clip_id,
            account_id=req.account_id,
            publish_at=req.publish_at,
            title=req.title,
            description=req.description,
            tags=req.tags,
            mock_if_no_creds=True,
        )
        return res
    except Exception as e:
        logger.error(f"Scheduling failed: {e}")
        raise HTTPException(status_code=500, detail=f"Scheduling failed: {e}")


@app.post("/api/schedule/stagger")
def schedule_stagger_endpoint(req: StaggerScheduleRequest):
    """
    Batch schedule an entire stream folder across multiple accounts over 7 days.
    """
    try:
        start_dt = None
        if req.start_date:
            clean_str = req.start_date.replace("Z", "+00:00")
            start_dt = datetime.fromisoformat(clean_str)

        results = account_mgr.execute_smart_stagger(
            stream_name=req.stream_name,
            account_ids=req.account_ids,
            start_datetime=start_dt,
            days=req.days,
        )
        return {
            "status": "success",
            "stream_name": req.stream_name,
            "scheduled_count": len(results),
            "results": results,
        }
    except Exception as e:
        logger.error(f"Smart stagger failed: {e}")
        raise HTTPException(status_code=500, detail=f"Smart stagger failed: {e}")
