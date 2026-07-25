#!/usr/bin/env python3
"""Assemble a finished video from a script's audio and scene images.

Times each image to the portion of the narration it corresponds to (based on
word count), so images change roughly in sync with the story, then muxes in
the narration audio track.

Usage:
    python assemble_video.py
    python assemble_video.py scripts/20260724_your-topic.txt
"""

import argparse
import json
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

from project_paths import AUDIO_DIR, IMAGES_DIR, SCRIPTS_DIR, VIDEOS_DIR


WORDS_PER_SCENE = 50  # must match generate_images.py
WORDS_PER_CAPTION = 8
TRANSITION_SECONDS = 1.0
VIDEO_WIDTH = 1280
VIDEO_HEIGHT = 720
VIDEO_FPS = 24


def select_script(script_argument: str | None) -> Path:
    """Resolve a supplied script path or prompt for one from scripts/."""
    if script_argument:
        return Path(script_argument)

    scripts_dir = SCRIPTS_DIR
    available_scripts = sorted(
        (path for path in scripts_dir.glob("*.txt") if path.is_file()),
        key=lambda path: path.name,
        reverse=True,
    )
    if not available_scripts:
        raise SystemExit(f"No .txt scripts found in {scripts_dir}")

    print("Available narration scripts:")
    for number, script_path in enumerate(available_scripts, start=1):
        print(f"  {number}. {script_path.name}")

    while True:
        try:
            selection = input("Select a script number: ").strip()
            selected_index = int(selection) - 1
            if 0 <= selected_index < len(available_scripts):
                return available_scripts[selected_index]
        except ValueError:
            pass
        except (EOFError, KeyboardInterrupt) as exc:
            raise SystemExit("\nScript selection cancelled.") from exc

        print(f"Enter a number from 1 to {len(available_scripts)}.")


def count_scene_words(text: str, words_per_scene: int = WORDS_PER_SCENE) -> list[int]:
    """Return the word count of each scene, matching generate_images.py's split."""
    words = text.split()
    counts = []
    for i in range(0, len(words), words_per_scene):
        counts.append(len(words[i:i + words_per_scene]))
    return counts


def audio_duration(audio_path: Path) -> float:
    """Return the duration of a PCM WAV file in seconds."""
    with wave.open(str(audio_path), "rb") as audio_file:
        return audio_file.getnframes() / audio_file.getframerate()


def srt_timestamp(seconds: float) -> str:
    total_milliseconds = max(0, round(seconds * 1_000))
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{milliseconds:03d}"


def load_caption_segments(
    text: str, duration: float, timing_path: Path
) -> list[tuple[str, float, float]]:
    """Load measured TTS boundaries, falling back for legacy audio files."""
    if timing_path.is_file():
        try:
            payload = json.loads(timing_path.read_text(encoding="utf-8"))
            measured_duration = float(payload["audio_duration"])
            tolerance = max(0.25, duration * 0.01)
            if abs(measured_duration - duration) > tolerance:
                raise ValueError("timing data does not match the narration audio")
            segments = [
                (item["text"], float(item["start"]), float(item["end"]))
                for item in payload["segments"]
                if item["text"] and float(item["end"]) > float(item["start"])
            ]
            if segments:
                return segments
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            print(f"Warning: ignoring invalid caption timing data: {timing_path}")

    print("Warning: measured caption timing is missing; using legacy estimated timing.")
    return [(text, 0.0, duration)]


def write_caption_file(
    text: str, duration: float, output_dir: Path, timing_path: Path
) -> Path:
    """Create captions within measured TTS segment boundaries."""
    if not text.split():
        raise SystemExit("The selected script is empty; captions cannot be created.")

    segments = load_caption_segments(text, duration, timing_path)

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".srt",
        prefix="video_captions_",
        dir=output_dir,
        encoding="utf-8",
        delete=False,
    ) as captions:
        caption_number = 1
        for segment_text, segment_start, segment_end in segments:
            words = segment_text.split()
            segment_duration = segment_end - segment_start
            for start_index in range(0, len(words), WORDS_PER_CAPTION):
                caption_words = words[start_index:start_index + WORDS_PER_CAPTION]
                end_index = start_index + len(caption_words)
                start_time = segment_start + (start_index / len(words)) * segment_duration
                end_time = segment_start + (end_index / len(words)) * segment_duration
                captions.write(f"{caption_number}\n")
                captions.write(
                    f"{srt_timestamp(start_time)} --> {srt_timestamp(end_time)}\n"
                )
                captions.write(" ".join(caption_words) + "\n\n")
                caption_number += 1

        return Path(captions.name)


def build_video_filter(
    scene_durations: list[float],
    caption_filename: str,
) -> tuple[str, list[float]]:
    """Build scaling, crossfade, and burned-caption filters for ffmpeg."""
    transition = min(
        TRANSITION_SECONDS,
        min(scene_durations) / 2 if len(scene_durations) > 1 else 0,
    )
    input_durations = [
        duration if index == 0 else duration + transition
        for index, duration in enumerate(scene_durations)
    ]

    filters = []
    for index in range(len(scene_durations)):
        filters.append(
            f"[{index}:v]"
            f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},"
            "setsar=1,format=yuv420p,settb=AVTB,setpts=PTS-STARTPTS,"
            f"fps={VIDEO_FPS}"
            f"[scene{index}]"
        )

    current_label = "scene0"
    current_duration = input_durations[0]
    for index in range(1, len(scene_durations)):
        output_label = f"fade{index}"
        offset = max(0, current_duration - transition)
        filters.append(
            f"[{current_label}][scene{index}]"
            f"xfade=transition=fade:duration={transition:.3f}:offset={offset:.6f}"
            f"[{output_label}]"
        )
        current_label = output_label
        current_duration += input_durations[index] - transition

    caption_style = (
        "FontName=Arial,FontSize=22,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1,"
        "Alignment=2,MarginV=42"
    )
    filters.append(
        f"[{current_label}]subtitles=filename='{caption_filename}':"
        f"force_style='{caption_style}',format=yuv420p[video]"
    )
    return ";".join(filters), input_durations


def render_video(
    image_paths: list[Path],
    scene_durations: list[float],
    audio_path: Path,
    script_text: str,
    output_path: Path,
) -> None:
    """Render crossfaded images, captions, and narration using ffmpeg."""
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        raise SystemExit("ffmpeg was not found on PATH.")

    caption_path = write_caption_file(
        script_text,
        sum(scene_durations),
        output_path.parent,
        audio_path.with_suffix(".timings.json"),
    )
    video_filter, input_durations = build_video_filter(
        scene_durations,
        caption_path.name,
    )
    temporary_output_path = output_path.with_suffix(".rendering.mp4")
    try:
        command = [ffmpeg_path, "-y"]
        for image_path, duration in zip(image_paths, input_durations):
            command.extend(
                [
                    "-loop",
                    "1",
                    "-framerate",
                    str(VIDEO_FPS),
                    "-t",
                    f"{duration:.6f}",
                    "-i",
                    str(image_path),
                ]
            )

        audio_input_index = len(image_paths)
        command.extend(
            [
                "-i",
                str(audio_path),
                "-filter_complex",
                video_filter,
                "-map",
                "[video]",
                "-map",
                f"{audio_input_index}:a:0",
                "-c:v",
                "libx264",
                "-profile:v",
                "main",
                "-level:v",
                "3.1",
                "-pix_fmt",
                "yuv420p",
                "-crf",
                "20",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-shortest",
                "-movflags",
                "+faststart",
                str(temporary_output_path),
            ]
        )
        subprocess.run(command, check=True, cwd=output_path.parent)
        temporary_output_path.replace(output_path)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"ffmpeg failed with exit code {exc.returncode}.") from exc
    finally:
        caption_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Assemble a finished video from a script's audio and images."
    )
    parser.add_argument(
        "script_path",
        nargs="?",
        help="Path to the .txt script file; omit to select one interactively",
    )
    args = parser.parse_args()

    script_path = select_script(args.script_path)
    if not script_path.exists():
        raise SystemExit(f"Script file not found: {script_path}")

    audio_path = AUDIO_DIR / f"{script_path.stem}.wav"
    image_dir = IMAGES_DIR / script_path.stem

    if not audio_path.exists():
        raise SystemExit(
            f"Audio file not found: {audio_path}\n"
            "Run generate_audio.py for this script first."
        )
    if not image_dir.exists():
        raise SystemExit(
            f"Image folder not found: {image_dir}\n"
            "Run generate_images.py for this script first."
        )

    image_paths = sorted(
        path
        for path in image_dir.glob("scene_*.*")
        if path.suffix.lower() in {".jpg", ".jpeg", ".png"}
    )
    if not image_paths:
        raise SystemExit(f"No images found in {image_dir}")

    text = script_path.read_text(encoding="utf-8")
    scene_word_counts = count_scene_words(text)

    if len(scene_word_counts) != len(image_paths):
        print(
            f"Warning: script has {len(scene_word_counts)} scenes but "
            f"{len(image_paths)} images were found. Spreading time evenly "
            "across images as a fallback."
        )
        scene_word_counts = [1] * len(image_paths)

    print(f"Loading audio: {audio_path.name}")
    total_duration = audio_duration(audio_path)
    total_words = sum(scene_word_counts)
    scene_durations = [
        (word_count / total_words) * total_duration
        for word_count in scene_word_counts
    ]

    print(f"Audio duration: {total_duration / 60:.1f} minutes")
    print(f"Building {len(image_paths)} timed image clips...")

    output_dir = VIDEOS_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{script_path.stem}.mp4"

    print(f"Rendering video to {output_path} (this can take a while)...")
    render_video(image_paths, scene_durations, audio_path, text, output_path)

    print(f"\nDone. Saved video to {output_path}")


if __name__ == "__main__":
    main()
