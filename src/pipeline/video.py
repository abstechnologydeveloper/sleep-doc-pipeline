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
import re
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

from project_paths import AUDIO_DIR, IMAGES_DIR, SCRIPTS_DIR, THUMBNAILS_DIR, VIDEOS_DIR


WORDS_PER_SCENE = 50  # must match generate_images.py
WORDS_PER_CAPTION = 8
TRANSITION_SECONDS = 1.0
VIDEO_WIDTH = 1280
VIDEO_HEIGHT = 720
VIDEO_FPS = 24
EFFECT_POINTS = (
    (96, 88, 2.9, 0.2),
    (211, 142, 3.7, 1.1),
    (337, 76, 3.2, 2.0),
    (482, 176, 4.1, 0.7),
    (629, 104, 3.5, 1.8),
    (773, 157, 4.4, 2.6),
    (918, 69, 3.1, 0.9),
    (1061, 129, 3.9, 2.2),
    (1176, 91, 4.6, 1.4),
)


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


def split_scene_text(text: str, words_per_scene: int = WORDS_PER_SCENE) -> list[str]:
    words = text.split()
    return [
        " ".join(words[index:index + words_per_scene])
        for index in range(0, len(words), words_per_scene)
    ]


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
    scene_texts: list[str],
    title_filename: str | None = None,
) -> tuple[str, list[float]]:
    """Build cinematic movement, contextual effects, fades, and captions."""
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
        scene_text = scene_texts[index].lower() if index < len(scene_texts) else ""
        frame_count = max(1, round(input_durations[index] * VIDEO_FPS))
        if index % 2:
            pan_x = f"(iw-iw/zoom)*(1-on/{frame_count})"
        else:
            pan_x = f"(iw-iw/zoom)*on/{frame_count}"

        if any(word in scene_text for word in ("fireplace", "hearth", "candle", "lantern")):
            grade = "eq=saturation=1.16:contrast=1.05:gamma_r=1.04:gamma_b=0.97"
            effect = "embers"
        elif any(word in scene_text for word in ("rain", "storm", "snow", "mist", "fog")):
            grade = "eq=saturation=1.09:contrast=1.04:gamma_r=0.98:gamma_b=1.04"
            effect = ""
        elif any(
            phrase in scene_text
            for phrase in ("starlit", "starry", "constellation", "night sky", "stars above")
        ):
            grade = "eq=saturation=1.12:contrast=1.05:gamma_b=1.04"
            effect = "stars"
        elif any(word in scene_text for word in ("firefly", "fireflies", "enchanted", "magical garden")):
            grade = "eq=saturation=1.15:contrast=1.04:gamma_g=1.03"
            effect = "motes"
        else:
            grade = "eq=saturation=1.12:contrast=1.04"
            effect = ""

        scene_filter = (
            f"[{index}:v]"
            f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},"
            f"zoompan=z='min(zoom+0.00015,1.06)':x='{pan_x}':"
            f"y='ih/2-(ih/zoom/2)':d=1:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:fps={VIDEO_FPS},"
            f"{grade},vignette=PI/5,setsar=1,format=yuv420p,settb=AVTB,setpts=PTS-STARTPTS"
        )
        if effect:
            color = "white" if effect == "stars" else "0xffd27f"
            y_offset = 0 if effect == "stars" else 245
            for point, (x, y, period, phase) in enumerate(EFFECT_POINTS):
                size = 2 + (point % 2)
                if effect == "stars":
                    effect_x = str(x)
                    effect_y = str(y)
                else:
                    effect_x = f"{x}+10*sin(t*0.35+{phase})"
                    effect_y = f"{y + y_offset}-mod(t*5+{phase * 18:.1f},110)"
                scene_filter += (
                    f",drawbox=x='{effect_x}':y='{effect_y}':w={size}:h={size}:"
                    f"color={color}@0.65:t=fill:"
                    f"enable='lt(mod(t+{phase},{period}),0.7)'"
                )
        scene_filter += f"[scene{index}]"
        filters.append(
            scene_filter
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

    if title_filename:
        filters.append(
            f"[{current_label}]subtitles=filename='{title_filename}'[titled]"
        )
        current_label = "titled"

    caption_style = (
        "FontName=Arial,FontSize=22,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BackColour=&H80000000,"
        "BorderStyle=3,Outline=1,Shadow=0,"
        "Alignment=2,MarginV=42"
    )
    filters.append(
        f"[{current_label}]subtitles=filename='{caption_filename}':"
        f"force_style='{caption_style}',format=yuv420p[video]"
    )
    return ";".join(filters), input_durations


def thumbnail_title(title: str, script_stem: str) -> str:
    """Create a calm, readable three-to-six-word thumbnail headline."""
    candidate = title.strip()
    if not candidate:
        candidate = re.sub(r"^\d{8}_\d{6}_", "", script_stem).replace("-", " ")
    candidate = re.split(r"\s+[|—–]\s+", candidate, maxsplit=1)[0]
    candidate = re.sub(
        r"\b(?:sleep ambient|sleep story|relaxing music)\b.*$",
        "",
        candidate,
        flags=re.IGNORECASE,
    ).strip(" -:|")
    words = candidate.split()[:6]
    if not words:
        words = ["A", "CALM", "NIGHT", "STORY"]
    split_at = (len(words) + 1) // 2
    lines = [" ".join(words[:split_at]), " ".join(words[split_at:])]
    return r"\N".join(line.upper() for line in lines if line)


def opening_title(title: str) -> str:
    words = title.strip().split()[:10]
    if not words:
        return ""
    split_at = (len(words) + 1) // 2
    lines = [" ".join(words[:split_at]), " ".join(words[split_at:])]
    return r"\N".join(line for line in lines if line)


def safe_ass_text(value: str) -> str:
    return value.replace("{", "(").replace("}", ")").replace("\n", " ")


def write_opening_title_file(title: str, output_dir: Path) -> Path | None:
    display_title = opening_title(title)
    if not display_title:
        return None
    safe_title = safe_ass_text(display_title)
    ass_content = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {VIDEO_WIDTH}
PlayResY: {VIDEO_HEIGHT}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Opening,Arial,34,&H00FFFFFF,&H00FFFFFF,&H00101010,&H50000000,-1,0,0,0,100,100,1,0,1,2,1,7,52,52,54,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.35,0:00:05.50,Opening,,0,0,0,,{{\\fad(700,1000)}}{safe_title}
"""
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".ass",
        prefix="opening_title_",
        dir=output_dir,
        encoding="utf-8",
        delete=False,
    ) as title_file:
        title_file.write(ass_content)
        return Path(title_file.name)


def write_thumbnail_title_file(title: str, output_dir: Path) -> Path:
    safe_title = safe_ass_text(title)
    ass_content = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {VIDEO_WIDTH}
PlayResY: {VIDEO_HEIGHT}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Title,Arial,58,&H00FFFFFF,&H00FFFFFF,&H00000000,&H88000000,-1,0,0,0,100,100,1,0,3,12,0,1,70,70,58,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:05.00,Title,,0,0,0,,{safe_title}
"""
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".ass",
        prefix="thumbnail_title_",
        dir=output_dir,
        encoding="utf-8",
        delete=False,
    ) as title_file:
        title_file.write(ass_content)
        return Path(title_file.name)


def render_thumbnail(
    ffmpeg_path: str,
    image_paths: list[Path],
    thumbnail_source: Path | None,
    title: str,
    script_stem: str,
) -> Path:
    """Render a dedicated high-contrast 16:9 thumbnail from the richest scene."""
    THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
    source_image = thumbnail_source or max(
        image_paths, key=lambda path: path.stat().st_size
    )
    output_path = THUMBNAILS_DIR / f"{script_stem}.jpg"
    temporary_output = output_path.with_suffix(".rendering.jpg")
    title_path = write_thumbnail_title_file(
        thumbnail_title(title, script_stem), THUMBNAILS_DIR
    )
    thumbnail_filter = (
        f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},"
        "eq=saturation=1.2:contrast=1.08:brightness=-0.015,"
        "unsharp=5:5:0.6:5:5:0,vignette=PI/5,"
        f"subtitles=filename='{title_path.name}'"
    )
    try:
        subprocess.run(
            [
                ffmpeg_path,
                "-y",
                "-i",
                str(source_image),
                "-vf",
                thumbnail_filter,
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(temporary_output),
            ],
            check=True,
            cwd=THUMBNAILS_DIR,
        )
        temporary_output.replace(output_path)
        return output_path
    finally:
        title_path.unlink(missing_ok=True)
        temporary_output.unlink(missing_ok=True)


def render_video(
    image_paths: list[Path],
    scene_durations: list[float],
    audio_path: Path,
    script_text: str,
    output_path: Path,
    scene_texts: list[str],
    title: str = "",
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
    title_path = write_opening_title_file(title, output_path.parent)
    video_filter, input_durations = build_video_filter(
        scene_durations,
        caption_path.name,
        scene_texts,
        title_path.name if title_path else None,
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
        if title_path:
            title_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Assemble a finished video from a script's audio and images."
    )
    parser.add_argument(
        "script_path",
        nargs="?",
        help="Path to the .txt script file; omit to select one interactively",
    )
    parser.add_argument(
        "--title",
        default="",
        help="Optional post title used for the generated thumbnail",
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
    thumbnail_source = next(
        (
            image_dir / f"thumbnail_source{suffix}"
            for suffix in (".jpg", ".jpeg", ".png")
            if (image_dir / f"thumbnail_source{suffix}").is_file()
        ),
        None,
    )

    text = script_path.read_text(encoding="utf-8")
    scene_word_counts = count_scene_words(text)
    scene_texts = split_scene_text(text)

    if len(scene_word_counts) != len(image_paths):
        print(
            f"Warning: script has {len(scene_word_counts)} scenes but "
            f"{len(image_paths)} images were found. Spreading time evenly "
            "across images as a fallback."
        )
        scene_word_counts = [1] * len(image_paths)
        scene_texts = [""] * len(image_paths)

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
    render_video(
        image_paths,
        scene_durations,
        audio_path,
        text,
        output_path,
        scene_texts,
        args.title,
    )
    thumbnail_path = render_thumbnail(
        shutil.which("ffmpeg") or "ffmpeg",
        image_paths,
        thumbnail_source,
        args.title,
        script_path.stem,
    )

    print(f"\nDone. Saved video to {output_path}")
    print(f"Thumbnail saved to {thumbnail_path}")


if __name__ == "__main__":
    main()
