"""
Method C: Batch Story Assigner and Multi-Clip Fusion Pipeline.
Scans stories/ for text files, extracts hooks, and fuses them onto selected video clips using Editing Loadouts.
"""

from dataclasses import dataclass
import json
import logging
from pathlib import Path
import re
import time
from typing import List, Optional

from autoclip.story.loadouts import EditingLoadout, LoadoutManager, DEFAULT_LOADOUTS
from autoclip.story.narrator import StoryNarrator
from autoclip.core.job_queue import job_queue

logger = logging.getLogger(__name__)


@dataclass
class StoryDocument:
    file_name: str
    file_path: str
    headline: str
    body_text: str
    full_text: str
    word_count: int
    estimated_duration_sec: float

    def to_dict(self) -> dict:
        return {
            "file_name": self.file_name,
            "file_path": self.file_path,
            "headline": self.headline,
            "body_text": self.body_text[:120] + "..." if len(self.body_text) > 120 else self.body_text,
            "full_text": self.full_text,
            "word_count": self.word_count,
            "estimated_duration_sec": round(self.estimated_duration_sec, 1),
        }


class StoryVault:
    """Manages the stories/ directory containing text stories."""

    def __init__(self, stories_dir: Path):
        self.stories_dir = Path(stories_dir)
        self.stories_dir.mkdir(parents=True, exist_ok=True)

    def list_stories(self) -> List[StoryDocument]:
        """Scan stories/ and parse all text files."""
        files = sorted(
            list(self.stories_dir.glob("*.txt")),
            key=lambda p: p.name.lower(),
        )
        docs: List[StoryDocument] = []
        for f in files:
            try:
                content = f.read_text(encoding="utf-8").strip()
                if not content:
                    continue
                lines = [line.strip() for line in content.splitlines() if line.strip()]
                if not lines:
                    continue
                headline = lines[0]
                body = " ".join(lines[1:]) if len(lines) > 1 else lines[0]
                words = len(content.split())
                # Approx 150 words per minute speaking rate = ~2.5 words/sec
                est_sec = words / 2.5

                docs.append(
                    StoryDocument(
                        file_name=f.name,
                        file_path=str(f),
                        headline=headline,
                        body_text=body,
                        full_text=content,
                        word_count=words,
                        estimated_duration_sec=est_sec,
                    )
                )
            except Exception as e:
                logger.warning(f"Error parsing story {f}: {e}")
        return docs

    def get_story(self, file_name: str) -> Optional[StoryDocument]:
        p = self.stories_dir / file_name
        if not p.exists():
            return None
        content = p.read_text(encoding="utf-8").strip()
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        headline = lines[0] if lines else p.stem
        body = " ".join(lines[1:]) if len(lines) > 1 else headline
        return StoryDocument(
            file_name=p.name,
            file_path=str(p),
            headline=headline,
            body_text=body,
            full_text=content,
            word_count=len(content.split()),
            estimated_duration_sec=len(content.split()) / 2.5,
        )


class BatchStoryAssigner:
    """Automates Method C: Pairing story text files with video clips and rendering via Loadouts."""

    def __init__(
        self,
        narrator: StoryNarrator,
        loadout_mgr: Optional[LoadoutManager] = None,
    ):
        self.narrator = narrator
        self.loadout_mgr = loadout_mgr or LoadoutManager()

    def batch_fuse(
        self,
        clip_paths: List[Path],
        story_docs: List[StoryDocument],
        loadout: EditingLoadout,
        job_id: Optional[str] = None,
    ) -> List[dict]:
        """
        Fuses a list of stories onto a list of clips sequentially.
        If more clips than stories exist, stories cycle or stop based on count.
        """
        total = min(len(clip_paths), len(story_docs))
        if total == 0:
            raise ValueError("No matching clips or stories provided for batch fusion.")

        results = []
        margin_v, alignment = loadout.get_margin_v_and_alignment()

        for idx in range(total):
            clip_path = Path(clip_paths[idx])
            story = story_docs[idx]

            stage_name = f"Fusing Clip {idx+1}/{total}: '{story.headline[:30]}...'"
            percent = (idx / total) * 100.0
            if job_id:
                job_queue.update_progress(
                    job_id=job_id,
                    progress=percent,
                    stage=stage_name,
                    details={"current_clip": idx + 1, "total_clips": total},
                )

            logger.info(f"[{idx+1}/{total}] Rendering story onto {clip_path.name} with {loadout.name}...")

            # Output to a temporary render file
            out_file = clip_path.parent / f"{clip_path.stem}_story.mp4"
            backup_file = clip_path.parent / f"{clip_path.stem}_raw.mp4"

            try:
                # Save raw cut backup if not exists
                if not backup_file.exists():
                    import shutil
                    shutil.copy(str(clip_path), str(backup_file))

                # Render with chosen loadout
                res = self.narrator.fuse_story_and_render(
                    background_video_path=clip_path,
                    story_text=story.full_text,
                    headline=story.headline,
                    output_video_path=out_file,
                    voice_id=loadout.voice_id,
                    bg_audio_volume=loadout.bg_audio_volume,
                    voice_volume=loadout.voice_volume,
                    alignment=alignment,
                    margin_v=margin_v,
                )

                # Overwrite original clip with rendered story so studio displays it directly
                import shutil
                shutil.move(str(out_file), str(clip_path))

                # Update metadata JSON
                meta_json = clip_path.parent / f"{clip_path.stem}.json"
                if meta_json.exists():
                    with open(meta_json, "r", encoding="utf-8") as f:
                        meta_data = json.load(f)
                    meta_data["story_attached"] = True
                    meta_data["story_headline"] = story.headline
                    meta_data["voice_id"] = loadout.voice_id
                    meta_data["loadout_id"] = loadout.loadout_id
                    meta_data["duration"] = res.duration
                    with open(meta_json, "w", encoding="utf-8") as f:
                        json.dump(meta_data, f, indent=2)

                results.append({
                    "clip_name": clip_path.name,
                    "headline": story.headline,
                    "duration": res.duration,
                    "voice_id": loadout.voice_id,
                    "status": "success",
                })
            except Exception as e:
                logger.error(f"Failed to render clip {clip_path.name}: {e}")
                results.append({
                    "clip_name": clip_path.name,
                    "headline": story.headline,
                    "error": str(e),
                    "status": "failed",
                })

        if job_id:
            job_queue.update_progress(
                job_id=job_id,
                progress=100.0,
                stage="Batch Story Fusion Complete",
                details={"completed_count": len(results)},
            )
            job_queue.mark_completed(job_id=job_id, result={"clips_rendered": results})

        return results
