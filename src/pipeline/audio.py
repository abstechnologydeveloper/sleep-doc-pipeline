#!/usr/bin/env python3
"""Turn a saved narration script into a single narrated audio file using
Google AI Studio's Gemini TTS (free tier).

Resumable: each chunk is saved to disk as soon as it's generated, so a dropped
connection or Ctrl+C only costs you the chunk in progress, not the whole run.
Just rerun the same command and it picks up where it left off.

Usage:
    python generate_audio.py
    python generate_audio.py scripts/20260724_your-topic.txt
"""

import argparse
import io
import os
import re
import shutil
import time
import wave
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.genai import errors as genai_errors

from project_paths import AUDIO_DIR, PROJECT_ROOT, SCRIPTS_DIR


MODEL = "gemini-2.5-flash-preview-tts"
VOICE = "Kore"  # calm, steady default voice - see AI Studio for other options
MAX_CHARS_PER_CHUNK = 4_000
SAMPLE_RATE = 24_000
CHANNELS = 1
SAMPLE_WIDTH = 2
MAX_RETRIES = 5
RETRY_BASE_DELAY = 5  # seconds, doubles each retry
DEFAULT_WORKERS = 3

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


def generate_chunk_audio(client: genai.Client, text: str) -> bytes:
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=(
                    "Synthesize clear, audible speech from the transcript below. "
                    "Read every word calmly and exactly as written.\n\n"
                    f"TRANSCRIPT:\n{text}"
                ),
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                voice_name=VOICE
                            )
                        )
                    ),
                ),
            )
            return extract_pcm_audio(response)
        except (
            genai_errors.ServerError,
            genai_errors.ClientError,
            ConnectionError,
            TimeoutError,
        ) as exc:
            last_error = exc
            delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
            print(
                f"    network/API error on attempt {attempt}/{MAX_RETRIES} "
                f"({exc}). Retrying in {delay}s..."
            )
            time.sleep(delay)

    raise RuntimeError(
        f"Failed to generate audio after {MAX_RETRIES} attempts."
    ) from last_error


def extract_pcm_audio(response: object) -> bytes:
    """Extract raw PCM samples, decoding a WAV response when necessary."""
    try:
        inline_data = response.candidates[0].content.parts[0].inline_data
        audio_data = inline_data.data
        mime_type = (inline_data.mime_type or "").lower()
    except (AttributeError, IndexError, TypeError) as exc:
        raise RuntimeError("Gemini returned no usable audio data.") from exc

    if not audio_data:
        raise RuntimeError("Gemini returned an empty audio response.")

    if "wav" in mime_type:
        with wave.open(io.BytesIO(audio_data), "rb") as input_wf:
            if (
                input_wf.getnchannels() != CHANNELS
                or input_wf.getsampwidth() != SAMPLE_WIDTH
                or input_wf.getframerate() != SAMPLE_RATE
            ):
                raise RuntimeError(
                    "Gemini returned WAV audio with an unexpected format."
                )
            return input_wf.readframes(input_wf.getnframes())

    if not mime_type or "pcm" in mime_type or "l16" in mime_type:
        return audio_data

    raise RuntimeError(f"Unsupported Gemini audio format: {mime_type}")


def write_wave(path: Path, pcm_data: bytes) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm_data)


def narrate_chunk(
    client: genai.Client,
    chunk_number: int,
    total_chunks: int,
    text: str,
    output_path: Path,
) -> int:
    """Generate and save one chunk, returning its one-based chunk number."""
    print(
        f"  Narrating chunk {chunk_number}/{total_chunks} "
        f"({len(text)} chars)..."
    )
    pcm = generate_chunk_audio(client, text)
    temporary_path = output_path.with_suffix(".wav.tmp")
    write_wave(temporary_path, pcm)
    temporary_path.replace(output_path)
    return chunk_number


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate narrated audio from a saved script using Gemini TTS."
    )
    parser.add_argument(
        "script_path",
        nargs="?",
        help="Path to the .txt script file; omit to select one interactively",
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

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise SystemExit(
            "GEMINI_API_KEY was not found. Get a free key from Google AI Studio "
            "(aistudio.google.com) and add it to .env as GEMINI_API_KEY=your_key_here"
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
    client: genai.Client,
    pending_chunks: list[ChunkJob],
    total_chunks: int,
    requested_workers: int,
) -> None:
    if not pending_chunks:
        return

    worker_count = min(requested_workers, len(pending_chunks))
    print(f"Generating missing chunks with {worker_count} concurrent workers.")
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(
                narrate_chunk,
                client,
                chunk_number,
                total_chunks,
                chunk,
                chunk_path,
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
    chunk_dir = AUDIO_DIR / f"{script_path.stem}_chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)

    client = genai.Client(api_key=api_key)
    pending_chunks = collect_pending_chunks(chunks, chunk_dir)
    generate_pending_chunks(client, pending_chunks, len(chunks), args.workers)

    # Stitch all chunk files together into the final audio file.
    print("\nAll chunks ready. Stitching into final audio file...")
    output_dir = AUDIO_DIR
    output_path = output_dir / f"{script_path.stem}.wav"
    total_frames = stitch_chunks(chunk_dir, len(chunks), output_path)

    duration_seconds = total_frames / SAMPLE_RATE
    print(f"\nSaved narrated audio to {output_path}")
    print(f"Approximate duration: {duration_seconds / 60:.1f} minutes")

    try:
        shutil.rmtree(chunk_dir)
        print(f"Removed temporary chunk folder: {chunk_dir}")
    except OSError as exc:
        print(f"Warning: could not remove chunk folder {chunk_dir}: {exc}")


if __name__ == "__main__":
    main()
