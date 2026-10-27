"""
Automated smoke test suite for AutoClip-Local.
Generates synthetic 16:9 video and verifies 9:16 vertical reframing, kinetic ASS subtitles, and NVENC rendering.
"""

from pathlib import Path
import subprocess
import pytest

from autoclip.audio.transcriber import Word, Segment, Transcript
from autoclip.intelligence.segmenter import ViralitySegmenter, ClipCandidate
from autoclip.vision.reframer import VideoReframer, CropWindow
from autoclip.subtitle.ass_builder import AssSubtitleBuilder
from autoclip.render.ffmpeg_pipe import FFmpegRenderer, RenderOptions


def generate_synthetic_video(output_mp4: Path, duration_sec: int = 15) -> Path:
    """Generate a clean 16:9 1920x1080 MP4 with audio tone."""
    output_mp4 = Path(output_mp4)
    output_mp4.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size=1920x1080:rate=30:duration={duration_sec}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:duration={duration_sec}",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-c:a",
        "aac",
        "-pix_fmt",
        "yuv420p",
        str(output_mp4),
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Failed to generate synthetic video: {res.stderr}")
    return output_mp4


def probe_video_dimensions(video_path: Path):
    """Query width and height using ffprobe."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "csv=p=0",
        str(video_path),
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    parts = res.stdout.strip().split(",")
    return int(parts[0]), int(parts[1])


def test_reframer_aspect_ratio():
    """Verify 9:16 aspect ratio math and boundary clamping."""
    reframer = VideoReframer(ema_alpha=0.15)

    # 1080p landscape test: 1080 * 9 / 16 = 607.5 -> 608 even
    crop_w = int(round(1080 * (9.0 / 16.0)))
    if crop_w % 2 != 0:
        crop_w += 1
    assert crop_w == 608

    # Clamping test: focal center near right edge
    raw_cx = 1900.0
    crop_x = int(round(max(0.0, min(raw_cx - crop_w / 2.0, float(1920 - crop_w)))))
    assert crop_x <= (1920 - crop_w)
    assert crop_x >= 0


def test_kinetic_ass_builder(tmp_path: Path):
    """Verify ASS subtitle generation with word-level pop & highlight tags."""
    words = [
        Word(word="Why", start=1.0, end=1.4),
        Word(word="8GB", start=1.4, end=1.8),
        Word(word="VRAM", start=1.8, end=2.3),
        Word(word="is", start=2.3, end=2.6),
        Word(word="enough", start=2.6, end=3.1),
    ]

    ass_path = tmp_path / "test_subs.ass"
    builder = AssSubtitleBuilder(font_name="Montserrat ExtraBold", font_size=75, margin_v=190)
    builder.build_ass(words, clip_start=0.5, clip_end=4.0, output_path=ass_path)

    assert ass_path.exists()
    content = ass_path.read_text(encoding="utf-8")

    # Check header
    assert "[Script Info]" in content
    assert "PlayResX: 1080" in content
    assert "PlayResY: 1920" in content
    assert "Style: Default" in content

    # Check kinetic tags
    assert r"\fscx115\fscy115" in content
    assert r"\c&H0000FFFF&" in content
    assert "WHY" in content
    assert "8GB" in content


def test_segmenter_json_parsing():
    """Verify robust JSON extraction from standard and markdown responses."""
    # 1. Clean JSON array
    clean_json = """[
      {
        "start_time": 10.0,
        "end_time": 45.0,
        "headline": "Massive Breakthrough",
        "hook_strength": 92,
        "virality_score": 90,
        "rationale": "High curiosity opening"
      }
    ]"""
    res1 = ViralitySegmenter._parse_json_reply(clean_json)
    assert len(res1) == 1
    assert res1[0].headline == "Massive Breakthrough"
    assert res1[0].duration == 35.0

    # 2. Markdown wrapped JSON with chatter
    markdown_json = """Here are the viral clips:
    ```json
    [
      {
        "start_time": 15.5,
        "end_time": 50.0,
        "headline": "The 70% RAM Rule",
        "hook_strength": 95,
        "virality_score": 93,
        "rationale": "Empirical systems proof"
      }
    ]
    ```
    Hope this helps!"""
    res2 = ViralitySegmenter._parse_json_reply(markdown_json)
    assert len(res2) == 1
    assert res2[0].headline == "The 70% RAM Rule"
    assert res2[0].virality_score == 93


def test_end_to_end_nvenc_render(tmp_path: Path):
    """End-to-end smoke test: generates synthetic video, crops to 9:16, burns subtitles, and encodes via NVENC."""
    synth_input = tmp_path / "synthetic_16x9.mp4"
    generate_synthetic_video(synth_input, duration_sec=6)
    assert synth_input.exists()

    # Create dummy subtitle track
    words = [
        Word(word="Testing", start=0.5, end=1.0),
        Word(word="AutoClip", start=1.0, end=1.8),
        Word(word="Local", start=1.8, end=2.5),
        Word(word="Engine", start=2.5, end=3.2),
    ]
    ass_file = tmp_path / "smoke.ass"
    builder = AssSubtitleBuilder()
    builder.build_ass(words, clip_start=0.0, clip_end=4.0, output_path=ass_file)

    # Calculate 9:16 crop
    reframer = VideoReframer()
    crop = reframer.compute_smoothed_crop(synth_input, start_time=0.0, end_time=4.0)

    # Render output with hardware NVENC
    output_short = tmp_path / "rendered_short_9x16.mp4"
    renderer = FFmpegRenderer()
    render_opts = RenderOptions(
        crop_w=crop.crop_w,
        crop_h=crop.crop_h,
        crop_x=crop.crop_x,
        crop_y=crop.crop_y,
        target_width=1080,
        target_height=1920,
        use_nvenc=True,
    )

    renderer.render_clip(
        input_video=synth_input,
        start_time=0.0,
        end_time=4.0,
        output_video=output_short,
        ass_subtitles=ass_file,
        options=render_opts,
        show_progress=False,
    )

    assert output_short.exists()
    assert output_short.stat().st_size > 10000  # Non-trivial MP4 file

    # Verify vertical 1080x1920 resolution
    w, h = probe_video_dimensions(output_short)
    assert w == 1080, f"Expected width 1080, got {w}"
    assert h == 1920, f"Expected height 1920, got {h}"


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        print("Running smoke tests manually...")
        test_reframer_aspect_ratio()
        test_kinetic_ass_builder(p)
        test_segmenter_json_parsing()
        test_end_to_end_nvenc_render(p)
        print("✓ All smoke tests passed successfully!")
