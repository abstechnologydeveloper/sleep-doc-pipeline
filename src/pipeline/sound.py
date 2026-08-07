#!/usr/bin/env python3
"""Generate optional, sparse story sound effects with ElevenLabs."""

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib import error, request

from dotenv import load_dotenv

from project_paths import IMAGES_DIR, PROJECT_ROOT, SCRIPTS_DIR, SOUNDS_DIR


API_URL = "https://api.elevenlabs.io/v1/sound-generation"
MODEL_ID = "eleven_text_to_sound_v2"
MANIFEST_FILENAME = "sound_manifest.json"
MAX_SOUND_CUES = 30
AMBIENCE_FILENAME = "ambience.mp3"
AMBIENCE_SECONDS = 12.0
AMBIENCE_RULES = (
    (("rain", "storm", "drizzle"), "steady gentle rain outside, seamless calm ambience, no thunder, no music, no voices"),
    (("train", "railway", "sleeper car"), "soft steady sleeper train rhythm on rails, seamless calm ambience, no horn, no voices, no music"),
    (("fireplace", "hearth", "wood stove"), "quiet fireplace crackle in a warm room, seamless calm ambience, no sharp pops, no voices, no music"),
    (("ocean", "seaside", "sea", "coast"), "slow gentle ocean waves at night, seamless calm ambience, no birds, no voices, no music"),
    (("forest", "woods", "stream", "waterfall"), "soft forest night with light breeze and distant water, seamless calm ambience, no animal calls, no voices, no music"),
)


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


def normalized_cues(payload: object, scene_ids: list[str]) -> list[dict]:
    if not isinstance(payload, list):
        return []
    cues = []
    for cue in payload:
        if not isinstance(cue, dict):
            continue
        try:
            if cue.get("scene_id"):
                scene_id = str(cue["scene_id"])
                scene_index = scene_ids.index(scene_id) + 1
            else:
                scene_index = int(cue["scene_index"])
                scene_id = scene_ids[scene_index - 1]
            prompt = str(cue["prompt"]).strip()
            position = min(1.0, max(0.0, float(cue.get("position", 0.5))))
            duration = min(4.0, max(0.5, float(cue.get("duration_seconds", 2.0))))
            volume = min(0.16, max(0.04, float(cue.get("volume", 0.10))))
            kind = str(cue.get("kind", "action"))[:30].lower()
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if not 1 <= scene_index <= len(scene_ids) or not prompt or kind == "transition":
            continue
        cues.append(
            {
                "scene_id": scene_id,
                "scene_index": scene_index,
                "position": position,
                "prompt": prompt[:500],
                "duration_seconds": duration,
                "volume": volume,
                "kind": kind,
            }
        )
    maximum = min(MAX_SOUND_CUES, max(1, (len(scene_ids) + 1) // 2))
    return cues[:maximum]


def select_ambience(
    text: str, project_profile: object, planned_ambience: object = None
) -> dict | None:
    """Choose one unobtrusive loop only for calm, sleep-oriented productions."""
    profile = json.dumps(project_profile, ensure_ascii=False).lower()
    if not any(
        word in profile
        for word in ("sleep", "bedtime", "cozy", "relaxing", "soothing", "restful")
    ):
        return None
    if isinstance(planned_ambience, dict):
        prompt = str(planned_ambience.get("prompt", "")).strip()
        try:
            volume = min(0.05, max(0.015, float(planned_ambience.get("volume", 0.03))))
        except (TypeError, ValueError):
            volume = 0.03
        if prompt:
            return {
                "filename": AMBIENCE_FILENAME,
                "prompt": prompt[:500],
                "duration_seconds": AMBIENCE_SECONDS,
                "volume": volume,
                "kind": "continuous_ambience",
            }
    lowered = text.lower()
    ranked = [
        (sum(lowered.count(keyword) for keyword in keywords), prompt)
        for keywords, prompt in AMBIENCE_RULES
    ]
    score, prompt = max(ranked, key=lambda item: item[0])
    if score == 0:
        return None
    return {
        "filename": AMBIENCE_FILENAME,
        "prompt": prompt,
        "duration_seconds": AMBIENCE_SECONDS,
        "volume": 0.035,
        "kind": "continuous_ambience",
    }


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
    if not api_key.startswith("sk_"):
        print("Sound design skipped: ELEVENLABS_API_KEY has an unsupported format.")
        return

    plan_path = IMAGES_DIR / script_path.stem / "scene_plan.json"
    if not plan_path.is_file():
        print("Sound design skipped: the scene plan has no sound cues.")
        return
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Could not read scene plan: {exc}") from exc

    if plan.get("version") in {2, 3, 4} and isinstance(plan.get("scenes"), list):
        scene_ids = [str(scene.get("id", "")) for scene in plan["scenes"]]
    else:
        scene_ids = [
            f"scene_{index:03d}"
            for index in range(1, len(plan.get("scene_prompts", [])) + 1)
        ]
    cues = normalized_cues(plan.get("sound_cues"), scene_ids)
    ambience = select_ambience(
        script_path.read_text(encoding="utf-8"),
        plan.get("project_profile", {}),
        plan.get("continuous_ambience"),
    )
    if not cues and not ambience:
        print("Sound design skipped: no useful sound moments or ambience were selected.")
        return

    output_dir = SOUNDS_DIR / script_path.stem
    output_dir.mkdir(parents=True, exist_ok=True)
    completed = []
    pending = []
    for index, cue in enumerate(cues, start=1):
        filename = f"cue_{index:03d}.mp3"
        output_path = output_dir / filename
        if output_path.is_file() and output_path.stat().st_size > 0:
            print(f"  Sound {index}/{len(cues)} already done, skipping.")
            completed.append((index, {**cue, "filename": filename}))
        else:
            pending.append((index, cue, filename, output_path))

    def generate_pending_sound(item: tuple) -> tuple[int, dict] | None:
        index, cue, filename, output_path = item
        print(f"  Generating sound {index}/{len(cues)}: {cue['prompt'][:70]}...")
        temporary_path = output_path.with_suffix(".generating.mp3")
        try:
            temporary_path.write_bytes(generate_sound(api_key, cue))
            temporary_path.replace(output_path)
            return index, {**cue, "filename": filename}
        except (OSError, RuntimeError) as exc:
            temporary_path.unlink(missing_ok=True)
            print(f"  Warning: sound {index} was skipped ({exc})")
            return None

    try:
        requested_workers = int(os.getenv("SOUND_GENERATION_WORKERS", "3"))
    except ValueError:
        requested_workers = 3
    worker_count = min(max(1, requested_workers), 4, len(pending))
    if pending:
        print(
            f"Generating {len(pending)} sound effects with "
            f"{worker_count} concurrent workers."
        )
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [executor.submit(generate_pending_sound, item) for item in pending]
            for future in as_completed(futures):
                result = future.result()
                if result:
                    completed.append(result)

    completed_cues = [item for _, item in sorted(completed, key=lambda row: row[0])]

    completed_ambience = None
    if ambience:
        ambience_path = output_dir / AMBIENCE_FILENAME
        if ambience_path.is_file() and ambience_path.stat().st_size > 0:
            print("  Continuous ambience already done, skipping.")
            completed_ambience = ambience
        else:
            print(f"  Generating continuous ambience: {ambience['prompt'][:70]}...")
            temporary_path = ambience_path.with_suffix(".generating.mp3")
            try:
                temporary_path.write_bytes(generate_sound(api_key, ambience))
                temporary_path.replace(ambience_path)
                completed_ambience = ambience
            except (OSError, RuntimeError) as exc:
                temporary_path.unlink(missing_ok=True)
                print(f"  Warning: continuous ambience was skipped ({exc})")

    (output_dir / MANIFEST_FILENAME).write_text(
        json.dumps(
            {"cues": completed_cues, "ambience": completed_ambience}, indent=2
        ),
        encoding="utf-8",
    )
    print(f"Sound effects saved to {output_dir}")


if __name__ == "__main__":
    main()
