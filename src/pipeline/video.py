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
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

from project_paths import (
    AUDIO_DIR,
    IMAGES_DIR,
    SCRIPTS_DIR,
    SOUNDS_DIR,
    THUMBNAILS_DIR,
    VIDEOS_DIR,
)


WORDS_PER_SCENE = 50  # must match generate_images.py
WORDS_PER_CAPTION = 5
TRANSITION_SECONDS = 2.0
VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080
VIDEO_FPS = 24
THUMBNAIL_WIDTH = 3840
THUMBNAIL_HEIGHT = 2160
MAX_YOUTUBE_THUMBNAIL_BYTES = 1_950_000


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


def load_story_scenes(image_dir: Path, text: str) -> list[dict]:
    """Load dynamic story beats, falling back to legacy fixed-size projects."""
    plan_path = image_dir / "scene_plan.json"
    if plan_path.is_file():
        payload = {}
        try:
            payload = json.loads(plan_path.read_text(encoding="utf-8"))
            scenes = payload.get("scenes")
            if payload.get("version") in {2, 3, 4, 5} and isinstance(scenes, list) and scenes:
                expected_start = 0
                normalized = []
                for index, scene in enumerate(scenes, start=1):
                    start_word = int(scene["start_word"])
                    end_word = int(scene["end_word"])
                    if (
                        scene.get("id") != f"scene_{index:03d}"
                        or start_word != expected_start
                        or end_word <= start_word
                    ):
                        raise ValueError("invalid dynamic scene coverage")
                    normalized.append(scene)
                    expected_start = end_word
                if expected_start != len(text.split()):
                    raise ValueError("dynamic scenes do not cover the full script")
                return normalized
        except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
            if payload.get("version") in {2, 3, 4, 5}:
                raise SystemExit(
                    f"Dynamic scene plan is invalid: {plan_path}. "
                    "Regenerate the scene plan before assembling the video."
                )
            print("Warning: invalid dynamic scene plan; using legacy scene timing.")

    scene_texts = split_scene_text(text)
    scenes = []
    word_cursor = 0
    for index, narration in enumerate(scene_texts, start=1):
        word_count = len(narration.split())
        scenes.append(
            {
                "id": f"scene_{index:03d}",
                "start_word": word_cursor,
                "end_word": word_cursor + word_count,
                "word_count": word_count,
                "narration": narration,
                "reuse_scene_id": None,
            }
        )
        word_cursor += word_count
    return scenes


def resolve_scene_images(image_dir: Path, scenes: list[dict]) -> list[Path]:
    """Resolve one visual per story beat, including deliberate visual reuse."""
    image_paths = []
    for scene in scenes:
        image_id = scene.get("reuse_scene_id") or scene["id"]
        image_path = next(
            (
                image_dir / f"{image_id}{suffix}"
                for suffix in (".jpg", ".jpeg", ".png")
                if (image_dir / f"{image_id}{suffix}").is_file()
            ),
            None,
        )
        if image_path is None:
            raise SystemExit(f"Image for {scene['id']} was not found in {image_dir}")
        image_paths.append(image_path)
    return image_paths


def load_scene_directions(image_dir: Path, scene_count: int) -> list[dict]:
    """Return the fixed still-image and fade-only direction contract."""
    return [
        {"camera": "static", "transition": "fade", "atmosphere": "none"}
        for _index in range(scene_count)
    ]


def load_thumbnail_hook(image_dir: Path) -> str:
    plan_path = image_dir / "scene_plan.json"
    if not plan_path.is_file():
        return ""
    try:
        payload = json.loads(plan_path.read_text(encoding="utf-8"))
        hook = str(payload.get("thumbnail_hook", "")).strip()
        return " ".join(hook.split()[:4])
    except (OSError, json.JSONDecodeError, TypeError):
        return ""


def load_sound_cues(
    script_stem: str, scenes: list[dict], scene_durations: list[float]
) -> list[dict]:
    """Load generated effects and convert scene-relative positions to timestamps."""
    sound_dir = SOUNDS_DIR / script_stem
    manifest_path = sound_dir / "sound_manifest.json"
    if not manifest_path.is_file():
        return []
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print(f"Warning: ignoring invalid sound manifest: {manifest_path}")
        return []

    scene_starts = []
    elapsed = 0.0
    for duration in scene_durations:
        scene_starts.append(elapsed)
        elapsed += duration

    cues = []
    scene_indexes = {scene["id"]: index for index, scene in enumerate(scenes)}
    for cue in payload.get("cues", []):
        try:
            if cue.get("scene_id"):
                scene_index = scene_indexes[str(cue["scene_id"])]
            else:
                scene_index = int(cue["scene_index"]) - 1
            position = min(1.0, max(0.0, float(cue.get("position", 0.5))))
            volume = min(0.16, max(0.04, float(cue.get("volume", 0.10))))
            duration = min(4.0, max(0.5, float(cue.get("duration_seconds", 2.0))))
            path = sound_dir / Path(str(cue["filename"])).name
        except (KeyError, TypeError, ValueError):
            continue
        if not 0 <= scene_index < len(scene_durations) or not path.is_file():
            continue
        cues.append(
            {
                "path": path,
                "start": scene_starts[scene_index] + position * scene_durations[scene_index],
                "volume": volume,
                "duration": duration,
            }
        )
    return cues


def load_continuous_ambience(script_stem: str) -> dict | None:
    """Load one generated ambience bed that ffmpeg will loop under narration."""
    sound_dir = SOUNDS_DIR / script_stem
    manifest_path = sound_dir / "sound_manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        ambience = payload.get("ambience")
        if not isinstance(ambience, dict):
            return None
        path = sound_dir / Path(str(ambience["filename"])).name
        volume = min(0.06, max(0.01, float(ambience.get("volume", 0.035))))
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return {"path": path, "volume": volume} if path.is_file() else None


def load_background_music(script_stem: str) -> dict | None:
    """Load one quiet instrumental track that FFmpeg loops beneath narration."""
    sound_dir = SOUNDS_DIR / script_stem
    manifest_path = sound_dir / "sound_manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        music = payload.get("music")
        if not isinstance(music, dict):
            return None
        path = sound_dir / Path(str(music["filename"])).name
        volume = min(0.06, max(0.02, float(music.get("volume", 0.045))))
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return {"path": path, "volume": volume} if path.is_file() else None


def timed_scene_durations(
    scenes: list[dict], text: str, duration: float, timing_path: Path
) -> list[float]:
    """Map exact story word ranges onto measured TTS chunk boundaries."""
    word_times: list[tuple[float, float]] = []
    if timing_path.is_file():
        try:
            payload = json.loads(timing_path.read_text(encoding="utf-8"))
            measured_duration = float(payload["audio_duration"])
            if abs(measured_duration - duration) > max(0.25, duration * 0.01):
                raise ValueError("timing data does not match narration")
            for segment in payload["segments"]:
                count = len(str(segment["text"]).split())
                start = float(segment["start"])
                end = float(segment["end"])
                if count < 1 or end <= start:
                    continue
                per_word = (end - start) / count
                word_times.extend(
                    (start + offset * per_word, start + (offset + 1) * per_word)
                    for offset in range(count)
                )
        except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
            word_times = []

    if len(word_times) == len(text.split()):
        return [
            word_times[int(scene["end_word"]) - 1][1]
            - word_times[int(scene["start_word"])][0]
            for scene in scenes
        ]

    print("Warning: scene timing is estimated because measured word alignment is unavailable.")
    total_words = sum(int(scene["word_count"]) for scene in scenes)
    return [int(scene["word_count"]) / total_words * duration for scene in scenes]


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
    transcript_path = timing_path.with_name(
        timing_path.name.replace(".timings.json", ".transcript.srt")
    )
    if transcript_path.is_file():
        try:
            transcript = transcript_path.read_text(encoding="utf-8-sig")
            matches = re.findall(
                r"(?m)^\d+\s*\n"
                r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*"
                r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*\n"
                r"(.+?)(?=\n\s*\n|\Z)",
                transcript,
                flags=re.DOTALL,
            )
            segments = []
            for match in matches:
                values = [int(value) for value in match[:8]]
                start = values[0] * 3600 + values[1] * 60 + values[2] + values[3] / 1000
                end = values[4] * 3600 + values[5] * 60 + values[6] + values[7] / 1000
                spoken_text = " ".join(match[8].replace("\n", " ").split())
                if spoken_text and end > start:
                    segments.append((spoken_text, start, min(end, duration)))
            if segments and abs(segments[-1][2] - duration) <= max(2.0, duration * 0.03):
                return segments
        except (OSError, TypeError, ValueError):
            pass
        print(f"Warning: ignoring invalid speech transcript: {transcript_path}")

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
    scene_directions: list[dict] | None = None,
    use_transitions: bool = True,
) -> tuple[str, list[float]]:
    """Build completely still images with soft fades and captions."""
    transition = (
        min(
            TRANSITION_SECONDS,
            min(scene_durations) / 2 if len(scene_durations) > 1 else 0,
        )
        if use_transitions
        else 0
    )
    input_durations = [
        duration if index == 0 else duration + transition
        for index, duration in enumerate(scene_durations)
    ]

    filters = []
    for index in range(len(scene_durations)):
        scene_filter = (
            f"[{index}:v]"
            f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},"
            "eq=saturation=1.08:contrast=1.035,vignette=PI/5,"
            "setsar=1,format=yuv420p,"
            f"fps={VIDEO_FPS},settb=1/{VIDEO_FPS},"
            f"setpts=N/({VIDEO_FPS}*TB)"
        )
        scene_filter += f"[scene{index}]"
        filters.append(scene_filter)

    if use_transitions and len(scene_durations) > 1:
        current_label = "scene0"
        current_duration = input_durations[0]
        for index in range(1, len(scene_durations)):
            output_label = f"fade{index}"
            offset = max(0, current_duration - transition)
            filters.append(
                f"[{current_label}][scene{index}]"
                f"xfade=transition=fade:duration={transition:.3f}:"
                f"offset={offset:.6f}"
                f",fps={VIDEO_FPS},settb=1/{VIDEO_FPS},"
                f"setpts=N/({VIDEO_FPS}*TB)"
                f"[{output_label}]"
            )
            current_label = output_label
            current_duration += input_durations[index] - transition
    elif len(scene_durations) > 1:
        current_label = "joined"
        fallback_fade = min(0.75, min(scene_durations) / 3)
        fallback_labels = []
        for index, duration in enumerate(scene_durations):
            output_label = f"softcut{index}"
            fade_filters = []
            if index > 0:
                fade_filters.append(f"fade=t=in:st=0:d={fallback_fade:.3f}")
            if index < len(scene_durations) - 1:
                fade_start = max(0, duration - fallback_fade)
                fade_filters.append(
                    f"fade=t=out:st={fade_start:.3f}:d={fallback_fade:.3f}"
                )
            filters.append(
                f"[scene{index}]{','.join(fade_filters)}[{output_label}]"
            )
            fallback_labels.append(f"[{output_label}]")
        filters.append(
            "".join(fallback_labels)
            + f"concat=n={len(scene_durations)}:v=1:a=0[{current_label}]"
        )
    else:
        current_label = "scene0"

    if title_filename:
        filters.append(
            f"[{current_label}]subtitles=filename='{title_filename}'[titled]"
        )
        current_label = "titled"

    caption_style = (
        "FontName=Arial,FontSize=27,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BackColour=&H00000000,"
        "BorderStyle=1,Outline=3,Shadow=1,"
        "Alignment=2,MarginV=28"
    )
    filters.append(
        f"[{current_label}]subtitles=filename='{caption_filename}':"
        f"force_style='{caption_style}',format=yuv420p[video]"
    )
    return ";".join(filters), input_durations


def thumbnail_title(title: str, script_stem: str) -> str:
    """Create a readable two-to-four-word thumbnail headline."""
    candidate = title.strip()
    if not candidate:
        candidate = re.sub(r"^\d{8}_\d{6}_", "", script_stem).replace("-", " ")
    candidate = re.split(r"\s+[|—–]\s+", candidate, maxsplit=1)[0]
    candidate = re.sub(
        r"^(?:tell\s+)?(?:the\s+)?(?:true\s+)?story\s+of\s+",
        "",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\b(?:sleep ambient|sleep story|relaxing music)\b.*$",
        "",
        candidate,
        flags=re.IGNORECASE,
    ).strip(" -:|")
    words = candidate.split()[:4]
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
Style: Opening,Arial,50,&H00FFFFFF,&H00FFFFFF,&H00101010,&H50000000,-1,0,0,0,100,100,1,0,1,3,1,7,78,78,80,1

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
PlayResX: {THUMBNAIL_WIDTH}
PlayResY: {THUMBNAIL_HEIGHT}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Title,DejaVu Sans,238,&H00FFFFFF,&H00FFFFFF,&H00101010,&H70000000,-1,0,0,0,100,100,2,0,1,20,8,4,190,2050,150,1

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
    curiosity_hook: str = "",
) -> Path:
    """Render a dedicated high-contrast 16:9 thumbnail from the richest scene."""
    THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
    source_image = thumbnail_source or max(
        image_paths, key=lambda path: path.stat().st_size
    )
    output_path = THUMBNAILS_DIR / f"{script_stem}.jpg"
    temporary_output = output_path.with_suffix(".rendering.jpg")
    title_path = write_thumbnail_title_file(
        thumbnail_title(title or curiosity_hook, script_stem), THUMBNAILS_DIR
    )
    thumbnail_filter = (
        f"scale={THUMBNAIL_WIDTH}:{THUMBNAIL_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={THUMBNAIL_WIDTH}:{THUMBNAIL_HEIGHT},"
        "eq=saturation=1.30:contrast=1.15:brightness=0.015,"
        "unsharp=7:7:0.85:5:5:0,vignette=PI/7,"
        "drawbox=x=0:y=0:w=iw*0.54:h=ih:color=black@0.34:t=fill,"
        f"subtitles=filename='{title_path.name}'"
    )
    try:
        for jpeg_quality in (5, 8, 11, 14):
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
                    str(jpeg_quality),
                    str(temporary_output),
                ],
                check=True,
                cwd=THUMBNAILS_DIR,
            )
            if temporary_output.stat().st_size <= MAX_YOUTUBE_THUMBNAIL_BYTES:
                break
        temporary_output.replace(output_path)
        return output_path
    finally:
        title_path.unlink(missing_ok=True)
        temporary_output.unlink(missing_ok=True)


def validate_rendered_video(
    ffmpeg_path: str,
    video_path: Path,
    expected_duration: float,
    caption_path: Path,
    image_paths: list[Path],
) -> dict:
    """Reject technically broken output and report non-fatal creative warnings."""
    ffprobe_path = shutil.which("ffprobe")
    if not ffprobe_path:
        raise RuntimeError("ffprobe is required to verify the finished video")
    probe = subprocess.run(
        [
            ffprobe_path, "-v", "error", "-show_entries",
            "format=duration:stream=codec_type,width,height", "-of", "json",
            str(video_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(probe.stdout or "{}")
    streams = payload.get("streams", [])
    video_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "video"), None
    )
    audio_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "audio"), None
    )
    duration = float(payload.get("format", {}).get("duration", 0))
    failures = []
    warnings = []
    if not video_stream:
        failures.append("missing video stream")
    if not audio_stream:
        failures.append("missing audio stream")
    if video_stream and (
        int(video_stream.get("width", 0)) != VIDEO_WIDTH
        or int(video_stream.get("height", 0)) != VIDEO_HEIGHT
    ):
        failures.append("incorrect video dimensions")
    if abs(duration - expected_duration) > max(2.0, expected_duration * 0.02):
        failures.append("video duration does not match narration")

    media_check = subprocess.run(
        [
            ffmpeg_path, "-hide_banner", "-nostats", "-i", str(video_path),
            "-vf", "blackdetect=d=2:pix_th=0.05",
            "-af", "volumedetect", "-f", "null", "-",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    black_durations = [
        float(value)
        for value in re.findall(r"black_duration:([0-9.]+)", media_check.stderr)
    ]
    if any(value >= 2 for value in black_durations):
        failures.append("long black video section detected")

    mean_match = re.search(r"mean_volume:\s*(-?[0-9.]+) dB", media_check.stderr)
    mean_volume = float(mean_match.group(1)) if mean_match else None
    if mean_volume is None:
        warnings.append("narration volume could not be measured")
    elif mean_volume < -45:
        failures.append("audio is effectively silent")

    caption_text = caption_path.read_text(encoding="utf-8")
    caption_ends = re.findall(
        r"-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})", caption_text
    )
    caption_end = 0.0
    if caption_ends:
        hours, minutes, seconds, milliseconds = map(int, caption_ends[-1])
        caption_end = hours * 3600 + minutes * 60 + seconds + milliseconds / 1000
    if abs(caption_end - expected_duration) > max(2.0, expected_duration * 0.02):
        failures.append("captions do not reach the end of narration")

    image_hashes = [
        hashlib.sha256(path.read_bytes()).hexdigest() for path in image_paths
    ]
    distinct_images = len(set(image_hashes))
    if len(image_hashes) >= 4 and distinct_images / len(image_hashes) < 0.4:
        warnings.append("many timed scenes reuse the same image")

    report = {
        "passed": not failures,
        "duration_seconds": round(duration, 3),
        "expected_duration_seconds": round(expected_duration, 3),
        "resolution": (
            f"{video_stream.get('width')}x{video_stream.get('height')}"
            if video_stream else "missing"
        ),
        "mean_volume_db": mean_volume,
        "long_black_sections": black_durations,
        "caption_end_seconds": round(caption_end, 3),
        "timed_scenes": len(image_paths),
        "distinct_images": distinct_images,
        "warnings": warnings,
        "failures": failures,
    }
    if failures:
        raise RuntimeError("Finished-video quality check failed: " + "; ".join(failures))
    return report


def render_video(
    image_paths: list[Path],
    scene_durations: list[float],
    audio_path: Path,
    script_text: str,
    output_path: Path,
    scene_texts: list[str],
    title: str = "",
    scene_directions: list[dict] | None = None,
    sound_cues: list[dict] | None = None,
    ambience: dict | None = None,
    music: dict | None = None,
) -> None:
    """Render crossfaded images, captions, narration, and sparse effects."""
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
    temporary_output_path = output_path.with_suffix(".rendering.mp4")

    def render_attempt(use_transitions: bool) -> None:
        video_filter, input_durations = build_video_filter(
            scene_durations,
            caption_path.name,
            scene_texts,
            title_path.name if title_path else None,
            scene_directions,
            use_transitions,
        )
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
        command.extend(["-i", str(audio_path)])
        ambience_input_index = None
        if ambience:
            ambience_input_index = audio_input_index + 1
            command.extend(["-stream_loop", "-1", "-i", str(ambience["path"])])
        music_input_index = None
        if music:
            music_input_index = audio_input_index + 1 + (1 if ambience else 0)
            command.extend(["-stream_loop", "-1", "-i", str(music["path"])])
        cue_input_start = (
            audio_input_index + 1 + (1 if ambience else 0) + (1 if music else 0)
        )
        for cue in sound_cues or []:
            command.extend(["-i", str(cue["path"])])

        audio_filters = [
            f"[{audio_input_index}:a]highpass=f=65,lowpass=f=13500,"
            "loudnorm=I=-16:TP=-1.5:LRA=7[narration]"
        ]
        sound_labels = []
        if ambience_input_index is not None:
            total_duration = sum(scene_durations)
            fade_out_start = max(0.0, total_duration - 1.5)
            audio_filters.append(
                f"[{ambience_input_index}:a]atrim=duration={total_duration:.3f},"
                "asetpts=N/SR/TB,"
                f"volume={ambience['volume']:.3f},"
                "afade=t=in:st=0:d=1.0,"
                f"afade=t=out:st={fade_out_start:.3f}:d=1.5[ambience]"
            )
            sound_labels.append("[ambience]")
        if music_input_index is not None:
            total_duration = sum(scene_durations)
            fade_out_start = max(0.0, total_duration - 2.5)
            audio_filters.append(
                f"[{music_input_index}:a]atrim=duration={total_duration:.3f},"
                "asetpts=N/SR/TB,highpass=f=90,lowpass=f=9000,"
                f"volume={music['volume']:.3f},"
                "afade=t=in:st=0:d=2.0,"
                f"afade=t=out:st={fade_out_start:.3f}:d=2.5[music]"
            )
            sound_labels.append("[music]")
        for index, cue in enumerate(sound_cues or []):
            input_index = cue_input_start + index
            delay_ms = max(0, round(cue["start"] * 1_000))
            fade_out_start = max(0.0, cue["duration"] - 0.25)
            label = f"effect{index}"
            audio_filters.append(
                f"[{input_index}:a]volume={cue['volume']:.3f},"
                "afade=t=in:st=0:d=0.12,"
                f"afade=t=out:st={fade_out_start:.3f}:d=0.25,"
                f"adelay={delay_ms}:all=1[{label}]"
            )
            sound_labels.append(f"[{label}]")

        if sound_labels:
            audio_filters.append(
                f"[narration]{''.join(sound_labels)}"
                f"amix=inputs={len(sound_labels) + 1}:duration=first:"
                "dropout_transition=0:normalize=0,alimiter=limit=0.95[audio]"
            )
        else:
            audio_filters.append("[narration]anull[audio]")

        command.extend(
            [
                "-filter_complex",
                ";".join([video_filter, *audio_filters]),
                "-map",
                "[video]",
                "-map",
                "[audio]",
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-profile:v",
                "main",
                "-level:v",
                "4.0",
                "-pix_fmt",
                "yuv420p",
                "-crf",
                "18",
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

    try:
        try:
            render_attempt(True)
        except subprocess.CalledProcessError:
            temporary_output_path.unlink(missing_ok=True)
            print(
                "Warning: cinematic transitions failed; retrying with "
                "compatible scene changes."
            )
            render_attempt(False)
        quality_report = validate_rendered_video(
            ffmpeg_path,
            temporary_output_path,
            sum(scene_durations),
            caption_path,
            image_paths,
        )
        temporary_output_path.replace(output_path)
        shutil.copy2(caption_path, output_path.with_suffix(".srt"))
        output_path.with_suffix(".quality.json").write_text(
            json.dumps(quality_report, indent=2) + "\n", encoding="utf-8"
        )
        print(
            "Quality check passed: "
            f"{quality_report['resolution']}, "
            f"{quality_report['distinct_images']}/{quality_report['timed_scenes']} "
            "distinct timed images."
        )
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

    thumbnail_source = next(
        (
            image_dir / f"thumbnail_source{suffix}"
            for suffix in (".jpg", ".jpeg", ".png")
            if (image_dir / f"thumbnail_source{suffix}").is_file()
        ),
        None,
    )

    text = script_path.read_text(encoding="utf-8")
    scenes = load_story_scenes(image_dir, text)
    image_paths = resolve_scene_images(image_dir, scenes)
    scene_texts = [str(scene.get("narration", "")) for scene in scenes]
    scene_directions = load_scene_directions(image_dir, len(scenes))
    curiosity_hook = load_thumbnail_hook(image_dir)

    print(f"Loading audio: {audio_path.name}")
    total_duration = audio_duration(audio_path)
    scene_durations = timed_scene_durations(
        scenes,
        text,
        total_duration,
        audio_path.with_suffix(".timings.json"),
    )
    sound_cues = load_sound_cues(script_path.stem, scenes, scene_durations)
    ambience = load_continuous_ambience(script_path.stem)
    music = load_background_music(script_path.stem)

    print(f"Audio duration: {total_duration / 60:.1f} minutes")
    print(f"Building {len(image_paths)} timed image clips...")
    if sound_cues:
        print(f"Mixing {len(sound_cues)} quiet story sound effects...")
    if ambience:
        print("Mixing continuous low-volume ambience...")
    if music:
        print("Mixing quiet instrumental background music...")

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
        scene_directions,
        sound_cues,
        ambience,
        music,
    )
    thumbnail_path = render_thumbnail(
        shutil.which("ffmpeg") or "ffmpeg",
        image_paths,
        thumbnail_source,
        args.title,
        script_path.stem,
        curiosity_hook,
    )

    print(f"\nDone. Saved video to {output_path}")
    print(f"Thumbnail saved to {thumbnail_path}")


if __name__ == "__main__":
    main()
