#!/usr/bin/env python3
"""Generate optional, sparse story sound effects with ElevenLabs."""

import argparse
import json
import os
from pathlib import Path
from urllib import error, request

from dotenv import load_dotenv

from project_paths import IMAGES_DIR, PROJECT_ROOT, SCRIPTS_DIR, SOUNDS_DIR


API_URL = "https://api.elevenlabs.io/v1/sound-generation"
MODEL_ID = "eleven_text_to_sound_v2"
MANIFEST_FILENAME = "sound_manifest.json"
MAX_SOUND_CUES = 30


def select_script(script_argument: str | None) -> Path:
    if script_argument:
        return Path(script_argument)
    scripts = sorted(SCRIPTS_DIR.glob("*.txt"), reverse=True)
    if not scripts:
        raise SystemExit(f"No .txt scripts found in {SCRIPTS_DIR}")
    print("Available narration scripts:")
    for number, path in enumerate(scripts, start=1):
        print(f"  {number}. {path.name}")
    while True:
        try:
            selected = int(input("Select a script number: ").strip()) - 1
            if 0 <= selected < len(scripts):
                return scripts[selected]
        except ValueError:
            pass
        except (EOFError, KeyboardInterrupt) as exc:
            raise SystemExit("\nScript selection cancelled.") from exc
        print(f"Enter a number from 1 to {len(scripts)}.")


def normalized_cues(payload: object, scene_count: int) -> list[dict]:
    if not isinstance(payload, list):
        return []
    cues = []
    for cue in payload:
        if not isinstance(cue, dict):
            continue
        try:
            scene_index = int(cue["scene_index"])
            prompt = str(cue["prompt"]).strip()
            position = min(1.0, max(0.0, float(cue.get("position", 0.5))))
            duration = min(4.0, max(0.5, float(cue.get("duration_seconds", 2.0))))
            volume = min(0.16, max(0.04, float(cue.get("volume", 0.10))))
        except (KeyError, TypeError, ValueError):
            continue
        if not 1 <= scene_index <= scene_count or not prompt:
            continue
        cues.append(
            {
                "scene_index": scene_index,
                "position": position,
                "prompt": prompt[:500],
                "duration_seconds": duration,
                "volume": volume,
                "kind": str(cue.get("kind", "action"))[:30],
            }
        )
    maximum = min(MAX_SOUND_CUES, max(1, (scene_count + 1) // 2))
    return cues[:maximum]


def generate_sound(api_key: str, cue: dict) -> bytes:
    body = json.dumps(
        {
            "text": cue["prompt"],
            "duration_seconds": cue["duration_seconds"],
            "prompt_influence": 0.35,
            "loop": False,
            "model_id": MODEL_ID,
        }
    ).encode("utf-8")
    api_request = request.Request(
        API_URL,
        data=body,
        method="POST",
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
            "User-Agent": "sleep-doc-pipeline/1.0",
        },
    )
    try:
        with request.urlopen(api_request, timeout=180) as response:
            audio = response.read()
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(
            f"ElevenLabs rejected the sound request ({exc.code}): {detail}"
        ) from exc
    except (error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"ElevenLabs sound request failed: {exc}") from exc
    if not audio:
        raise RuntimeError("ElevenLabs returned an empty sound effect.")
    return audio


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate optional story sound effects.")
    parser.add_argument("script_path", nargs="?")
    args = parser.parse_args()
    script_path = select_script(args.script_path)
    if not script_path.is_file():
        raise SystemExit(f"Script file not found: {script_path}")

    load_dotenv(PROJECT_ROOT / ".env", override=True)
    api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        print("Sound design skipped: ELEVENLABS_API_KEY is not configured.")
        return

    plan_path = IMAGES_DIR / script_path.stem / "scene_plan.json"
    if not plan_path.is_file():
        print("Sound design skipped: the scene plan has no sound cues.")
        return
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Could not read scene plan: {exc}") from exc

    scene_count = len(plan.get("scene_prompts", []))
    cues = normalized_cues(plan.get("sound_cues"), scene_count)
    if not cues:
        print("Sound design skipped: no useful sound moments were selected.")
        return

    output_dir = SOUNDS_DIR / script_path.stem
    output_dir.mkdir(parents=True, exist_ok=True)
    completed = []
    for index, cue in enumerate(cues, start=1):
        filename = f"cue_{index:03d}.mp3"
        output_path = output_dir / filename
        if output_path.is_file() and output_path.stat().st_size > 0:
            print(f"  Sound {index}/{len(cues)} already done, skipping.")
        else:
            print(f"  Generating sound {index}/{len(cues)}: {cue['prompt'][:70]}...")
            temporary_path = output_path.with_suffix(".generating.mp3")
            try:
                temporary_path.write_bytes(generate_sound(api_key, cue))
                temporary_path.replace(output_path)
            except (OSError, RuntimeError) as exc:
                temporary_path.unlink(missing_ok=True)
                print(f"  Warning: sound {index} was skipped ({exc})")
                continue
        completed.append({**cue, "filename": filename})

    (output_dir / MANIFEST_FILENAME).write_text(
        json.dumps({"cues": completed}, indent=2), encoding="utf-8"
    )
    print(f"Sound effects saved to {output_dir}")


if __name__ == "__main__":
    main()
