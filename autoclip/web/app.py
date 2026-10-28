"""
AutoClip-Local Web Studio Dashboard API.
Provides asynchronous background job execution, Method C batch story fusion,
Editing Loadouts, multi-channel YouTube scheduling, and HTTP 206 video streaming.
"""

import asyncio
from dataclasses import asdict
import json
import logging
from pathlib import Path
import shutil
import time
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Request, Response, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from autoclip.config import AutoClipConfig, default_config
from autoclip.core.job_queue import job_queue
from autoclip.stream.slicer import StreamSlicer
from autoclip.story.loadouts import LoadoutManager, DEFAULT_LOADOUTS
from autoclip.story.narrator import StoryNarrator, VOICE_PRESETS
from autoclip.story.batch_assigner import StoryVault, BatchStoryAssigner
from autoclip.story.generator import StoryGenerator, StoryRequest
from autoclip.publisher.account_manager import AccountManager

logger = logging.getLogger(__name__)

# Base directories
BASE_DIR = Path(__file__).resolve().parent.parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
LIBRARY_UNPUBLISHED = BASE_DIR / "library" / "unpublished"
LIBRARY_PUBLISHED = BASE_DIR / "library" / "published"
RAW_STREAMS_DIR = BASE_DIR / "raw_streams"
STORIES_DIR = BASE_DIR / "stories"

LIBRARY_UNPUBLISHED.mkdir(parents=True, exist_ok=True)
LIBRARY_PUBLISHED.mkdir(parents=True, exist_ok=True)
RAW_STREAMS_DIR.mkdir(parents=True, exist_ok=True)
STORIES_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="AutoClip-Local Web Studio",
    description="High-Throughput Local Video Clipping, Story Fusion, and Multi-Channel Shorts Scheduler",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Core Singleton Services
slicer = StreamSlicer(nvenc_enabled=True)
narrator = StoryNarrator(nvenc_enabled=True)
loadout_mgr = LoadoutManager()
story_vault = StoryVault(STORIES_DIR)
batch_assigner = BatchStoryAssigner(narrator=narrator, loadout_mgr=loadout_mgr)
story_gen = StoryGenerator()
account_mgr = AccountManager(
    config_dir=BASE_DIR / "library",
    library_unpublished=LIBRARY_UNPUBLISHED,
    library_published=LIBRARY_PUBLISHED,
)


# ==================== PYDANTIC SCHEMAS ====================

class SliceStreamRequest(BaseModel):
    source_type: str = "url"  # "url" or "file"
    url: Optional[str] = None
    local_path: Optional[str] = None
    segment_duration: float = 120.0
    max_clips: Optional[int] = None


class BatchFuseRequest(BaseModel):
    clip_ids: List[str]
    stream_name: str
    loadout_id: str = "loadout_1"
    story_files: Optional[List[str]] = None  # If omitted, auto-pairs from stories/


class RenderStoryRequest(BaseModel):
    stream_name: str
    clip_id: str
    story_text: str
    headline: str
    loadout_id: Optional[str] = "loadout_1"
    voice_id: Optional[str] = None
    bg_audio_volume: float = 0.12
    voice_volume: float = 1.0


class SingleScheduleRequest(BaseModel):
    stream_name: str
    clip_id: str
    account_id: str
    publish_at: str  # ISO 8601 string
    title: str
    description: Optional[str] = ""
    tags: Optional[List[str]] = None


class StaggerScheduleRequest(BaseModel):
    stream_name: str
    account_ids: List[str]
    start_date: Optional[str] = None
    days: int = 7
    slots_per_day: Optional[List[str]] = None


class RegisterAccountRequest(BaseModel):
    account_id: str
    account_name: str
    client_secret_json: Optional[str] = None


# ==================== ROUTES ====================

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    """Render main Studio dashboard."""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/status")
def get_system_status():
    """Live telemetry HUD for GPU, memory, vault counts, and job queue."""
    try:
        import torch
        cuda_avail = torch.cuda.is_available()
        gpu_name = torch.cuda.get_device_name(0) if cuda_avail else "CPU Mode"
        vram_free = "N/A"
        if cuda_avail:
            try:
                free, total = torch.cuda.mem_get_info()
                vram_free = f"{free / (1024**2):.0f} / {total / (1024**2):.0f} MB"
            except Exception:
                pass
    except (ImportError, Exception):
        cuda_avail = False
        gpu_name = "CPU Mode"
        vram_free = "N/A"

    unpub_count = len(list(LIBRARY_UNPUBLISHED.glob("*/*.mp4")))
    pub_count = len(list(LIBRARY_PUBLISHED.glob("*/*.mp4")))
    unpub_streams = len([d for d in LIBRARY_UNPUBLISHED.iterdir() if d.is_dir()])
    pub_streams = len([d for d in LIBRARY_PUBLISHED.iterdir() if d.is_dir()])
    accounts = account_mgr.list_accounts()
    stories = story_vault.list_stories()

    return {
        "status": "online",
        "cuda_available": cuda_avail,
        "gpu_name": gpu_name,
        "vram_free": vram_free,
        "nvenc_enabled": slicer.nvenc_enabled,
        "unpublished_clips_count": unpub_count,
        "published_clips_count": pub_count,
        "unpublished_streams_count": unpub_streams,
        "published_streams_count": pub_streams,
        "accounts_count": len(accounts),
        "stories_count": len(stories),
    }


# ==================== JOB QUEUE & SSE ====================

@app.get("/api/jobs")
def list_jobs_endpoint():
    return job_queue.list_jobs()


@app.get("/api/jobs/{job_id}")
def get_job_endpoint(job_id: str):
    job = job_queue.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.to_dict()


@app.get("/api/jobs/{job_id}/stream")
async def stream_job_progress(job_id: str):
    """Server-Sent Events (SSE) progress broadcaster for live UI updates."""
    job = job_queue.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_generator():
        q = asyncio.Queue()
        job_queue.register_subscriber(job_id, q)
        try:
            # Yield current state first
            yield f"data: {json.dumps(job.to_dict())}\n\n"
            while True:
                try:
                    data = await asyncio.wait_for(q.get(), timeout=25.0)
                    yield f"data: {json.dumps(data)}\n\n"
                    if data.get("status") in ("completed", "failed"):
                        break
                except asyncio.TimeoutError:
                    # Keep-alive heartbeat
                    yield ": ping\n\n"
        finally:
            job_queue.remove_subscriber(job_id, q)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ==================== STREAMS & SLICING ====================

@app.get("/api/streams")
def list_streams():
    """List all streams and their grouped clips partitioned by Unpublished / Published."""
    def parse_section(root: Path, status_label: str):
        streams = []
        if not root.exists():
            return streams
        for folder in sorted(root.iterdir()):
            if not folder.is_dir():
                continue
            mp4s = sorted(folder.glob("*.mp4"))
            clips = []
            for mp4 in mp4s:
                if mp4.name.endswith("_raw.mp4") or mp4.name.endswith("_story.mp4"):
                    continue
                meta_file = folder / f"{mp4.stem}.json"
                if meta_file.exists():
                    try:
                        with open(meta_file, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            data["status"] = status_label
                            clips.append(data)
                    except Exception:
                        pass
                else:
                    clips.append({
                        "clip_id": mp4.stem,
                        "stream_name": folder.name,
                        "file_path": str(mp4),
                        "status": status_label,
                        "story_attached": False,
                    })

            if clips:
                total_dur = sum(c.get("duration", 120.0) for c in clips)
                streams.append({
                    "stream_name": folder.name,
                    "clip_count": len(clips),
                    "total_duration_sec": total_dur,
                    "clips": clips,
                })
        return streams

    return {
        "unpublished": parse_section(LIBRARY_UNPUBLISHED, "unpublished"),
        "published": parse_section(LIBRARY_PUBLISHED, "published"),
    }


@app.post("/api/streams/slice")
def slice_stream_endpoint(req: SliceStreamRequest):
    """
    Launch asynchronous stream slicing. Returns job_id immediately within 50ms.
    """
    is_url = (req.source_type == "url")
    source = req.url if is_url else req.local_path
    if not source:
        raise HTTPException(status_code=400, detail="Missing URL or local file path")

    job_title = f"Slice Stream: {source[:40]}..."
    job = job_queue.create_job(
        job_type="slice_stream",
        title=job_title,
        details={"source": source, "is_url": is_url, "duration": req.segment_duration},
    )

    def worker():
        try:
            slicer.slice_stream_to_shorts(
                source=source,
                is_url=is_url,
                segment_duration=req.segment_duration,
                library_dir=LIBRARY_UNPUBLISHED,
                max_clips=req.max_clips,
                job_id=job.job_id,
            )
        except Exception as e:
            logger.error(f"Slicing worker failed: {e}")
            job_queue.mark_failed(job.job_id, str(e))

    job_queue.run_in_background(worker)
    return {"status": "accepted", "job_id": job.job_id, "title": job_title}


# ==================== METHOD C: STORIES & LOADOUTS ====================

@app.get("/api/stories")
def list_stories_endpoint():
    """List all text stories stored in stories/."""
    docs = story_vault.list_stories()
    return [d.to_dict() for d in docs]


@app.get("/api/loadouts")
def list_loadouts_endpoint():
    """List all available Editing Loadouts."""
    return loadout_mgr.list_loadouts()


@app.post("/api/stories/batch-fuse")
def batch_fuse_endpoint(req: BatchFuseRequest):
    """
    Method C: Batch fuse stories from stories/ onto selected clips using chosen Loadout.
    Executes asynchronously via JobQueue.
    """
    unpub_dir = LIBRARY_UNPUBLISHED / req.stream_name
    if not unpub_dir.exists():
        raise HTTPException(status_code=404, detail="Stream folder not found")

    clip_paths = [unpub_dir / f"{c_id}.mp4" for c_id in req.clip_ids]
    valid_clips = [p for p in clip_paths if p.exists()]
    if not valid_clips:
        raise HTTPException(status_code=400, detail="No matching clip video files found")

    # Get story documents
    if req.story_files:
        stories = [story_vault.get_story(sf) for sf in req.story_files]
        stories = [s for s in stories if s is not None]
    else:
        stories = story_vault.list_stories()

    if not stories:
        raise HTTPException(status_code=400, detail="No stories found in stories/ folder")

    loadout = loadout_mgr.get_loadout(req.loadout_id)
    job_title = f"Batch Fuse {min(len(valid_clips), len(stories))} Clips via {loadout.name}"
    job = job_queue.create_job(
        job_type="batch_fuse_stories",
        title=job_title,
        details={
            "stream_name": req.stream_name,
            "clips_count": len(valid_clips),
            "stories_count": len(stories),
            "loadout": loadout.name,
        },
    )

    def worker():
        try:
            batch_assigner.batch_fuse(
                clip_paths=valid_clips,
                story_docs=stories,
                loadout=loadout,
                job_id=job.job_id,
            )
        except Exception as e:
            logger.error(f"Batch fusion failed: {e}")
            job_queue.mark_failed(job.job_id, str(e))

    job_queue.run_in_background(worker)
    return {"status": "accepted", "job_id": job.job_id, "title": job_title}


@app.get("/api/story/voices")
def get_story_voices():
    """List available Edge-TTS neural voice presets."""
    return [asdict(v) if hasattr(v, "__dataclass_fields__") else v for v in VOICE_PRESETS.values()]


@app.post("/api/story/generate")
def generate_story_endpoint(req: StoryRequest):
    """Generate a high-retention story using local LLM or curated fallback."""
    try:
        story = story_gen.generate_story(req)
        return story.to_dict()
    except Exception as e:
        logger.error(f"Story generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/story/render")
def render_single_story_endpoint(req: RenderStoryRequest):
    """Single clip story fusion (Method A/B)."""
    unpub_stream = LIBRARY_UNPUBLISHED / req.stream_name
    clip_file = unpub_stream / f"{req.clip_id}.mp4"
    if not clip_file.exists():
        raise HTTPException(status_code=404, detail="Clip file not found")

    loadout = loadout_mgr.get_loadout(req.loadout_id or "loadout_1")
    voice_to_use = req.voice_id or loadout.voice_id
    margin_v, alignment = loadout.get_margin_v_and_alignment()

    output_file = unpub_stream / f"{req.clip_id}_story.mp4"
    backup_file = unpub_stream / f"{req.clip_id}_raw.mp4"
    if not backup_file.exists():
        shutil.copy(str(clip_file), str(backup_file))

    try:
        res = narrator.fuse_story_and_render(
            background_video_path=clip_file,
            story_text=req.story_text,
            headline=req.headline,
            output_video_path=output_file,
            voice_id=voice_to_use,
            bg_audio_volume=req.bg_audio_volume,
            voice_volume=req.voice_volume,
            alignment=alignment,
            margin_v=margin_v,
        )
        shutil.move(str(output_file), str(clip_file))

        # Update metadata JSON
        meta_json = unpub_stream / f"{req.clip_id}.json"
        if meta_json.exists():
            with open(meta_json, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["story_attached"] = True
            data["story_headline"] = req.headline
            data["voice_id"] = voice_to_use
            data["duration"] = res.duration
            with open(meta_json, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

        return {"status": "success", "duration": res.duration, "headline": req.headline}
    except Exception as e:
        logger.error(f"Render failed: {e}")
        raise HTTPException(status_code=500, detail=f"Render failed: {e}")


# ==================== MULTI-CHANNEL SCHEDULER ====================

@app.get("/api/accounts")
def list_accounts_endpoint():
    return [a.to_dict() for a in account_mgr.list_accounts()]


@app.post("/api/accounts/register")
def register_account_endpoint(req: RegisterAccountRequest):
    acc = account_mgr.register_account(
        account_id=req.account_id,
        account_name=req.account_name,
        client_secret_json=req.client_secret_json,
    )
    return acc.to_dict()


@app.delete("/api/accounts/{account_id}")
def delete_account_endpoint(account_id: str):
    removed = account_mgr.delete_account(account_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Account not found")
    return {"status": "success", "account_id": account_id}


@app.post("/api/schedule/single")
def schedule_single_endpoint(req: SingleScheduleRequest):
    """
    Schedule a single short to a specific YouTube account.
    Automatically moves the clip from Unpublished to Published vault!
    """
    clip_file = LIBRARY_UNPUBLISHED / req.stream_name / f"{req.clip_id}.mp4"
    if not clip_file.exists():
        raise HTTPException(status_code=404, detail="Unpublished clip not found")

    receipt = account_mgr.schedule_video_for_account(
        account_id=req.account_id,
        video_path=clip_file,
        title=req.title,
        description=req.description or "",
        publish_at=req.publish_at,
        tags=req.tags,
    )
    return receipt.to_dict()


@app.post("/api/schedule/stagger")
def schedule_stagger_endpoint(req: StaggerScheduleRequest):
    """
    1-Click Smart Stagger: Distributes all clips across connected accounts over 7 days.
    """
    unpub_folder = LIBRARY_UNPUBLISHED / req.stream_name
    if not unpub_folder.exists():
        raise HTTPException(status_code=404, detail="Stream folder not found")

    clips = sorted([p for p in unpub_folder.glob("*.mp4") if not p.name.endswith("_raw.mp4")])
    if not clips:
        raise HTTPException(status_code=400, detail="No unpublished clips in this stream")

    receipts = account_mgr.stagger_schedule_clips(
        stream_name=req.stream_name,
        clips=clips,
        account_ids=req.account_ids,
        days=req.days,
        slots_per_day=req.slots_per_day,
    )
    return {
        "status": "success",
        "total_scheduled": len(receipts),
        "receipts": [r.to_dict() for r in receipts],
    }


# ==================== VIDEO STREAMING (HTTP 206) ====================

@app.get("/api/clips/{section}/{stream_name}/{clip_id}/video")
def stream_clip_video(section: str, stream_name: str, clip_id: str, request: Request):
    """HTTP 206 Partial Content video streaming for smooth scrubbing in HTML5 player."""
    target_dir = LIBRARY_PUBLISHED if section == "published" else LIBRARY_UNPUBLISHED
    clip_path = target_dir / stream_name / f"{clip_id}.mp4"
    if not clip_path.exists():
        raise HTTPException(status_code=404, detail=f"Clip not found: {clip_path}")

    file_size = clip_path.stat().st_size
    range_header = request.headers.get("range")

    if not range_header:
        return StreamingResponse(
            open(clip_path, "rb"),
            media_type="video/mp4",
            headers={"Content-Length": str(file_size)},
        )

    # Parse byte range (e.g. 'bytes=1024-')
    try:
        byte_range = range_header.replace("bytes=", "").split("-")
        start = int(byte_range[0])
        end = int(byte_range[1]) if byte_range[1] else file_size - 1
        length = end - start + 1
    except Exception:
        raise HTTPException(status_code=416, detail="Requested Range Not Satisfiable")

    def file_chunk_generator(file_p, start_b, len_b, chunk_s=1024 * 1024):
        with open(file_p, "rb") as f:
            f.seek(start_b)
            remaining = len_b
            while remaining > 0:
                read_bytes = min(remaining, chunk_s)
                data = f.read(read_bytes)
                if not data:
                    break
                remaining -= len(data)
                yield data

    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
        "Content-Type": "video/mp4",
    }
    return StreamingResponse(
        file_chunk_generator(clip_path, start, length),
        status_code=206,
        headers=headers,
    )


@app.delete("/api/clips/{stream_name}/{clip_id}")
def delete_clip_endpoint(stream_name: str, clip_id: str):
    """Delete a single clip."""
    unpub = LIBRARY_UNPUBLISHED / stream_name
    deleted = False
    for ext in (".mp4", ".json", "_raw.mp4", "_story.mp4"):
        f = unpub / f"{clip_id}{ext}"
        if f.exists():
            f.unlink()
            deleted = True
    if not deleted:
        raise HTTPException(status_code=404, detail="Clip not found")
    return {"status": "success", "clip_id": clip_id}


@app.delete("/api/streams/{stream_name}")
def delete_stream_endpoint(stream_name: str):
    """Delete entire stream folder from unpublished."""
    unpub = LIBRARY_UNPUBLISHED / stream_name
    if unpub.exists():
        shutil.rmtree(unpub)
        return {"status": "success", "stream_name": stream_name}
    raise HTTPException(status_code=404, detail="Stream folder not found")
