"""
Comprehensive integration and unit tests for the AutoClip-Local Studio workflow:
1. Stream Slicing to 2-minute 9:16 vertical shorts (autoclip.stream.slicer)
2. Story & Neural Voice Fusion Engine (autoclip.story.generator, narrator)
3. Multi-Account YouTube Scheduler & State Transitions (autoclip.publisher.account_manager)
4. Web Studio Dashboard FastAPI Endpoints (autoclip.web.app)
"""

import json
from pathlib import Path
import shutil
import pytest
from fastapi.testclient import TestClient

from autoclip.stream.slicer import StreamSlicer
from autoclip.story.generator import StoryGenerator, StoryRequest, GeneratedStory
from autoclip.story.narrator import StoryNarrator, VOICE_PRESETS
from autoclip.publisher.account_manager import AccountManager
from autoclip.web.app import app
from tests.test_smoke import generate_synthetic_video, probe_video_dimensions


@pytest.fixture
def test_video(tmp_path: Path) -> Path:
    """Generate a 20-second synthetic video for fast integration testing."""
    vid = tmp_path / "test_stream_source.mp4"
    return generate_synthetic_video(vid, duration_sec=20)


# =========================================================================
# 1. Stream Slicing Tests
# =========================================================================

def test_stream_slicer(test_video: Path, tmp_path: Path):
    """Verify slicing stream into 9:16 vertical shorts with metadata."""
    out_lib = tmp_path / "library" / "unpublished"
    slicer = StreamSlicer(nvenc_enabled=False)  # CPU fallback in tests for portability

    # Slice into 8-second chunks
    clips = slicer.slice_stream_to_shorts(
        video_path=test_video,
        segment_duration=8.0,
        library_dir=out_lib,
    )

    assert len(clips) >= 2
    for c in clips:
        clip_p = Path(c.file_path)
        assert clip_p.exists()
        w, h = probe_video_dimensions(clip_p)
        assert w == 1080
        assert h == 1920
        assert c.status == "unpublished"

        meta_p = clip_p.parent / f"{c.clip_id}.json"
        assert meta_p.exists()
        with open(meta_p, "r", encoding="utf-8") as f:
            data = json.load(f)
            assert data["clip_id"] == c.clip_id
            assert data["duration"] > 0

    # Verify stream summary
    summary_file = out_lib / clips[0].stream_name / "stream_meta.json"
    assert summary_file.exists()


# =========================================================================
# 2. Story Generation & Fusion Tests
# =========================================================================

def test_story_generator_fallback_and_custom():
    """Verify story generation produces high-retention stories across genres."""
    gen = StoryGenerator(api_base_url="http://127.0.0.1:9999/v1")  # unreachable -> fallback

    # AITA story
    story_aita = gen.generate_story(StoryRequest(category="aita"))
    assert story_aita.headline.isupper()
    assert len(story_aita.story_text.split()) >= 40
    assert story_aita.category == "aita"
    assert story_aita.estimated_duration_sec > 0

    # Revenge story
    story_rev = gen.generate_story(StoryRequest(category="revenge"))
    assert len(story_rev.story_text.split()) >= 40

    # Custom prompt text
    custom_script = (
        "MY ROOMMATE ATE MY EXPENSIVE STEAK SO I REPLACED HIS SHAMPOO\n"
        "I had been saving a thirty dollar dry-aged ribeye steak in the fridge for three weeks. "
        "Last night I came home from a ten-hour shift and found the wrapper in the trash and my roommate "
        "sitting on the couch with barbecue sauce on his chin. When I asked him about it he laughed and said "
        "community fridge rules apply. That was the last straw for me so I decided to take action."
    )
    custom_res = gen.generate_story(StoryRequest(category="custom", custom_prompt=custom_script))
    assert "ROOMMATE" in custom_res.headline
    assert "dry-aged ribeye" in custom_res.story_text


def test_story_narrator_subtitles_and_ducking(test_video: Path, tmp_path: Path):
    """Verify ASS subtitles with opening hook card, audio ducking, and render."""
    narrator = StoryNarrator(nvenc_enabled=False)

    out_short = tmp_path / "rendered_story_short.mp4"
    headline = "AITA FOR CONFRONTING MY SISTER AT DINNER?"
    story_text = "I found out my fiancée had been hiding a secret bank account while I worked eighty hours."

    render_res = narrator.fuse_story_and_render(
        background_video_path=test_video,
        story_text=story_text,
        headline=headline,
        output_video_path=out_short,
        voice_id="en-US-ChristopherNeural",
        bg_audio_volume=0.12,
        voice_volume=1.0,
    )

    assert Path(render_res.final_video_path).exists()
    assert Path(render_res.ass_subtitle_path).exists()

    # Verify opening hook card was injected in ASS
    with open(render_res.ass_subtitle_path, "r", encoding="utf-8") as f:
        ass_content = f.read()
        assert "HookCard" in ass_content
        assert headline in ass_content

    # Verify rendered dimensions
    w, h = probe_video_dimensions(Path(render_res.final_video_path))
    assert w == 1080
    assert h == 1920


# =========================================================================
# 3. Multi-Account Manager & State Transitions
# =========================================================================

def test_account_manager_state_transition(test_video: Path, tmp_path: Path):
    """
    Verify state transition:
    Clip is moved from library/unpublished/<stream> to library/published/<stream>
    to prevent duplicate posting.
    """
    unpub = tmp_path / "library" / "unpublished"
    pub = tmp_path / "library" / "published"
    conf = tmp_path / "config"

    stream_name = "apex_legends_tournament"
    stream_dir = unpub / stream_name
    stream_dir.mkdir(parents=True, exist_ok=True)

    clip_id = f"{stream_name}_clip_001"
    clip_file = stream_dir / f"{clip_id}.mp4"
    shutil.copy(str(test_video), str(clip_file))

    meta_file = stream_dir / f"{clip_id}.json"
    with open(meta_file, "w", encoding="utf-8") as f:
        json.dump({"clip_id": clip_id, "stream_name": stream_name, "status": "unpublished"}, f)

    mgr = AccountManager(
        config_dir=conf,
        library_unpublished=unpub,
        library_published=pub,
    )

    accounts = mgr.list_accounts()
    assert len(accounts) >= 3  # channel_1, channel_2, channel_3 seeded

    # Schedule clip
    res = mgr.schedule_and_publish_clip(
        stream_name=stream_name,
        clip_id=clip_id,
        account_id="channel_1",
        publish_at="2026-10-29 18:00:00",
        title="Epic 1v3 Clutch Moment #Shorts",
    )

    assert res["status"] == "success"
    assert res["publish_at"] == "2026-10-29T18:00:00Z"

    # Verify clip moved to Published and removed from Unpublished
    assert not clip_file.exists()
    published_clip = pub / stream_name / f"{clip_id}.mp4"
    assert published_clip.exists()

    # Verify published metadata JSON
    published_meta = pub / stream_name / f"{clip_id}.json"
    assert published_meta.exists()
    with open(published_meta, "r", encoding="utf-8") as f:
        meta_data = json.load(f)
        assert meta_data["status"] == "published"
        assert meta_data["published_account_id"] == "channel_1"
        assert meta_data["scheduled_publish_at"] == "2026-10-29T18:00:00Z"
        assert "youtube_url" in meta_data

    # Attempting to re-publish should fail to prevent duplicate posting
    with pytest.raises(Exception):
        mgr.schedule_and_publish_clip(
            stream_name=stream_name,
            clip_id=clip_id,
            account_id="channel_1",
            publish_at="2026-10-30 18:00:00",
        )


def test_account_manager_smart_stagger(test_video: Path, tmp_path: Path):
    """Verify 7-day Smart Stagger plan and execution across 3 channels."""
    unpub = tmp_path / "library" / "unpublished"
    pub = tmp_path / "library" / "published"
    conf = tmp_path / "config"

    stream_name = "minecraft_stream_vod"
    stream_dir = unpub / stream_name
    stream_dir.mkdir(parents=True, exist_ok=True)

    # Create 6 dummy clips
    for i in range(1, 7):
        cid = f"{stream_name}_clip_{i:03d}"
        shutil.copy(str(test_video), str(stream_dir / f"{cid}.mp4"))
        with open(stream_dir / f"{cid}.json", "w", encoding="utf-8") as f:
            json.dump({"clip_id": cid, "status": "unpublished"}, f)

    mgr = AccountManager(
        config_dir=conf,
        library_unpublished=unpub,
        library_published=pub,
    )

    plan = mgr.generate_smart_stagger_plan(
        stream_name=stream_name,
        account_ids=["channel_1", "channel_2", "channel_3"],
        days=7,
    )

    assert len(plan) == 6
    # Verify round-robin channel distribution
    assert plan[0]["account_id"] == "channel_1"
    assert plan[1]["account_id"] == "channel_2"
    assert plan[2]["account_id"] == "channel_3"

    # Execute stagger
    results = mgr.execute_smart_stagger(
        stream_name=stream_name,
        account_ids=["channel_1", "channel_2", "channel_3"],
        days=7,
    )

    assert len(results) == 6
    # All 6 clips must be in published directory now
    published_clips = list((pub / stream_name).glob("*_clip_*.mp4"))
    assert len(published_clips) == 6


# =========================================================================
# 4. Web Studio Dashboard FastAPI Endpoints Tests
# =========================================================================

def test_web_api_status_and_voices():
    """Verify system status and voices endpoints."""
    client = TestClient(app)

    res = client.get("/api/status")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "online"
    assert "accounts_count" in data
    assert "unpublished_clips_count" in data

    voices_res = client.get("/api/story/voices")
    assert voices_res.status_code == 200
    voices = voices_res.json()
    assert len(voices) >= 5
    assert any(v["id"] == "en-US-ChristopherNeural" for v in voices)


def test_web_api_story_generation():
    """Verify API story generation endpoint."""
    client = TestClient(app)
    res = client.post(
        "/api/story/generate",
        json={"category": "revenge", "target_duration_sec": 120.0},
    )
    assert res.status_code == 200
    data = res.json()
    assert "headline" in data
    assert "story_text" in data
    assert data["category"] == "revenge"


def test_web_api_accounts_list():
    """Verify accounts list endpoint."""
    client = TestClient(app)
    res = client.get("/api/accounts")
    assert res.status_code == 200
    accounts = res.json()
    assert len(accounts) >= 3
    acc_ids = [a["account_id"] for a in accounts]
    assert "channel_1" in acc_ids
    assert "channel_2" in acc_ids
    assert "channel_3" in acc_ids


def test_web_api_video_streaming(test_video: Path):
    """Verify video streaming with HTTP 206 Partial Content Range support."""
    client = TestClient(app)

    # Place a dummy clip in unpublished library
    stream_folder = Path("library/unpublished/test_stream_api")
    stream_folder.mkdir(parents=True, exist_ok=True)
    clip_path = stream_folder / "test_stream_api_clip_001.mp4"
    shutil.copy(str(test_video), str(clip_path))

    try:
        # Full file request
        res = client.get("/api/clips/unpublished/test_stream_api/test_stream_api_clip_001/video")
        assert res.status_code == 200
        assert res.headers["content-type"] == "video/mp4"

        # Byte-range request (Range: bytes=0-1023)
        range_res = client.get(
            "/api/clips/unpublished/test_stream_api/test_stream_api_clip_001/video",
            headers={"Range": "bytes=0-1023"},
        )
        assert range_res.status_code == 206
        assert range_res.headers["content-length"] == "1024"
        assert "bytes 0-1023/" in range_res.headers["content-range"]
    finally:
        if stream_folder.exists():
            shutil.rmtree(stream_folder)

