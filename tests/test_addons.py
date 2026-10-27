"""
Automated unit and integration tests for the three AutoClip-Local add-on modules:
1. YouTube Scheduled Publisher (autoclip.publisher.youtube_scheduler)
2. Gaming Stacked 9:16 Cropper (autoclip.vision.gaming_stacked_crop)
3. Audio Spike & Hype Detector (autoclip.intelligence.audio_spike_detector)
"""

from pathlib import Path
from unittest.mock import MagicMock, patch
import numpy as np
import pytest

from autoclip.intelligence.audio_spike_detector import AudioSpikeDetector, HypeMoment
from autoclip.publisher.youtube_scheduler import YouTubePublisher, ScheduledUploadResult
from autoclip.vision.gaming_stacked_crop import StackedGamingCropper, StackedCropGeometry
from tests.test_smoke import generate_synthetic_video, probe_video_dimensions


# =========================================================================
# 1. YouTube Scheduled Publisher Tests
# =========================================================================

def test_youtube_publish_at_formatter():
    """Verify ISO 8601 UTC timestamp formatting and validation."""
    # Standard date-time string
    res1 = YouTubePublisher.format_publish_at("2026-10-28 18:30:00")
    assert res1 == "2026-10-28T18:30:00Z"

    # Already formatted ISO
    res2 = YouTubePublisher.format_publish_at("2026-12-31T23:59:59Z")
    assert res2 == "2026-12-31T23:59:59Z"

    # Malformed timestamp
    with pytest.raises(ValueError):
        YouTubePublisher.format_publish_at("invalid-date-string")


def test_youtube_missing_credentials_error(tmp_path: Path):
    """Verify informative FileNotFoundError when client secrets JSON is missing."""
    dummy_secrets = tmp_path / "non_existent_secret.json"
    dummy_token = tmp_path / "non_existent_token.json"

    pub = YouTubePublisher(client_secrets_file=dummy_secrets, token_file=dummy_token)
    with pytest.raises(FileNotFoundError) as exc_info:
        pub.get_service()
    assert "YouTube OAuth client secrets file not found" in str(exc_info.value)


def test_youtube_scheduled_upload_mock(tmp_path: Path):
    """Test resumable upload pipeline logic and YouTube scheduling payload."""
    test_video = tmp_path / "test_short.mp4"
    test_video.write_bytes(b"\x00" * 1024)  # 1KB dummy file

    mock_service = MagicMock()
    mock_request = MagicMock()
    mock_request.next_chunk.return_value = (None, {"id": "mock_yt_12345"})
    mock_service.videos().insert.return_value = mock_request

    pub = YouTubePublisher(
        client_secrets_file=tmp_path / "secrets.json",
        token_file=tmp_path / "token.json",
    )
    pub._service = mock_service

    result = pub.upload_scheduled_short(
        video_path=test_video,
        title="Epic 120B Inference Short",
        description="Local inference running on RTX 5050",
        publish_at="2026-10-28T15:00:00Z",
        tags=["ai", "hardware"],
    )

    assert result.video_id == "mock_yt_12345"
    assert result.url == "https://youtu.be/mock_yt_12345"
    assert result.publish_at == "2026-10-28T15:00:00Z"
    assert result.privacy_status == "private"

    # Verify API insert arguments
    insert_call = mock_service.videos().insert.call_args[1]
    assert insert_call["part"] == "snippet,status"
    assert insert_call["body"]["status"]["privacyStatus"] == "private"
    assert insert_call["body"]["status"]["publishAt"] == "2026-10-28T15:00:00Z"
    assert "shorts" in [t.lower() for t in insert_call["body"]["snippet"]["tags"]]


# =========================================================================
# 2. Gaming Stacked 9:16 Cropper Tests
# =========================================================================

def test_gaming_stacked_geometry():
    """Verify geometry splits canvas into top 35% (672px) and bottom 65% (1248px)."""
    geom = StackedCropGeometry(
        face_crop_w=640,
        face_crop_h=400,
        face_crop_x=50,
        face_crop_y=50,
        game_crop_w=934,
        game_crop_h=1080,
        game_crop_x=494,
        game_crop_y=0,
    )

    assert geom.top_scale_w == 1080
    assert geom.top_scale_h == 672
    assert geom.bottom_scale_w == 1080
    assert geom.bottom_scale_h == 1248
    assert geom.top_scale_h + geom.bottom_scale_h == 1920

    filtergraph = geom.to_filtergraph()
    assert "scale=1080:672[top]" in filtergraph
    assert "scale=1080:1248[bottom]" in filtergraph
    assert "[top][bottom]vstack[outv]" in filtergraph


def test_gaming_stacked_render_end_to_end(tmp_path: Path):
    """End-to-end render test: crops synthetic video into stacked 1080x1920 layout using NVENC."""
    synth_input = tmp_path / "game_source.mp4"
    generate_synthetic_video(synth_input, duration_sec=4)
    assert synth_input.exists()

    cropper = StackedGamingCropper()
    out_stacked = tmp_path / "rendered_gaming_short.mp4"

    cropper.render_stacked_clip(
        input_video=synth_input,
        start_time=0.0,
        end_time=3.0,
        output_video=out_stacked,
        use_nvenc=True,
    )

    assert out_stacked.exists()
    assert out_stacked.stat().st_size > 10000

    w, h = probe_video_dimensions(out_stacked)
    assert w == 1080
    assert h == 1920


# =========================================================================
# 3. Audio Spike & Hype Detector Tests
# =========================================================================

def test_audio_spike_detection_synthetic():
    """
    Generate synthetic audio with a quiet baseline and a 4-second loud burst (+3.5 sigma)
    and verify AudioSpikeDetector flags the exact hype window.
    """
    sr = 16000
    total_sec = 20.0
    total_samples = int(sr * total_sec)

    # 1. Quiet ambient noise (amplitude 0.02)
    np.random.seed(42)
    audio = np.random.normal(0, 0.02, total_samples).astype(np.float32)

    # 2. Inject loud hype burst from t=8.0s to t=12.5s (4.5s duration, amplitude 0.8)
    burst_start = int(8.0 * sr)
    burst_end = int(12.5 * sr)
    audio[burst_start:burst_end] += np.random.normal(0, 0.8, burst_end - burst_start).astype(np.float32)

    detector = AudioSpikeDetector(
        window_sec=1.0,
        hop_sec=0.25,
        threshold_std=2.5,
        min_consecutive_sec=3.0,
        rolling_baseline_window_sec=20.0,
    )

    moments = detector.detect_spikes_from_array(audio, sample_rate=sr)
    assert len(moments) >= 1, "Failed to detect synthetic hype moment"

    top_moment = moments[0]
    # Check that detected peak is inside the burst window [8.0, 12.5]
    assert 7.0 <= top_moment.peak_time <= 13.5
    assert top_moment.peak_intensity >= 2.5
    assert top_moment.duration >= 3.0
