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


if __name__ == "__main__":
    main()
