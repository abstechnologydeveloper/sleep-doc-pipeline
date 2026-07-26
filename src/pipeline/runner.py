#!/usr/bin/env python3
"""Run the complete narration-to-video pipeline with one command.

Usage:
    python run_pipeline.py
    python run_pipeline.py "A forgotten lighthouse keeper" 10
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

from project_paths import (
    AUDIO_DIR,
    IMAGES_DIR,
    PROJECT_ROOT,
    SCRIPTS_DIR,
    SOUNDS_DIR,
    THUMBNAILS_DIR,
    VIDEOS_DIR,
)

BASE_DIR = PROJECT_ROOT
PIPELINE_STEPS = (
    "generate_script.py",
    "generate_audio.py",
    "generate_images.py",
    "generate_sounds.py",
    "assemble_video.py",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a script, narration, images, and finished video."
    )
    parser.add_argument("topic", nargs="?", help="Story topic or niche")
    parser.add_argument(
        "minutes",
        nargs="?",
        type=float,
        help="Desired narration duration in minutes",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume the newest script instead of generating a new one",
    )
    parser.add_argument(
        "--resume-script",
        type=Path,
        help="Resume one specific saved script (used by admin job retries)",
    )
    parser.add_argument(
        "--title",
        default="",
        help="Optional post title used to create the video thumbnail",
    )
    args = parser.parse_args()
    if (args.resume or args.resume_script) and (
        args.topic is not None or args.minutes is not None
    ):
        parser.error("resume options cannot be combined with topic or minutes")
    if args.resume and args.resume_script:
        parser.error("--resume and --resume-script cannot be combined")
    if args.minutes is not None and args.minutes <= 0:
        parser.error("minutes must be greater than zero")
    return args


def prompt_for_topic(configured_topic: str | None) -> str:
    if configured_topic and configured_topic.strip():
        return configured_topic.strip()

    while True:
        try:
            topic = input("Enter the story topic or niche: ").strip()
            if topic:
                return topic
        except (EOFError, KeyboardInterrupt) as exc:
            raise SystemExit("\nPipeline cancelled.") from exc
        print("Topic cannot be empty.")


def prompt_for_minutes(configured_minutes: float | None) -> float:
    if configured_minutes is not None:
        return configured_minutes

    while True:
        try:
            response = input("Desired narration duration in minutes [1]: ").strip()
            minutes = float(response) if response else 1.0
            if minutes > 0:
                return minutes
        except ValueError:
            pass
        except (EOFError, KeyboardInterrupt) as exc:
            raise SystemExit("\nPipeline cancelled.") from exc
        print("Enter a number greater than zero, such as 1, 10, or 60.")


def validate_environment() -> None:
    """Fail before any paid request if required configuration is missing."""
    load_dotenv(dotenv_path=BASE_DIR / ".env", override=True)

    missing = []
    if not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")):
        missing.append("GEMINI_API_KEY")
    if not os.getenv("CLOUDFLARE_ACCOUNT_ID"):
        missing.append("CLOUDFLARE_ACCOUNT_ID")
    if not os.getenv("CLOUDFLARE_API_TOKEN"):
        missing.append("CLOUDFLARE_API_TOKEN")
    if not shutil.which("ffmpeg"):
        missing.append("ffmpeg executable")

    missing_steps = [
        filename for filename in PIPELINE_STEPS if not (BASE_DIR / filename).is_file()
    ]
    if missing_steps:
        missing.extend(missing_steps)

    if missing:
        formatted = "\n  - ".join(missing)
        raise SystemExit(f"Missing pipeline requirements:\n  - {formatted}")


def run_step(label: str, command: list[str]) -> None:
    print(f"\n{'=' * 60}\n{label}\n{'=' * 60}")
    try:
        subprocess.run(command, cwd=BASE_DIR, check=True)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(
            f"{label} failed with exit code {exc.returncode}. "
            "Fix the reported error, then rerun the pipeline."
        ) from exc


def snapshot_scripts() -> dict[Path, int]:
    SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    return {
        path: path.stat().st_mtime_ns
        for path in SCRIPTS_DIR.glob("*.txt")
        if path.is_file()
    }


def find_generated_script(previous: dict[Path, int]) -> Path:
    candidates = []
    for path in SCRIPTS_DIR.glob("*.txt"):
        if not path.is_file():
            continue
        previous_mtime = previous.get(path)
        if previous_mtime is None or path.stat().st_mtime_ns > previous_mtime:
            candidates.append(path)

    if not candidates:
        raise SystemExit("Script generation finished but no new script file was found.")
    return max(candidates, key=lambda path: path.stat().st_mtime_ns)


def find_latest_script() -> Path:
    candidates = [path for path in SCRIPTS_DIR.glob("*.txt") if path.is_file()]
    if not candidates:
        raise SystemExit("No saved scripts are available to resume.")
    return max(candidates, key=lambda path: path.stat().st_mtime_ns).resolve()


def resolve_resume_script(configured_path: Path) -> Path:
    script_path = configured_path.resolve()
    scripts_root = SCRIPTS_DIR.resolve()
    if scripts_root not in script_path.parents or script_path.suffix.lower() != ".txt":
        raise SystemExit("--resume-script must point to a .txt file inside scripts/.")
    if not script_path.is_file():
        raise SystemExit(f"Saved script not found: {script_path}")
    return script_path


def main() -> None:
    args = parse_args()
    validate_environment()

    python = sys.executable
    if args.resume_script:
        script_path = resolve_resume_script(args.resume_script)
        print(f"Resuming job script: {script_path.name}")
    elif args.resume:
        script_path = find_latest_script()
        print(f"Resuming newest script: {script_path.name}")
    else:
        topic = prompt_for_topic(args.topic)
        minutes = prompt_for_minutes(args.minutes)
        previous_scripts = snapshot_scripts()
        run_step(
            "1/5 Generating narration script",
            [
                python,
                str(BASE_DIR / "generate_script.py"),
                "--minutes",
                str(minutes),
                "--",
                topic,
            ],
        )
        script_path = find_generated_script(previous_scripts).resolve()

    audio_path = AUDIO_DIR / f"{script_path.stem}.wav"
    timing_path = audio_path.with_suffix(".timings.json")
    if audio_path.exists() and timing_path.exists():
        print(f"Skipping existing audio: {audio_path.name}")
    else:
        if audio_path.exists():
            print("Regenerating audio because caption timing data is missing.")
        run_step(
            "2/5 Generating narration audio",
            [python, str(BASE_DIR / "generate_audio.py"), str(script_path)],
        )
    run_step(
        "3/5 Generating Cloudflare scene images",
        [
            python,
            str(BASE_DIR / "generate_images.py"),
            str(script_path),
            "--title",
            args.title,
        ],
    )
    run_step(
        "4/5 Generating optional story sound effects",
        [python, str(BASE_DIR / "generate_sounds.py"), str(script_path)],
    )
    run_step(
        "5/5 Assembling captioned video",
        [
            python,
            str(BASE_DIR / "assemble_video.py"),
            str(script_path),
            "--title",
            args.title,
        ],
    )

    video_path = VIDEOS_DIR / f"{script_path.stem}.mp4"
    if not video_path.exists():
        raise SystemExit("Pipeline finished but the expected video was not created.")

    print("\nPipeline complete.")
    print(f"Script: {script_path}")
    print(f"Audio:  {audio_path}")
    print(f"Images: {IMAGES_DIR / script_path.stem}")
    if (SOUNDS_DIR / script_path.stem).is_dir():
        print(f"Sounds: {SOUNDS_DIR / script_path.stem}")
    print(f"Video:  {video_path}")
    print(f"Thumb:  {THUMBNAILS_DIR / f'{script_path.stem}.jpg'}")


if __name__ == "__main__":
    main()
