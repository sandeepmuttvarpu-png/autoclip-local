"""
CLI entrypoint for AutoClip-Local.
Provides 'run' (full pipeline) and 'scan' (ASR + virality scoring dry-run) commands.
"""

import json
import logging
import os
from pathlib import Path
import sys
import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from autoclip import __version__
from autoclip.audio.transcriber import AudioTranscriber, Transcript
from autoclip.config import AutoClipConfig, default_config
from autoclip.intelligence.segmenter import ViralitySegmenter, ClipCandidate
from autoclip.render.ffmpeg_pipe import FFmpegRenderer, RenderOptions
from autoclip.subtitle.ass_builder import AssSubtitleBuilder
from autoclip.vision.reframer import VideoReframer

console = Console()
logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")


def print_banner(cfg: AutoClipConfig):
    gpu_badge = "[bold green]NVIDIA CUDA (Active)[/bold green]" if cfg.hardware.device == "cuda" else "[yellow]CPU Only[/yellow]"
    nvenc_badge = "[bold green]NVENC Hardware Acceleration (h264_nvenc)[/bold green]" if cfg.hardware.has_nvenc else "[yellow]CPU libx264[/yellow]"

    banner_text = f"""[bold magenta]AutoClip-Local v{__version__}[/bold magenta] — 100% Local AI Video Clipping Engine
• Vision & Reframing : [cyan]9:16 Vertical EMA Tracking[/cyan]
• Subtitle Engine    : [cyan]Kinetic Word-Level ASS Pop Karaoke[/cyan]
• Compute Silicon    : {gpu_badge}
• Encoder Pipeline   : {nvenc_badge}
• Local LLM Endpoint : [bold blue]{cfg.llm_url}[/bold blue]"""
    console.print(Panel(banner_text, title="🥪 AutoClip-Local", border_style="bright_blue"))


@click.group()
@click.version_option(__version__, prog_name="autoclip")
def main():
    """AutoClip-Local: High-throughput local video clipping and vertical shorts repurposing."""
    pass


@main.command("scan")
@click.argument("input_video", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--llm-url", default=None, help="Local LLM endpoint (default: http://127.0.0.1:8080/v1)")
@click.option("--whisper-model", default=None, help="Whisper model size (default: large-v3 with fallback)")
@click.option("--device", default=None, type=click.Choice(["cuda", "cpu"]), help="ASR compute device")
@click.option("--top-k", default=3, type=int, help="Number of viral hook candidates to extract")
@click.option("--min-score", default=70, type=int, help="Minimum virality score threshold (0-100)")
@click.option("--json", "output_json", is_flag=True, help="Output pure JSON results to stdout")
def scan_cmd(input_video, llm_url, whisper_model, device, top_k, min_score, output_json):
    """Transcribe and evaluate viral hook candidates without rendering video."""
    cfg = AutoClipConfig()
    cfg.detect_environment()

    if llm_url:
        cfg.llm_url = llm_url
    if whisper_model:
        cfg.whisper_model = whisper_model
    if device:
        cfg.whisper_device = device
        if device == "cpu":
            cfg.whisper_compute_type = "int8"

    if not output_json:
        print_banner(cfg)
        console.print(f"[bold cyan]🔍 Scanning:[/bold cyan] {input_video.name}")

    # 1. Transcribe
    with console.status("[bold green]Transcribing audio with word-level timestamps...", spinner="dots"):
        transcriber = AudioTranscriber(
            model_size=cfg.whisper_model,
            fallback_model=cfg.whisper_fallback_model,
            device=cfg.whisper_device,
            compute_type=cfg.whisper_compute_type,
        )
        transcript = transcriber.transcribe(input_video)

    # 2. Virality Scoring
    with console.status("[bold green]Analyzing virality and hooks via local LLM...", spinner="dots"):
        segmenter = ViralitySegmenter(
            api_base_url=cfg.llm_url,
            api_key=cfg.llm_api_key,
            model=cfg.llm_model,
        )
        candidates = segmenter.find_viral_clips(
            transcript,
            min_duration=cfg.min_clip_duration,
            max_duration=cfg.max_clip_duration,
            min_virality_score=min_score,
            top_k=top_k,
        )

    if output_json:
        payload = {
            "source_video": str(input_video.resolve()),
            "duration": transcript.duration,
            "language": transcript.language,
            "clips": [c.to_dict() for c in candidates],
        }
        click.echo(json.dumps(payload, indent=2))
        return

    # Display results table
    table = Table(title=f"Viral Hook Candidates for {input_video.name}", border_style="bright_magenta")
    table.add_column("Rank", justify="center", style="bold yellow")
    table.add_column("Window", justify="center", style="cyan")
    table.add_column("Duration", justify="center")
    table.add_column("Virality", justify="center", style="bold green")
    table.add_column("Hook", justify="center", style="bold blue")
    table.add_column("Headline & Rationale", style="white")

    for i, c in enumerate(candidates, start=1):
        m_s, s_s = divmod(c.start_time, 60)
        m_e, s_e = divmod(c.end_time, 60)
        window_str = f"{int(m_s):02d}:{s_s:04.1f} - {int(m_e):02d}:{s_e:04.1f}"
        summary = f"[bold]{c.headline}[/bold]\n[dim]{c.rationale}[/dim]"
        table.add_row(
            f"#{i}",
            window_str,
            f"{c.duration:.1f}s",
            f"{c.virality_score}/100",
            f"{c.hook_strength}/100",
            summary,
        )

    console.print(table)


@main.command("run")
@click.argument("input_video", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--out-dir", default="./output_shorts", type=click.Path(file_okay=False, path_type=Path), help="Output directory for rendered clips")
@click.option("--top-k", default=3, type=int, help="Number of viral clips to extract (default: 3)")
@click.option("--min-score", default=70, type=int, help="Minimum virality threshold (default: 70)")
@click.option("--min-duration", default=30.0, type=float, help="Minimum clip duration in seconds")
@click.option("--max-duration", default=65.0, type=float, help="Maximum clip duration in seconds")
@click.option("--llm-url", default=None, help="Local LLM endpoint (default: http://127.0.0.1:8080/v1)")
@click.option("--whisper-model", default=None, help="Whisper model size (default: large-v3 with fallback)")
@click.option("--device", default=None, type=click.Choice(["cuda", "cpu"]), help="ASR compute device")
@click.option("--no-nvenc", is_flag=True, help="Disable NVENC hardware encoding (force CPU libx264)")
@click.option("--no-subtitles", is_flag=True, help="Disable burning kinetic subtitles")
@click.option("--font", default="Montserrat ExtraBold", help="Font name for kinetic subtitles")
@click.option("--json", "output_json", is_flag=True, help="Output generated clip metadata to JSON")
def run_cmd(
    input_video,
    out_dir,
    top_k,
    min_score,
    min_duration,
    max_duration,
    llm_url,
    whisper_model,
    device,
    no_nvenc,
    no_subtitles,
    font,
    output_json,
):
    """Run end-to-end: Transcribe -> Virality Scoring -> 9:16 Vision Reframing -> Kinetic Subtitles -> NVENC Render."""
    cfg = AutoClipConfig()
    cfg.detect_environment()

    if llm_url:
        cfg.llm_url = llm_url
    if whisper_model:
        cfg.whisper_model = whisper_model
    if device:
        cfg.whisper_device = device
        if device == "cpu":
            cfg.whisper_compute_type = "int8"
    if font:
        cfg.font_name = font
    if no_nvenc:
        cfg.hardware.has_nvenc = False

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not output_json:
        print_banner(cfg)
        console.print(f"[bold cyan]🎬 Ingesting Video:[/bold cyan] {input_video.name}")
        console.print(f"[dim]Output destination: {out_dir.resolve()}[/dim]\n")

    # 1. Transcribe
    with console.status("[bold green]Transcribing speech with word-level timestamps (faster-whisper)...", spinner="dots"):
        transcriber = AudioTranscriber(
            model_size=cfg.whisper_model,
            fallback_model=cfg.whisper_fallback_model,
            device=cfg.whisper_device,
            compute_type=cfg.whisper_compute_type,
        )
        transcript = transcriber.transcribe(input_video)

    # Save transcript to output dir
    transcript_file = out_dir / f"{input_video.stem}_transcript.json"
    transcript.save_json(transcript_file)
    if not output_json:
        console.print(f"✓ Transcription complete ({transcript.duration:.1f}s, {len(transcript.segments)} segments)")

    # 2. Extract all words for subtitle alignment
    all_words = []
    for s in transcript.segments:
        all_words.extend(s.words)

    # 3. Identify viral clips
    with console.status(f"[bold green]Querying local LLM ({cfg.llm_url}) for viral hooks...", spinner="dots"):
        segmenter = ViralitySegmenter(
            api_base_url=cfg.llm_url,
            api_key=cfg.llm_api_key,
            model=cfg.llm_model,
        )
        candidates = segmenter.find_viral_clips(
            transcript,
            min_duration=min_duration,
            max_duration=max_duration,
            min_virality_score=min_score,
            top_k=top_k,
        )

    if not candidates:
        console.print("[bold red]No segments met the virality criteria. Try lowering --min-score.[/bold red]")
        sys.exit(1)

    if not output_json:
        console.print(f"✓ Identified [bold green]{len(candidates)}[/bold green] viral hook segments:\n")
        for i, c in enumerate(candidates, 1):
            console.print(f"   [yellow]#{i}[/yellow] [bold]{c.headline}[/bold] ({c.duration:.1f}s) — Virality: [green]{c.virality_score}[/green], Hook: [blue]{c.hook_strength}[/blue]")
        console.print("")

    reframer = VideoReframer(ema_alpha=cfg.ema_alpha, face_detector_type=cfg.face_detector_type)
    ass_builder = AssSubtitleBuilder(
        font_name=cfg.font_name,
        font_size=cfg.font_size,
        margin_v=cfg.subtitle_margin_v,
        highlight_color=cfg.highlight_color_ass,
        primary_color=cfg.primary_color_ass,
        outline_color=cfg.outline_color_ass,
        max_words_per_line=cfg.max_words_per_line,
    )
    renderer = FFmpegRenderer()

    rendered_clips = []

    # 4. Process each candidate
    for idx, cand in enumerate(candidates, start=1):
        clip_name = f"clip_{idx:02d}_{cand.start_time:.0f}s_{cand.end_time:.0f}s"
        out_clip_path = out_dir / f"{clip_name}.mp4"
        ass_path = out_dir / f"{clip_name}.ass"

        if not output_json:
            console.rule(f"[bold magenta]Rendering Clip #{idx}: {cand.headline}[/bold magenta]")

        # A. Vision Reframing: EMA 9:16 crop window
        crop_window = reframer.compute_smoothed_crop(
            input_video,
            start_time=cand.start_time,
            end_time=cand.end_time,
            sample_fps=cfg.sample_fps,
        )

        # B. Kinetic Subtitles (.ass)
        ass_file = None
        if not no_subtitles and all_words:
            ass_file = ass_builder.build_ass(
                words=all_words,
                clip_start=cand.start_time,
                clip_end=cand.end_time,
                output_path=ass_path,
            )

        # C. Hardware NVENC Render
        render_opts = RenderOptions(
            crop_w=crop_window.crop_w,
            crop_h=crop_window.crop_h,
            crop_x=crop_window.crop_x,
            crop_y=crop_window.crop_y,
            target_width=cfg.target_width,
            target_height=cfg.target_height,
            use_nvenc=cfg.hardware.has_nvenc,
            nvenc_preset=cfg.hardware.nvenc_preset,
            nvenc_tune=cfg.hardware.nvenc_tune,
            video_bitrate=cfg.hardware.video_bitrate,
            max_rate=cfg.hardware.max_rate,
            buf_size=cfg.hardware.buf_size,
            audio_bitrate=cfg.hardware.audio_bitrate,
        )

        renderer.render_clip(
            input_video=input_video,
            start_time=cand.start_time,
            end_time=cand.end_time,
            output_video=out_clip_path,
            ass_subtitles=ass_file,
            options=render_opts,
            show_progress=(not output_json),
        )

        rendered_clips.append(
            {
                "clip_number": idx,
                "file": str(out_clip_path.resolve()),
                "headline": cand.headline,
                "start_time": cand.start_time,
                "end_time": cand.end_time,
                "duration": cand.duration,
                "virality_score": cand.virality_score,
                "hook_strength": cand.hook_strength,
                "crop": {
                    "w": crop_window.crop_w,
                    "h": crop_window.crop_h,
                    "x": crop_window.crop_x,
                },
                "subtitles": str(ass_path.resolve()) if ass_file else None,
            }
        )
        if not output_json:
            console.print(f"✓ Saved 9:16 short: [bold green]{out_clip_path.name}[/bold green]\n")

    if output_json:
        click.echo(json.dumps(rendered_clips, indent=2))
    else:
        console.print(
            Panel(
                f"[bold green]Successfully generated {len(rendered_clips)} shorts in:[/bold green]\n[cyan]{out_dir.resolve()}[/cyan]",
                title="✨ Pipeline Completed",
                border_style="green",
            )
        )


@main.command("gaming")
@click.argument("input_video", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--start", default=0.0, type=float, help="Start time in seconds")
@click.option("--end", default=30.0, type=float, help="End time in seconds")
@click.option("--out-dir", default="./output_shorts", type=click.Path(file_okay=False, path_type=Path), help="Output directory")
@click.option("--no-nvenc", is_flag=True, help="Disable NVENC hardware encoding")
def gaming_cmd(input_video, start, end, out_dir, no_nvenc):
    """Render a stacked gaming vertical short (Facecam Top 35% + Gameplay Bottom 65%)."""
    from autoclip.vision.gaming_stacked_crop import StackedGamingCropper

    cfg = AutoClipConfig()
    cfg.detect_environment()
    if no_nvenc:
        cfg.hardware.has_nvenc = False

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_clip = out_dir / f"{input_video.stem}_gaming_stacked_{start:.0f}s_{end:.0f}s.mp4"

    console.print(f"[bold magenta]🎮 Gaming Stacked Layout:[/bold magenta] {input_video.name}")
    console.print(f"Top 35% (1080x672) Facecam + Bottom 65% (1080x1248) Central Gameplay")

    cropper = StackedGamingCropper(face_detector_type=cfg.face_detector_type)
    cropper.render_stacked_clip(
        input_video=input_video,
        start_time=start,
        end_time=end,
        output_video=out_clip,
        use_nvenc=cfg.hardware.has_nvenc,
    )
    console.print(f"✓ Saved stacked gaming short: [bold green]{out_clip.resolve()}[/bold green]")


@main.command("detect-hype")
@click.argument("input_video", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--threshold-std", default=2.5, type=float, help="Z-score standard deviation threshold (default: 2.5)")
@click.option("--min-duration", default=3.0, type=float, help="Minimum consecutive spike duration in seconds (default: 3.0)")
@click.option("--json", "output_json", is_flag=True, help="Output JSON results")
def detect_hype_cmd(input_video, threshold_std, min_duration, output_json):
    """Detect loud hype, screaming, and team cheering moments via audio RMS energy spikes."""
    from autoclip.intelligence.audio_spike_detector import AudioSpikeDetector

    detector = AudioSpikeDetector(
        threshold_std=threshold_std,
        min_consecutive_sec=min_duration,
    )

    with console.status("[bold green]Analyzing audio stream RMS decibels and rolling baseline...", spinner="dots"):
        moments = detector.detect_spikes(input_video)

    if output_json:
        click.echo(json.dumps([m.to_dict() for m in moments], indent=2))
        return

    table = Table(title=f"Acoustic Hype & Excitement Spikes in {input_video.name}", border_style="bright_yellow")
    table.add_column("Rank", justify="center", style="bold yellow")
    table.add_column("Time Window", justify="center", style="cyan")
    table.add_column("Duration", justify="center")
    table.add_column("Peak Time", justify="center", style="magenta")
    table.add_column("Peak Energy Z-Score", justify="center", style="bold green")
    table.add_column("Peak Decibels", justify="center", style="bold red")

    for idx, m in enumerate(moments, 1):
        m_s, s_s = divmod(m.start_time, 60)
        m_e, s_e = divmod(m.end_time, 60)
        table.add_row(
            f"#{idx}",
            f"{int(m_s):02d}:{s_s:04.1f} - {int(m_e):02d}:{s_e:04.1f}",
            f"{m.duration:.1f}s",
            f"{m.peak_time:.1f}s",
            f"+{m.peak_intensity:.2f} σ",
            f"{m.peak_db:.1f} dB",
        )

    console.print(table)
    if not moments:
        console.print("[dim]No acoustic spikes exceeded +2.5 standard deviations for >= 3.0 seconds.[/dim]")


@main.command("publish")
@click.argument("video_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--title", required=True, help="Title of the YouTube Short")
@click.option("--publish-at", required=True, help="ISO 8601 UTC timestamp (e.g. 2026-10-28T18:00:00Z)")
@click.option("--desc", default="Auto-generated viral short via AutoClip-Local #Shorts", help="Description")
@click.option("--tags", default="Shorts,AutoClip,AI", help="Comma-separated tags")
def publish_cmd(video_path, title, publish_at, desc, tags):
    """Upload and schedule a video short to YouTube."""
    from autoclip.publisher.youtube_scheduler import YouTubePublisher

    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    publisher = YouTubePublisher()

    console.print(f"[bold cyan]Uploading and Scheduling YouTube Short:[/bold cyan] {video_path.name}")
    res = publisher.upload_scheduled_short(
        video_path=video_path,
        title=title,
        description=desc,
        publish_at=publish_at,
        tags=tag_list,
    )

    console.print(
        Panel(
            f"[bold green]Video Successfully Scheduled![/bold green]\n"
            f"• Title       : [bold]{res.title}[/bold]\n"
            f"• Video URL   : [cyan]{res.url}[/cyan]\n"
            f"• Publish At  : [yellow]{res.publish_at}[/yellow]\n"
            f"• Status      : [dim]{res.privacy_status}[/dim]",
            title="🚀 YouTube Scheduled",
            border_style="green",
        )
    )


@main.command("studio")
@click.option("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
@click.option("--port", default=8501, type=int, help="Port to bind (default: 8501)")
@click.option("--reload", is_flag=True, help="Enable auto-reload for development")
def studio_cmd(host, port, reload):
    """Launch the interactive AutoClip-Local Web Studio Dashboard."""
    import uvicorn
    console.print(
        Panel(
            f"[bold magenta]AutoClip-Local Studio Dashboard[/bold magenta]\n"
            f"• Web UI URL: [bold cyan]http://{host}:{port}[/bold cyan]\n"
            f"• Library   : [dim]library/unpublished & library/published[/dim]\n"
            f"• Mode      : 100% Local GPU-Accelerated Video Repurposing",
            title="🎬 Studio Server",
            border_style="magenta",
        )
    )
    uvicorn.run("autoclip.web.app:app", host=host, port=port, reload=reload)


@main.command("slice")
@click.argument("source", type=str)
@click.option("--duration", default=120.0, type=float, help="Segment duration in seconds (default: 120s)")
@click.option("--max-clips", default=None, type=int, help="Max clips to extract (default: all)")
@click.option("--output-dir", default="library/unpublished", help="Target output library folder")
@click.option("--direct/--download", default=True, help="Slice directly over HTTP streaming without downloading full VOD to disk")
def slice_cmd(source, duration, max_clips, output_dir, direct):
    """Slice a 1-to-5 hour stream (URL or local path) into 2-minute 9:16 vertical shorts."""
    from autoclip.stream.slicer import StreamSlicer

    slicer = StreamSlicer(nvenc_enabled=True)
    is_url = source.startswith("http://") or source.startswith("https://")

    if is_url and not direct:
        console.print(f"[bold cyan]Downloading live stream via yt-dlp:[/bold cyan] {source}")
        video_file = slicer.download_stream(source, Path("raw_streams"))
        source_target = str(video_file)
        is_url = False
    else:
        source_target = source

    if is_url:
        console.print(f"[bold green]Directly slicing online stream via HTTP seeking ({duration:.0f}s chunks)...[/bold green]")
    else:
        console.print(f"[bold green]Slicing '{Path(source_target).name}' into 9:16 vertical shorts ({duration:.0f}s each)...[/bold green]")

    clips = slicer.slice_stream_to_shorts(
        source=source_target,
        is_url=is_url,
        segment_duration=duration,
        library_dir=Path(output_dir),
        max_clips=max_clips,
    )
    if clips:
        console.print(f"✓ Generated [bold green]{len(clips)}[/bold green] vertical shorts in: [cyan]{output_dir}/{clips[0].stream_name}[/cyan]")
    else:
        console.print("[yellow]No clips produced. Please verify stream duration and source URL.[/yellow]")


@main.command("fuse-story")
@click.argument("clip_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--category", default="aita", help="Story category (aita, revenge, drama)")
@click.option("--voice", default="en-US-ChristopherNeural", help="Edge-TTS neural voice")
@click.option("--headline", default=None, help="Custom opening hook card headline")
@click.option("--story", default=None, help="Custom story text (or leave empty for AI generation)")
@click.option("--out", default=None, help="Output short video path")
def fuse_story_cmd(clip_path, category, voice, headline, story, out):
    """Fuse a viral drama story, neural voiceover, and kinetic subtitles onto a clip."""
    from autoclip.story.generator import StoryGenerator, StoryRequest
    from autoclip.story.narrator import StoryNarrator

    story_gen = StoryGenerator()
    narrator = StoryNarrator(nvenc_enabled=True)

    if not story:
        console.print(f"[bold green]Generating viral {category.upper()} story via local LLM...[/bold green]")
        gen_res = story_gen.generate_story(StoryRequest(category=category))
        story = gen_res.story_text
        headline = headline or gen_res.headline
    else:
        headline = headline or "TRUE STORY CONFESSION"

    out_path = Path(out) if out else clip_path.parent / f"{clip_path.stem}_story.mp4"
    console.print(f"[bold cyan]Headline:[/bold cyan] {headline}")
    console.print(f"[bold cyan]Rendering with voice:[/bold cyan] {voice}")

    res = narrator.fuse_story_and_render(
        background_video_path=clip_path,
        story_text=story,
        headline=headline,
        output_video_path=out_path,
        voice_id=voice,
    )
    console.print(f"✓ Final short rendered: [bold green]{res.final_video_path}[/bold green] ({res.duration:.1f}s)")


if __name__ == "__main__":
    main()
