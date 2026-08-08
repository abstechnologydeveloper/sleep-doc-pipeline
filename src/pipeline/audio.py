#!/usr/bin/env python3
"""Turn a saved narration script into a single narrated audio file using
AI33.Pro text-to-speech.

Resumable: each chunk is saved to disk as soon as it's generated, so a dropped
connection or Ctrl+C only costs you the chunk in progress, not the whole run.
Just rerun the same command and it picks up where it left off.

Usage:
    python generate_audio.py
    python generate_audio.py scripts/20260724_your-topic.txt
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import time
import wave
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from dotenv import load_dotenv

from .ai33 import (
    download_url,
    submit_file_task,
    submit_multipart_task,
    wait_for_audio,
    wait_for_task,
)
from project_paths import AUDIO_DIR, PROJECT_ROOT, SCRIPTS_DIR


DEFAULT_VOICE = "edge_en-US-AvaNeural"
MAX_CHARS_PER_CHUNK = 1_200
SAMPLE_RATE = 24_000
CHANNELS = 1
SAMPLE_WIDTH = 2
MAX_RETRIES = 5
RETRY_BASE_DELAY = 5  # seconds, doubles each retry
DEFAULT_WORKERS = 2

ChunkJob = tuple[int, str, Path]


def split_into_chunks(text: str, max_chars: int = MAX_CHARS_PER_CHUNK) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    chunks: list[str] = []
    current = ""

    for sentence in sentences:
        if len(current) + len(sentence) + 1 <= max_chars:
            current = f"{current} {sentence}".strip()
        else:
            if current:
                chunks.append(current)
            current = sentence

    if current:
        chunks.append(current)

    return chunks


def generate_chunk_audio(
    api_key: str, text: str, voice: str, voice_direction: str
) -> bytes:
    delivery_speeds = {
        "neutral": 1.0, "masculine": 1.0, "feminine": 1.0,
        "youthful": 1.03, "mature": 0.98, "deep": 0.98,
        "warm": 0.99, "bright": 1.02,
    }
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            task_id = submit_multipart_task(
                api_key,
                "/v3/text-to-speech",
                {
                    "text": text,
                    "voice_id": voice,
                    "speed": delivery_speeds.get(voice_direction, 1.0),
                    "with_transcript": "true",
                    "file_name": "narration.mp3",
                },
            )
            return wait_for_audio(api_key, task_id)
        except RuntimeError as exc:
            last_error = exc
            if "output download failed" in str(exc).lower():
                break
        if attempt == MAX_RETRIES:
            break
        delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
        print(
            f"    narration response error on attempt {attempt}/{MAX_RETRIES} "
            f"({last_error}). Retrying in {delay}s..."
        )
        time.sleep(delay)

    raise RuntimeError(
        "AI33.Pro could not generate the narration. Check the API key, voice access, "
        "account balance, and request limits."
    ) from last_error


def convert_mp3_to_wave(mp3_data: bytes, output_path: Path) -> None:
    """Convert AI33.Pro audio to the WAV contract used by the video pipeline."""
    temporary_mp3 = output_path.with_suffix(".generating.mp3")
    temporary_wave = output_path.with_suffix(".generating.wav")
    temporary_mp3.write_bytes(mp3_data)
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error", "-i", str(temporary_mp3),
                "-af",
                (
                    "silenceremove=start_periods=1:start_duration=0.08:"
                    "start_threshold=-50dB,areverse,"
                    "silenceremove=start_periods=1:start_duration=0.08:"
                    "start_threshold=-50dB,areverse,apad=pad_dur=0.12"
                ),
                "-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE),
                "-c:a", "pcm_s16le", str(temporary_wave),
            ],
            check=True,
            capture_output=True,
        )
        temporary_wave.replace(output_path)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("FFmpeg could not prepare the AI33.Pro narration audio.") from exc
    finally:
        temporary_mp3.unlink(missing_ok=True)
        temporary_wave.unlink(missing_ok=True)


def narrate_chunk(
    api_key: str,
    chunk_number: int,
    total_chunks: int,
    text: str,
    output_path: Path,
    voice: str,
    voice_direction: str,
) -> int:
    """Generate and save one chunk, returning its one-based chunk number."""
    print(
        f"  Narrating chunk {chunk_number}/{total_chunks} "
        f"({len(text)} chars)..."
    )
    mp3 = generate_chunk_audio(api_key, text, voice, voice_direction)
    convert_mp3_to_wave(mp3, output_path)
    return chunk_number


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate natural narration from a saved script using AI33.Pro."
    )
    parser.add_argument(
        "script_path",
        nargs="?",
        help="Path to the .txt script file; omit to select one interactively",
    )
    parser.add_argument(
        "--voice",
        default=DEFAULT_VOICE,
        help=f"AI33.Pro narration voice ID (default: {DEFAULT_VOICE})",
    )
    parser.add_argument(
        "--voice-direction",
        default="neutral",
        choices=("neutral", "masculine", "feminine", "youthful", "mature", "deep", "warm", "bright"),
        help="Requested voice quality",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"Number of chunks to generate concurrently (default: {DEFAULT_WORKERS})",
    )
    args = parser.parse_args()

    if args.workers < 1:
        parser.error("--workers must be at least 1")

    return args


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


def load_api_key() -> str:
    env_path = PROJECT_ROOT / ".env"
    load_dotenv(dotenv_path=env_path, override=True)

    api_key = os.getenv("AI33_API_KEY", "").strip()
    if not api_key:
        raise SystemExit(
            "AI33_API_KEY is required for narration. Add it to .env before "
            "creating a voice preview."
        )
    return api_key


def collect_pending_chunks(chunks: list[str], chunk_dir: Path) -> list[ChunkJob]:
    pending_chunks: list[ChunkJob] = []
    for chunk_number, chunk in enumerate(chunks, start=1):
        chunk_path = chunk_dir / f"chunk_{chunk_number:03d}.wav"
        if chunk_path.exists():
            print(
                f"  Chunk {chunk_number}/{len(chunks)} already done, skipping."
            )
        else:
            pending_chunks.append((chunk_number, chunk, chunk_path))
    return pending_chunks


def generate_pending_chunks(
    api_key: str,
    pending_chunks: list[ChunkJob],
    total_chunks: int,
    requested_workers: int,
    voice: str,
    voice_direction: str,
) -> None:
    if not pending_chunks:
        return

    worker_count = min(requested_workers, len(pending_chunks))
    print(f"Generating missing chunks with {worker_count} concurrent workers.")
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(
                narrate_chunk,
                api_key,
                chunk_number,
                total_chunks,
                chunk,
                chunk_path,
                voice,
                voice_direction,
            ): chunk_number
            for chunk_number, chunk, chunk_path in pending_chunks
        }
        for future in as_completed(futures):
            chunk_number = future.result()
            print(f"  Chunk {chunk_number}/{total_chunks} saved.")


def stitch_chunks(chunk_dir: Path, chunk_count: int, output_path: Path) -> int:
    """Join chunk WAV files in order and return the total frame count."""
    temporary_output_path = output_path.with_suffix(".wav.tmp")
    total_frames = 0
    with wave.open(str(temporary_output_path), "wb") as output_wf:
        output_wf.setnchannels(CHANNELS)
        output_wf.setsampwidth(SAMPLE_WIDTH)
        output_wf.setframerate(SAMPLE_RATE)
        for chunk_number in range(1, chunk_count + 1):
            chunk_path = chunk_dir / f"chunk_{chunk_number:03d}.wav"
            with wave.open(str(chunk_path), "rb") as chunk_wf:
                frame_count = chunk_wf.getnframes()
                output_wf.writeframesraw(chunk_wf.readframes(frame_count))
                total_frames += frame_count
    temporary_output_path.replace(output_path)
    return total_frames


def write_timing_file(
    chunks: list[str], chunk_dir: Path, output_path: Path, total_frames: int
) -> Path:
    """Persist actual TTS chunk boundaries for caption synchronization."""
    segments = []
    elapsed_frames = 0
    for chunk_number, text in enumerate(chunks, start=1):
        chunk_path = chunk_dir / f"chunk_{chunk_number:03d}.wav"
        with wave.open(str(chunk_path), "rb") as chunk_wf:
            chunk_frames = chunk_wf.getnframes()
        start = elapsed_frames / SAMPLE_RATE
        elapsed_frames += chunk_frames
        segments.append(
            {
                "text": text,
                "start": start,
                "end": elapsed_frames / SAMPLE_RATE,
            }
        )

    timing_path = output_path.with_suffix(".timings.json")
    temporary_path = timing_path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(
            {
                "version": 1,
                "audio_duration": total_frames / SAMPLE_RATE,
                "segments": segments,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(timing_path)
    return timing_path


def write_speech_transcript(api_key: str, audio_path: Path) -> Path | None:
    """Save AI33 speech-measured captions without making narration failure-prone."""
    transcript_path = audio_path.with_suffix(".transcript.srt")
    try:
        task_id = submit_file_task(
            api_key,
            "/v1/task/speech-to-text",
            audio_path,
            {"tag_audio_events": "false"},
        )
        task = wait_for_task(api_key, task_id)
        metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        srt_url = metadata.get("srt_url")
        if not isinstance(srt_url, str):
            raise RuntimeError("AI33.Pro completed transcription without an SRT file.")
        temporary_path = transcript_path.with_suffix(".srt.tmp")
        temporary_path.write_bytes(download_url(srt_url))
        temporary_path.replace(transcript_path)
        return transcript_path
    except (OSError, RuntimeError) as exc:
        print(f"Warning: precise speech captions were unavailable ({exc})")
        return None


def main() -> None:
    args = parse_args()
    script_path = select_script(args.script_path)
    if not script_path.exists():
        raise SystemExit(f"Script file not found: {script_path}")

    api_key = load_api_key()
    text = script_path.read_text(encoding="utf-8")
    chunks = split_into_chunks(text)
    print(f"Script split into {len(chunks)} chunks for narration.")

    # Each script gets its own subfolder of chunk files, so reruns can resume.
    safe_voice = re.sub(r"[^A-Za-z0-9_-]+", "-", args.voice)[:40]
    safe_direction = re.sub(r"[^A-Za-z0-9_-]+", "-", args.voice_direction)[:20]
    chunk_dir = AUDIO_DIR / (
        f"{script_path.stem}_ai33_v2_{safe_voice}_{safe_direction}_chunks"
    )
    chunk_dir.mkdir(parents=True, exist_ok=True)

    pending_chunks = collect_pending_chunks(chunks, chunk_dir)
    print(f"AI33.Pro narration voice: {args.voice}")
    print(f"Voice direction: {args.voice_direction}")
    generate_pending_chunks(
        api_key, pending_chunks, len(chunks), args.workers,
        args.voice, args.voice_direction,
    )

    # Stitch all chunk files together into the final audio file.
    print("\nAll chunks ready. Stitching into final audio file...")
    output_dir = AUDIO_DIR
    output_path = output_dir / f"{script_path.stem}.wav"
    total_frames = stitch_chunks(chunk_dir, len(chunks), output_path)
    timing_path = write_timing_file(
        chunks, chunk_dir, output_path, total_frames
    )
    transcript_path = write_speech_transcript(api_key, output_path)

    duration_seconds = total_frames / SAMPLE_RATE
    print(f"\nSaved narrated audio to {output_path}")
    print(f"Saved caption timing data to {timing_path}")
    if transcript_path:
        print(f"Saved speech-measured captions to {transcript_path}")
    print(f"Approximate duration: {duration_seconds / 60:.1f} minutes")

    try:
        shutil.rmtree(chunk_dir)
        print(f"Removed temporary chunk folder: {chunk_dir}")
    except OSError as exc:
        print(f"Warning: could not remove chunk folder {chunk_dir}: {exc}")


if __name__ == "__main__":
    main()
