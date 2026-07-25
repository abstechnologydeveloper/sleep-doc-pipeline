#!/usr/bin/env python3
"""Generate scene images with Cloudflare Workers AI's FLUX.1 Schnell API.

Resumable: each image saves to disk as soon as it's generated. Rerun the same
command to pick up where it left off after a crash or rate limit.

Usage:
    python generate_images.py
    python generate_images.py scripts/20260724_your-topic.txt
"""

import argparse
import base64
import binascii
import json
import os
import time
from pathlib import Path
from urllib import error, request

from dotenv import load_dotenv

from project_paths import IMAGES_DIR, PROJECT_ROOT, SCRIPTS_DIR


CLOUDFLARE_MODEL = "@cf/black-forest-labs/flux-1-schnell"
WORDS_PER_SCENE = 50  # roughly six images per two minutes at 150 words/minute
MAX_RETRIES = 5
RETRY_BASE_DELAY = 10  # seconds, doubles each retry

STYLE_SUFFIX = (
    ", premium cinematic sleep-story artwork, rich teal and warm gold color harmony, "
    "soft volumetric light, clear focal subject, layered foreground and background depth, "
    "calm low-stimulation atmosphere, cinematic wide shot composed for 16:9, "
    "no text, no watermark, high detail"
)
SAFETY_FALLBACK_PROMPT = (
    "A peaceful empty landscape beneath a starlit night sky, gentle moonlight, "
    "quiet trees and distant hills, calm atmospheric lighting, soft muted colors, "
    "cinematic wide shot, no people, no text, no watermark, high detail"
)


def split_into_scenes(text: str, words_per_scene: int = WORDS_PER_SCENE) -> list[str]:
    """Split the script into scene-sized text blocks for image prompting."""
    words = text.split()
    scenes = []
    for i in range(0, len(words), words_per_scene):
        scenes.append(" ".join(words[i:i + words_per_scene]))
    return scenes


def scene_to_prompt(scene_text: str) -> str:
    """Turn a chunk of narration into a short visual prompt.

    This is a simple heuristic (first ~40 words) rather than an extra AI call,
    to keep this step fast and free. Swap in an LLM call here later if you
    want more accurate scene descriptions.
    """
    words = scene_text.split()
    snippet = " ".join(words[:40])
    return f"{snippet}{STYLE_SUFFIX}"


def request_image(account_id: str, api_token: str, prompt: str) -> tuple[bytes, str]:
    api_url = (
        "https://api.cloudflare.com/client/v4/accounts/"
        f"{account_id}/ai/run/{CLOUDFLARE_MODEL}"
    )
    body = json.dumps(
        {
            "prompt": prompt,
            "steps": 4,
        }
    ).encode("utf-8")
    api_request = request.Request(
        api_url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "sleep-doc-pipeline/1.0",
        },
    )
    with request.urlopen(api_request, timeout=180) as response:
        result = json.loads(response.read().decode("utf-8"))

    encoded_image = (
        result.get("result", {}).get("image")
        if isinstance(result, dict) and isinstance(result.get("result"), dict)
        else None
    )
    if not encoded_image or not isinstance(encoded_image, str):
        raise RuntimeError("Cloudflare Workers AI returned no image data.")

    if encoded_image.startswith("data:"):
        encoded_image = encoded_image.partition(",")[2]
    try:
        image_data = base64.b64decode(encoded_image, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RuntimeError("Cloudflare returned invalid base64 image data.") from exc

    if image_data.startswith(b"\xff\xd8\xff"):
        return image_data, ".jpg"
    if image_data.startswith(b"\x89PNG\r\n\x1a\n"):
        return image_data, ".png"
    raise RuntimeError("Cloudflare returned an unsupported image format.")


def generate_image(account_id: str, api_token: str, prompt: str) -> tuple[bytes, str]:
    last_error = None
    active_prompt = prompt
    used_safety_fallback = False

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return request_image(account_id, api_token, active_prompt)
        except error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace").strip()
            if len(details) > 500:
                details = details[:500] + "..."
            if exc.code == 429 and (
                '"code":4006' in details or "daily free allocation" in details
            ):
                raise RuntimeError(
                    "Cloudflare Workers AI daily allocation is exhausted. "
                    "Retry after the daily reset or enable Workers Paid."
                ) from exc
            if exc.code == 400 and "NSFW content" in details and not used_safety_fallback:
                active_prompt = SAFETY_FALLBACK_PROMPT
                used_safety_fallback = True
                print("    Cloudflare rejected the scene prompt; retrying with a neutral visual.")
                continue
            if exc.code not in {408, 409, 429, 500, 502, 503, 504}:
                raise RuntimeError(
                    f"Cloudflare rejected the request ({exc.code}): {details}"
                ) from exc
            last_error = RuntimeError(f"Cloudflare error {exc.code}: {details}")
        except (error.URLError, TimeoutError) as exc:
            last_error = exc

        delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
        print(f"    error ({last_error}), retrying in {delay}s...")
        time.sleep(delay)

    raise RuntimeError(
        f"Failed to generate image after {MAX_RETRIES} attempts."
    ) from last_error


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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate scene images using Cloudflare Workers AI FLUX.1 Schnell."
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

    env_path = PROJECT_ROOT / ".env"
    load_dotenv(dotenv_path=env_path, override=True)

    account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID")
    api_token = os.getenv("CLOUDFLARE_API_TOKEN")
    if not account_id or not api_token:
        raise SystemExit(
            "Cloudflare credentials were not found. Add CLOUDFLARE_ACCOUNT_ID "
            "and CLOUDFLARE_API_TOKEN to .env."
        )

    text = script_path.read_text(encoding="utf-8")
    scenes = split_into_scenes(text)
    print(f"Script split into {len(scenes)} scenes for image generation.")

    image_dir = IMAGES_DIR / script_path.stem
    image_dir.mkdir(parents=True, exist_ok=True)

    for i, scene_text in enumerate(scenes, start=1):
        image_stem = f"scene_{i:03d}"
        existing_image = next(
            (
                image_dir / f"{image_stem}{suffix}"
                for suffix in (".jpg", ".jpeg", ".png")
                if (image_dir / f"{image_stem}{suffix}").exists()
            ),
            None,
        )
        if existing_image:
            print(f"  Scene {i}/{len(scenes)} already done, skipping.")
            continue

        prompt = scene_to_prompt(scene_text)
        print(f"  Generating scene {i}/{len(scenes)}: {prompt[:70]}...")

        image_data, suffix = generate_image(account_id, api_token, prompt)
        image_path = image_dir / f"{image_stem}{suffix}"
        image_path.write_bytes(image_data)

    print(f"\nAll images saved to {image_dir}")
    print(f"Total scenes: {len(scenes)}")


if __name__ == "__main__":
    main()
