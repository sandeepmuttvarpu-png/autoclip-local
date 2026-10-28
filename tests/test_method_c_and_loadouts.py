"""
Automated Integration Tests for Method C (Batch Stories Ingestion),
Editing Loadouts Preset Engine, and Asynchronous JobQueue.
"""

from pathlib import Path
import pytest
from starlette.testclient import TestClient

from autoclip.core.job_queue import JobQueue, job_queue
from autoclip.story.loadouts import LoadoutManager, EditingLoadout, DEFAULT_LOADOUTS
from autoclip.story.batch_assigner import StoryVault, BatchStoryAssigner
from autoclip.web.app import app


def test_job_queue_lifecycle():
    """Verify asynchronous job creation, progress, and completion."""
    jq = JobQueue()
    job = jq.create_job("test_task", "Testing Queue Execution", {"key": "value"})
    assert job.job_id.startswith("job_")
    assert job.status == "pending"

    jq.update_progress(job.job_id, 45.0, "Processing step 2/4")
    updated = jq.get_job(job.job_id)
    assert updated.status == "running"
    assert updated.progress == 45.0
    assert updated.stage == "Processing step 2/4"

    jq.mark_completed(job.job_id, {"result_code": 200})
    completed = jq.get_job(job.job_id)
    assert completed.status == "completed"
    assert completed.progress == 100.0


def test_loadouts_manager():
    """Verify loadouts defaults and subtitle position math."""
    lm = LoadoutManager()
    loadouts = lm.list_loadouts()
    assert len(loadouts) >= 4

    l1 = lm.get_loadout("loadout_1")
    assert l1.name == "Loadout 1: Viral AITA & Drama"
    assert l1.voice_id == "en-US-ChristopherNeural"
    margin_v, alignment = l1.get_margin_v_and_alignment()
    assert alignment == 2  # Lower-third
    assert margin_v == 160

    l2 = lm.get_loadout("loadout_2")
    assert l2.subtitle_position == "center"
    margin_v2, alignment2 = l2.get_margin_v_and_alignment()
    assert alignment2 == 5  # Middle-center


def test_story_vault_parsing(tmp_path: Path):
    """Verify Method C text story parsing from directory."""
    stories_dir = tmp_path / "stories"
    stories_dir.mkdir()

    s1 = stories_dir / "01_test_aita.txt"
    s1.write_text("AITA For Selling My Car?\n\nI sold my car to buy a computer. My family is mad.", encoding="utf-8")

    s2 = stories_dir / "02_boss_revenge.txt"
    s2.write_text("Revenge on Boss\n\nI reported the violation to HR and he was fired.", encoding="utf-8")

    vault = StoryVault(stories_dir)
    docs = vault.list_stories()
    assert len(docs) == 2
    assert docs[0].headline == "AITA For Selling My Car?"
    assert "sold my car" in docs[0].body_text
    assert docs[1].headline == "Revenge on Boss"


def test_api_stories_and_loadouts_endpoints():
    """Test Web Studio endpoints for stories and loadouts."""
    client = TestClient(app)

    res_loadouts = client.get("/api/loadouts")
    assert res_loadouts.status_code == 200
    loadouts_data = res_loadouts.json()
    assert len(loadouts_data) >= 4
    assert any(l["loadout_id"] == "loadout_1" for l in loadouts_data)

    res_stories = client.get("/api/stories")
    assert res_stories.status_code == 200
    stories_data = res_stories.json()
    assert len(stories_data) >= 3  # We populated 3 sample stories
    assert any("Sister's Wedding" in s["headline"] for s in stories_data)


def test_api_async_slice_endpoint():
    """Test that slice endpoint returns 200 with job_id asynchronously."""
    client = TestClient(app)

    payload = {
        "source_type": "url",
        "url": "https://youtu.be/eRXE8Aebp7s",
        "segment_duration": 120.0,
        "max_clips": 1,
    }
    res = client.post("/api/streams/slice", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "accepted"
    assert "job_id" in data
    assert data["job_id"].startswith("job_")

    # Verify job status endpoint
    job_res = client.get(f"/api/jobs/{data['job_id']}")
    assert job_res.status_code == 200
    job_data = job_res.json()
    assert job_data["job_id"] == data["job_id"]
