#!/usr/bin/env python3
"""Generate art-directed scene images with Cloudflare Workers AI.

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
import re
import hashlib
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib import error, request

from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from project_paths import IMAGES_DIR, PROJECT_ROOT, SCRIPTS_DIR


CLOUDFLARE_MODEL = "@cf/leonardo/lucid-origin"
FAST_IMAGE_MODEL = "@cf/black-forest-labs/flux-1-schnell"
WORDS_PER_SCENE = 50  # roughly six images per two minutes at 150 words/minute
DEFAULT_MAX_STORY_IMAGES = 48
MAX_RETRIES = 5
RETRY_BASE_DELAY = 10  # seconds, doubles each retry

STYLE_SUFFIX = (
    ", polished professional storytelling image, coherent anatomy and perspective, "
    "clear focal subject, layered foreground and background depth, intentional lighting, "
    "native cinematic 16:9 composition, no text, no watermark, no logo, high detail"
)
SAFETY_FALLBACK_PROMPT = (
    "A peaceful empty landscape beneath a starlit night sky, gentle moonlight, "
    "quiet trees and distant hills, calm atmospheric lighting, soft muted colors, "
    "cinematic wide shot, no people, no text, no watermark, high detail"
)
SCENE_PLAN_FILENAME = "scene_plan.json"
IMAGE_REVIEW_FILENAME = "image_review.json"
THUMBNAIL_STEM = "thumbnail_source"
PLAN_VERSION = 4


class ImageBudgetExceeded(RuntimeError):
    """Raised before paid generation when required story visuals exceed the cap."""


def split_narrative_segments(text: str) -> list[dict]:
    """Create stable sentence anchors and exact word ranges for story planning."""
    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])(?:[\"'”’]*)\s+", text.strip())
        if sentence.strip()
    ]
    if not sentences and text.strip():
        sentences = [text.strip()]

    segments = []
    word_cursor = 0
    for index, sentence in enumerate(sentences, start=1):
        word_count = len(sentence.split())
        segments.append(
            {
                "id": index,
                "text": sentence,
                "start_word": word_cursor,
                "end_word": word_cursor + word_count,
            }
        )
        word_cursor += word_count
    return segments


def materialize_scenes(raw_scenes: object, segments: list[dict]) -> list[dict]:
    """Validate complete segment coverage and add deterministic scene metadata."""
    if not isinstance(raw_scenes, list) or not raw_scenes or not segments:
        raise ValueError("The visual plan contains no story scenes")

    scenes = []
    expected_start = 1
    for index, raw in enumerate(raw_scenes, start=1):
        if not isinstance(raw, dict):
            raise ValueError("Every story scene must be an object")
        try:
            start_segment = int(raw["start_segment"])
            end_segment = int(raw["end_segment"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Every scene needs valid segment boundaries") from exc
        if start_segment != expected_start or end_segment < start_segment:
            raise ValueError("Story scenes must cover narration in order without gaps")
        if end_segment > len(segments):
            raise ValueError("A story scene ends beyond the narration")

        covered = segments[start_segment - 1:end_segment]
        prompt = str(raw.get("prompt", "")).strip()
        if not prompt:
            raise ValueError("Every story scene needs an image prompt")
        direction = raw.get("direction")
        if not isinstance(direction, dict):
            direction = {}
        reuse_scene_id = raw.get("reuse_scene_id")
        if reuse_scene_id is not None:
            reuse_scene_id = str(reuse_scene_id).strip() or None
            if reuse_scene_id:
                match = re.fullmatch(r"(?:scene[_ -]?)?(\d+)", reuse_scene_id, re.I)
                if match:
                    reuse_scene_id = f"scene_{int(match.group(1)):03d}"

        scenes.append(
            {
                "id": f"scene_{index:03d}",
                "start_segment": start_segment,
                "end_segment": end_segment,
                "start_word": covered[0]["start_word"],
                "end_word": covered[-1]["end_word"],
                "word_count": covered[-1]["end_word"] - covered[0]["start_word"],
                "narration": " ".join(item["text"] for item in covered),
                "beat": str(raw.get("beat", "")).strip(),
                "action": str(raw.get("action", "")).strip(),
                "characters": raw.get("characters", []),
                "location": str(raw.get("location", "")).strip(),
                "importance": str(raw.get("importance", "mandatory")).strip(),
                "reuse_scene_id": reuse_scene_id,
                "prompt": f"{prompt[:1750]}{STYLE_SUFFIX}",
                "direction": {
                    "camera": direction.get("camera", "slow_push"),
                    "transition": direction.get("transition", "fade"),
                    "atmosphere": direction.get("atmosphere", "none"),
                },
            }
        )
        expected_start = end_segment + 1

    if expected_start != len(segments) + 1:
        raise ValueError("The final story scene does not reach the end of the narration")

    known_ids = {scene["id"] for scene in scenes}
    for scene in scenes:
        reused = scene["reuse_scene_id"]
        if reused and (reused not in known_ids or reused >= scene["id"]):
            raise ValueError("A reused visual must reference an earlier scene")
    return scenes


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


def fallback_visual_plan(scenes: list[str]) -> dict:
    return {
        "project_profile": {
            "audience": "general",
            "visual_medium": "cinematic digital illustration",
            "tone": "calm",
        },
        "visual_bible": "Consistent cinematic storytelling artwork",
        "scene_prompts": [scene_to_prompt(scene) for scene in scenes],
        "scene_directions": [
            {"camera": "slow_push", "transition": "fade", "atmosphere": "none"}
            for _scene in scenes
        ],
        "sound_cues": [],
        "thumbnail_hook": "LOOK CLOSER",
        "thumbnail_prompt": scene_to_prompt(scenes[0]),
    }


def valid_visual_plan(plan: object, scene_count: int) -> bool:
    return (
        isinstance(plan, dict)
        and isinstance(plan.get("scene_prompts"), list)
        and len(plan["scene_prompts"]) == scene_count
        and all(isinstance(prompt, str) and prompt.strip() for prompt in plan["scene_prompts"])
        and isinstance(plan.get("scene_directions"), list)
        and len(plan["scene_directions"]) == scene_count
        and all(isinstance(direction, dict) for direction in plan["scene_directions"])
        and "sound_cues" in plan
        and isinstance(plan.get("sound_cues"), list)
        and isinstance(plan.get("thumbnail_hook"), str)
        and bool(plan["thumbnail_hook"].strip())
        and isinstance(plan.get("thumbnail_prompt"), str)
        and bool(plan["thumbnail_prompt"].strip())
    )


def valid_dynamic_plan(plan: object, text: str) -> bool:
    if not isinstance(plan, dict) or plan.get("version") != PLAN_VERSION:
        return False
    if plan.get("script_hash") != hashlib.sha256(text.encode("utf-8")).hexdigest():
        return False
    scenes = plan.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        return False
    expected_word = 0
    for index, scene in enumerate(scenes, start=1):
        if not isinstance(scene, dict) or scene.get("id") != f"scene_{index:03d}":
            return False
        if scene.get("start_word") != expected_word:
            return False
        end_word = scene.get("end_word")
        if not isinstance(end_word, int) or end_word <= expected_word:
            return False
        if not str(scene.get("prompt", "")).strip():
            return False
        expected_word = end_word
    return (
        expected_word == len(text.split())
        and isinstance(plan.get("sound_cues"), list)
        and bool(str(plan.get("thumbnail_hook", "")).strip())
        and bool(str(plan.get("thumbnail_prompt", "")).strip())
    )


def cap_distinct_scenes(scenes: list[dict], max_images: int) -> int:
    """Keep all timed beats while capping how many paid images are generated."""
    distinct_count = 0
    last_distinct_id = None
    root_by_id: dict[str, str] = {}
    for scene in scenes:
        scene_id = scene["id"]
        reused = scene.get("reuse_scene_id")
        if reused:
            root = root_by_id.get(reused, reused)
            scene["reuse_scene_id"] = root
            root_by_id[scene_id] = root
            continue
        if distinct_count < max_images:
            distinct_count += 1
            last_distinct_id = scene_id
            root_by_id[scene_id] = scene_id
        elif last_distinct_id:
            scene["reuse_scene_id"] = last_distinct_id
            root_by_id[scene_id] = last_distinct_id
    return distinct_count


def create_dynamic_visual_plan(
    text: str,
    title: str,
    api_key: str,
    plan_path: Path,
    max_images: int,
    preferred_style: str = "cinematic",
    niche: str = "",
    audience: str = "",
) -> dict:
    """Plan visuals around narrative events rather than a words-per-image ratio."""
    if plan_path.is_file():
        try:
            saved_plan = json.loads(plan_path.read_text(encoding="utf-8"))
            if valid_dynamic_plan(saved_plan, text):
                cap_distinct_scenes(saved_plan["scenes"], max_images)
                plan_path.write_text(
                    json.dumps(saved_plan, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                print("Using saved dynamic story scene plan.")
                return saved_plan
            # Preserve resumability for projects created by the original fixed-ratio planner.
            legacy_scenes = split_into_scenes(text)
            if valid_visual_plan(saved_plan, len(legacy_scenes)):
                print("Using saved legacy scene plan.")
                return saved_plan
        except (OSError, json.JSONDecodeError):
            pass

    segments = split_narrative_segments(text)
    segment_list = "\n".join(
        f"{item['id']}. {item['text']}" for item in segments
    )
    prompt = f"""Act as the storyboard director for an original narrated YouTube story.
Divide the narration into visual scenes based only on meaningful story events, not a fixed
word count or images-per-minute ratio. Start a new visual when the action, location, time,
important character, emotion, clue, discovery, decision, or story direction changes. Keep a
continuous calm passage together when the same visual can honestly represent it. Every
numbered narration segment must belong to exactly one scene, in order, without gaps or
overlaps. Never split a numbered segment.
Give each important setup, clue, reveal, relationship turn, character decision, climax action,
payoff, and final emotional resolution an honest matching visual beat. Do not use a generic
atmosphere image when the narration describes a specific meaningful action.

Art-direct the opening for viewer attention without changing the narration. The first image
must show the exact person, animal, object, place, choice, or contradiction that creates the
opening question; never begin with an empty landscape or generic establishing shot when a
specific subject is available. Give the first roughly 30 seconds two to four distinct visual
beats when the numbered narration contains real changes to show. For calm fiction, show the
lead doing their role before the gentle surprise appears. For factual history, show the
documented pivotal moment, people, place, object, map context, or decision without inventing a
scene. After the hook is clear, let the visual pace settle naturally.

The hard paid-image limit is {max_images}. You must stay within it. Preserve every narrative
beat as a timed scene, but consolidate nearby events into stronger compositions and reuse the
closest suitable earlier visual with a different camera movement when a new paid image would
exceed the limit. Never report the budget as insufficient. A scene may reuse an earlier image
by setting reuse_scene_id to that earlier ID.

The creator's niche is {niche or 'general storytelling'}.
The target audience is {audience or 'infer it from the narration'}.
The creator's preferred visual direction is {preferred_style}. Honor it when it suits the
story and audience, while keeping age and safety appropriate. Use this context and choose
a fitting coherent visual medium: realistic cinema for adult
realistic stories, age-appropriate 2D or 3D animation for children, historical realism,
fantasy illustration, gentle gothic suspense, nature documentary, or another suitable style.
Never imitate a named artist, studio, franchise, or copyrighted character.

Make this video visually distinct from a generic AI story. Derive its cast, locations,
time period, weather, color palette, lighting, architecture, props, and camera language from
the exact title and narration. Do not automatically use blue night scenes, warm glowing
windows, magical glowing objects, mist, a forest, or the same centered portrait unless the
story clearly calls for them. Vary wide, medium, close, low, high, over-shoulder, and
environmental compositions according to the action. Give this story its own recognisable
visual identity and repeat that identity only where continuity requires it.

Choose a story-specific palette with named dominant, accent, shadow, and highlight colors.
Use that palette consistently, but let lighting change naturally with time, place, emotion,
and weather. Do not apply the same teal-and-orange, blue-night, or warm-window grade to every
video. Design locations around the story's culture, work, geography, species, and period.

Cast people for their actual role in this story. Vary age, facial structure, skin tone, body
shape, hair, clothing, occupation, and signs of lived experience. Do not default to the same
young, slim, conventionally attractive model in every role. Historical people must look
appropriate to their documented place, period, work, and social setting. Children must look
their stated age. Do not make unrelated characters resemble one another.

Direct each character's pose, gaze, hands or paws, body tension, and facial expression around
the exact action and emotion of that beat. For animated animal or object stories, combine
clear readable emotion and human-like work or travel with real species traits, scale,
movement, habitats, and abilities. Build a fresh world for the premise rather than reusing a
generic animal city. Impossible clouds, weather, objects, or places must follow simple visual
rules that remain consistent throughout the video.

For factual history, prefer evidence-led visuals: accurate clothing, tools, buildings,
landscape, maps without readable labels, routes, formations, documents, artifacts, scale
comparisons, and clear before-versus-after context. Use visual progression to make chronology
understandable without placing text or invented events inside the image.

Create continuity registries for every recurring character, location, and important prop.
Give every recurring character one immutable canonical_description containing their face,
age, body shape, skin tone, hair, clothing, and fixed colors. Copy that exact description
word-for-word into every scene prompt where the character appears; never shorten, paraphrase,
or replace it. Lock architecture, geography, weather, lighting direction, and palette in the
same way for recurring locations. Each prompt must describe visible action, expression,
setting, camera distance, composition, and lighting without changing locked details.
Generate native cinematic 16:9 compositions with important subjects in a central safe area,
no words, letters, logos, watermark, collage, frame, gore, or nudity.

Choose continuous, restrained camera movement so no image feels frozen. Use slow_push or
slow_pull for intimate or still moments, pan_left or pan_right for journeys and horizontal
action, and pan_up or pan_down for tall buildings, trees, mountains, skies, or reveals.
Match scene changes: use dissolve when the subject or location continues, smooth_left or
smooth_right when travel or action continues, and fade only for a real change in time or
place. The outgoing image must keep moving while the incoming image replaces it smoothly.
Select sparse sound cues only for visible or strongly implied events; use stable scene_id
values. Never create whooshes, swipes, impacts, or other sounds for visual transitions.

Mark four to six honest retention checkpoints across longer videos: opening promise, early
progress, midpoint change, late consequence, climax, and payoff. Each checkpoint must point
to a real scene, state the viewer question being advanced, and describe the visible change.
Short videos may use fewer checkpoints. Never invent a cliffhanger outside the narration.
For sleep, bedtime, cozy, or relaxing stories, also choose one continuous low-detail ambience
that belongs to the main location, such as the exact room tone, weather, transport rhythm,
water, forest, or distant city texture. It must loop cleanly with no speech, melody, sudden
event, loud animal call, or prominent foreground action. For other genres return null.

Make the final two visual beats deliver the opening promise. Show the decisive action or
supported historical answer, then one memorable result image that is visibly different from
the opening while preserving continuity. Never finish on a random landscape, repeated filler
image, or unresolved prop.

Design three separate high-performing YouTube thumbnail concepts, then score each from 1 to
10 for truthful curiosity, phone-size readability, emotional clarity, and title alignment.
Select the highest useful total rather than the most dramatic concept. Each concept must make one specific,
unanswered story question instantly visible while remaining completely honest to the video.
Use one large, emotionally readable person, place, or object on the right third; one unusual
but truthful visual detail that creates curiosity; strong warm-versus-cool color contrast;
and a simple background with clean darker space on the left for added text. The subject and
curiosity detail must still be clear at phone size. Do not create a collage, crowded scene,
tiny subject, generic landscape, fake danger, misleading reaction, arrow, circle, logo, or
words inside the generated image. For history, show a recognisable person, object, decision,
or disputed moment without inventing facts. For sleep stories, show a safe but irresistible
place, arrival, light, doorway, train, room, or discovery without horror.

For each candidate, write a thumbnail_hook of 2 to 4 simple words. It must complement rather than repeat the
video title, expose the curiosity gap without answering it, and avoid generic phrases such as
WHAT HAPPENS NEXT, YOU WON'T BELIEVE, MUST SEE, or THE TRUTH.

Title: {title or '(derive from the narration)'}
Numbered narration segments:
{segment_list}

Return JSON only with:
- budget_sufficient: boolean
- required_scene_count: integer
- project_profile: audience, genre, visual_medium, tone, palette, lighting
- continuity: characters, locations, props arrays of detailed objects with stable IDs;
  every recurring character must include an immutable canonical_description
- visual_bible: concise string
- scenes: ordered objects with start_segment, end_segment, beat, action, characters (IDs),
  location (ID or empty), importance (mandatory or continuity), reuse_scene_id (earlier
  scene_### or null), prompt, and direction containing camera (slow_push, slow_pull,
  pan_left, pan_right, pan_up, pan_down), transition (fade, dissolve, smooth_left,
  smooth_right), and
  atmosphere (none, stars, rain, snow, embers, fog, motes)
- sound_cues: sparse objects with scene_id, position (0 to 1), prompt, duration_seconds
  (0.5 to 4), volume (0.04 to 0.16), and kind
- retention_checkpoints: ordered objects with scene_id, role, viewer_question, and visual_change
- continuous_ambience: null or an object with prompt and volume (0.015 to 0.05)
- thumbnail_candidates: exactly 3 objects containing hook, prompt, curiosity_question,
  truthfulness_score, readability_score, emotion_score, and title_alignment_score (1 to 10)
- selected_thumbnail_index: zero-based index of the strongest truthful candidate
- thumbnail_hook: 2 to 4 simple curiosity words that complement the title
- thumbnail_prompt: detailed native 16:9 prompt
"""

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.45,
            ),
        )
        raw_plan = json.loads(response.text or "")
        scenes = materialize_scenes(raw_plan.get("scenes"), segments)
        distinct_scene_count = cap_distinct_scenes(scenes, max_images)
        thumbnail_candidates = []
        for candidate in raw_plan.get("thumbnail_candidates", [])[:3]:
            if not isinstance(candidate, dict):
                continue
            candidate_hook = " ".join(
                str(candidate.get("hook", "")).strip().strip('"\'').split()[:4]
            ).upper()
            candidate_prompt = str(candidate.get("prompt", "")).strip()
            if not candidate_hook or not candidate_prompt:
                continue
            scores = {}
            for score_name in (
                "truthfulness_score", "readability_score", "emotion_score",
                "title_alignment_score",
            ):
                try:
                    scores[score_name] = min(
                        10, max(1, int(candidate.get(score_name, 1)))
                    )
                except (TypeError, ValueError):
                    scores[score_name] = 1
            thumbnail_candidates.append(
                {
                    "hook": candidate_hook,
                    "prompt": candidate_prompt[:1750],
                    "curiosity_question": str(
                        candidate.get("curiosity_question", "")
                    ).strip()[:300],
                    **scores,
                    "score": sum(scores.values()),
                }
            )
        selected_thumbnail = (
            max(thumbnail_candidates, key=lambda candidate: candidate["score"])
            if len(thumbnail_candidates) == 3 else None
        )
        hook = " ".join(
            str(
                selected_thumbnail["hook"]
                if selected_thumbnail else raw_plan.get("thumbnail_hook", "")
            ).strip().strip('"\'').split()[:4]
        ).upper()
        thumbnail_prompt = (
            selected_thumbnail["prompt"]
            if selected_thumbnail else str(raw_plan.get("thumbnail_prompt", "")).strip()
        )
        plan = {
            "version": PLAN_VERSION,
            "script_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "project_profile": raw_plan.get("project_profile", {}),
            "continuity": raw_plan.get("continuity", {}),
            "visual_bible": str(raw_plan.get("visual_bible", "")).strip(),
            "scenes": scenes,
            "sound_cues": raw_plan.get("sound_cues", []),
            "retention_checkpoints": raw_plan.get("retention_checkpoints", []),
            "continuous_ambience": raw_plan.get("continuous_ambience"),
            "thumbnail_candidates": thumbnail_candidates,
            "selected_thumbnail_index": (
                thumbnail_candidates.index(selected_thumbnail)
                if selected_thumbnail else None
            ),
            "thumbnail_hook": hook or "LOOK CLOSER",
            "thumbnail_prompt": (
                f"{thumbnail_prompt[:1750]}{STYLE_SUFFIX}"
            ),
            "planning_fallback": False,
        }
        if not valid_dynamic_plan(plan, text):
            raise ValueError("Gemini returned an incomplete dynamic storyboard")
        plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
        print(f"Saved dynamic story scene plan with {len(scenes)} scenes.")
        return plan
    except ImageBudgetExceeded:
        raise
    except (
        OSError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
        ConnectionError,
        TimeoutError,
        genai_errors.ClientError,
        genai_errors.ServerError,
    ) as exc:
        raise RuntimeError(
            "Dynamic story planning failed before image generation. "
            "Retry the image stage; no fixed-ratio fallback or paid image request was used."
        ) from exc


def create_visual_plan(
    scenes: list[str], title: str, api_key: str, plan_path: Path
) -> dict:
    """Create and persist consistent visual prompts for every narration scene."""
    if plan_path.is_file():
        try:
            saved_plan = json.loads(plan_path.read_text(encoding="utf-8"))
            if valid_visual_plan(saved_plan, len(scenes)):
                print("Using saved cinematic scene plan.")
                return saved_plan
        except (OSError, json.JSONDecodeError):
            pass

    scene_list = "\n".join(
        f"{index}. {scene[:700]}" for index, scene in enumerate(scenes, start=1)
    )
    prompt = f"""Act as a senior art director and editor for an original narrated story.
First infer the intended audience, genre, emotional tone, and best visual medium from the
actual story. Do not force photorealism: choose cinematic live action for realistic adult
stories, age-appropriate 2D/3D animation for children, graphic-novel or gothic illustration
for suitable suspense, historically grounded realism for history, or another coherent medium
when it genuinely fits. Never imitate a named living artist, studio, franchise, or copyrighted
character.

Create a project profile, visual continuity bible, one image prompt and one restrained editing
direction for every numbered narration scene, a sparse sound cue sheet, plus one separate
thumbnail prompt. Preserve
recurring faces, age, body shape, clothing, props, architecture, geography, weather, lighting,
and palette across every scene. Describe visible action, camera distance, lens feeling, and
composition rather than copying abstract narration. Keep every image age-appropriate.

Compose every scene for cinematic 16:9, but keep important subjects inside a central safe area
so the low-cost fallback model can also be cropped safely. Treat the thumbnail as a separate
YouTube advertisement for the story, not a random scene. It must spark honest curiosity by
showing one strange clue, unexpected discovery, expressive reaction, looming question, or
unresolved moment without revealing the answer. Use a bold, colorful complementary palette,
bright focal lighting, deep contrast, clean shapes, and one instantly readable subject. Keep it
vivid and inviting rather than muddy, grey, overly serious, frightening, or visually busy.
Place the focal subject on the right and leave clean darker space on the left for headline text.
Do not put words, letters, logos, watermarks, frames, split screens, collages, gore, nudity, or
copyrighted characters in any prompt.

Also write a thumbnail hook of 2 to 4 simple words. It should create a truthful curiosity gap
that the story answers, preferably as a short question when natural. Do not merely repeat the
video title. Avoid vague hooks, fake danger, spoilers, difficult words, and exaggerated clickbait.

Sound design must support the story without competing with narration. Choose only clear,
visible or strongly implied story moments such as soft footsteps, a door opening, cloth or
leaves moving, birds, water, rain, wind, a small magical sound, or an occasional subtle
transition. Do not add a cue to every scene. Use no more than {min(30, max(1, (len(scenes) + 1) // 2))}
cues total, avoid loud bangs, screams, jump scares, music, speech, and constant ambience, and
keep sleep stories gentle. Adapt the sound character to the inferred audience and genre:
playful but controlled for cartoons and children, natural and restrained for adults.

Title: {title or '(derive from the story)'}
Narration scenes:
{scene_list}

Return JSON only with:
- project_profile: object with audience, genre, visual_medium, tone, palette, lighting
- visual_bible: a concise string
- scene_prompts: an array of exactly {len(scenes)} detailed strings in scene order
- scene_directions: an array of exactly {len(scenes)} objects, each containing camera
  (slow_push, slow_pull, pan_left, pan_right, pan_up, or pan_down), transition (fade, dissolve, smooth_left,
  or smooth_right), and atmosphere (none, stars, rain, snow, embers, fog, or motes)
- sound_cues: a sparse array of objects containing scene_index (1-based), position (a number
  from 0 to 1 within that scene), prompt (a concise sound-only description), duration_seconds
  (0.5 to 4.0), volume (0.04 to 0.16), and kind (action or environment)
- thumbnail_hook: a 2-to-4-word curiosity headline, with no quotation marks
- thumbnail_prompt: one detailed string
"""

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.65,
            ),
        )
        plan = json.loads(response.text or "")
        if not valid_visual_plan(plan, len(scenes)):
            raise ValueError("Gemini returned an incomplete scene plan")
        plan["scene_prompts"] = [
            f"{scene_prompt[:1750]}{STYLE_SUFFIX}"
            for scene_prompt in plan["scene_prompts"]
        ]
        plan["thumbnail_prompt"] = (
            f"{plan['thumbnail_prompt'][:1750]}{STYLE_SUFFIX}"
        )
        hook = str(plan.get("thumbnail_hook", "")).strip().strip('"\'')
        plan["thumbnail_hook"] = " ".join(hook.split()[:4]).upper()
        plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
        print(f"Saved cinematic scene plan: {plan_path.name}")
        return plan
    except (
        OSError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
        ConnectionError,
        TimeoutError,
        genai_errors.ClientError,
        genai_errors.ServerError,
    ) as exc:
        print(f"Warning: cinematic planning failed ({exc}); using narration excerpts.")
        return fallback_visual_plan(scenes)


def request_image(
    account_id: str, api_token: str, prompt: str, model: str, seed: int | None = None
) -> tuple[bytes, str]:
    api_url = (
        "https://api.cloudflare.com/client/v4/accounts/"
        f"{account_id}/ai/run/{model}"
    )
    parameters = {"prompt": prompt}
    if model == FAST_IMAGE_MODEL:
        parameters["steps"] = 4
    else:
        parameters.update(
            width=1920,
            height=1080,
            num_steps=24,
            guidance=5,
        )
        if seed is not None:
            parameters["seed"] = seed
    body = json.dumps(parameters).encode("utf-8")
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


def generate_image(
    account_id: str, api_token: str, prompt: str, model: str, seed: int | None = None
) -> tuple[bytes, str]:
    last_error = None
    active_prompt = prompt
    used_safety_fallback = False

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return request_image(account_id, api_token, active_prompt, model, seed)
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


def image_file(path: Path) -> Path | None:
    return next(
        (
            path.with_suffix(suffix)
            for suffix in (".jpg", ".jpeg", ".png")
            if path.with_suffix(suffix).is_file()
        ),
        None,
    )


def review_image_batch(
    client: genai.Client, items: list[tuple[str, Path, str]]
) -> dict[str, dict]:
    """Identify only severe, regenerable image problems in one vision request."""
    parts = [types.Part(text=(
        "Review these generated storytelling images against their expected prompts. "
        "Fail only for a severe visible problem: blank or nearly black output, unrelated "
        "subject, accidental text or logo, badly broken anatomy, missing main subject, or "
        "framing that hides the important action. Style preference alone is not a failure. "
        "Return JSON only with a reviews array. Each review needs id, passed, confidence "
        "from 0 to 1, and one short correction."
    ))]
    for item_id, path, expected_prompt in items:
        mime_type = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        parts.append(types.Part(text=f"ID: {item_id}\nEXPECTED: {expected_prompt[:900]}"))
        parts.append(types.Part.from_bytes(data=path.read_bytes(), mime_type=mime_type))
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[types.Content(role="user", parts=parts)],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.1,
        ),
    )
    payload = json.loads(response.text or "")
    reviews = payload.get("reviews", []) if isinstance(payload, dict) else []
    return {
        str(review.get("id")): review
        for review in reviews
        if isinstance(review, dict) and review.get("id")
    }


def review_and_repair_images(
    *, image_dir: Path, scenes: list[dict], thumbnail_prompt: str,
    gemini_api_key: str, account_id: str, api_token: str,
    image_model: str, story_seed: int,
) -> None:
    """Review distinct assets once and regenerate only confident severe failures."""
    manifest_path = image_dir / IMAGE_REVIEW_FILENAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            manifest = {}
    except (OSError, json.JSONDecodeError):
        manifest = {}

    review_items: list[tuple[str, Path, str]] = []
    prompts: dict[str, str] = {}
    for scene in scenes:
        if scene.get("reuse_scene_id"):
            continue
        item_id = str(scene["id"])
        path = image_file(image_dir / item_id)
        if path is None:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if manifest.get(item_id, {}).get("sha256") == digest:
            continue
        prompt = str(scene.get("prompt", ""))
        prompts[item_id] = prompt
        review_items.append((item_id, path, prompt))

    thumbnail_path = image_file(image_dir / THUMBNAIL_STEM)
    if thumbnail_path is not None:
        digest = hashlib.sha256(thumbnail_path.read_bytes()).hexdigest()
        if manifest.get(THUMBNAIL_STEM, {}).get("sha256") != digest:
            prompts[THUMBNAIL_STEM] = thumbnail_prompt
            review_items.append((THUMBNAIL_STEM, thumbnail_path, thumbnail_prompt))

    if not review_items:
        return
    client = genai.Client(api_key=gemini_api_key)
    for start in range(0, len(review_items), 6):
        batch = review_items[start:start + 6]
        try:
            reviews = review_image_batch(client, batch)
        except (
            OSError, ValueError, TypeError, AttributeError, json.JSONDecodeError,
            ConnectionError, TimeoutError,
            genai_errors.ClientError, genai_errors.ServerError,
        ) as exc:
            print(f"  Warning: image quality review was skipped ({exc})")
            return
        for item_id, path, _prompt in batch:
            review = reviews.get(item_id, {})
            try:
                confidence = float(review.get("confidence", 0))
            except (TypeError, ValueError):
                confidence = 0
            passed = bool(review.get("passed", True))
            correction = str(review.get("correction", "")).strip()[:300]
            if not passed and confidence >= 0.8:
                print(f"  Regenerating weak {item_id}: {correction or 'severe visual issue'}")
                repair_prompt = (
                    f"{prompts[item_id]} Correct this severe issue: {correction}. "
                    "Keep the intended characters, action, setting, palette, and native 16:9 framing."
                )
                repair_seed = story_seed + int(
                    hashlib.sha256(item_id.encode()).hexdigest()[:6], 16
                )
                try:
                    repaired_data, suffix = generate_image(
                        account_id, api_token, repair_prompt, image_model, repair_seed
                    )
                    replacement = path.with_suffix(suffix)
                    temporary = replacement.with_name(
                        replacement.stem + ".repairing" + suffix
                    )
                    temporary.write_bytes(repaired_data)
                    temporary.replace(replacement)
                    if replacement != path:
                        path.unlink(missing_ok=True)
                    path = replacement
                    status = "regenerated"
                except (OSError, RuntimeError) as exc:
                    print(f"  Warning: {item_id} repair was skipped ({exc})")
                    status = "kept_repair_failed"
            else:
                status = "passed" if passed else "kept_low_confidence"
            manifest[item_id] = {
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "status": status,
                "confidence": confidence,
                "note": correction,
            }
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate art-directed scenes using Cloudflare Workers AI."
    )
    parser.add_argument(
        "script_path",
        nargs="?",
        help="Path to the .txt script file; omit to select one interactively",
    )
    parser.add_argument(
        "--title",
        default="",
        help="Optional post title used to compose the dedicated thumbnail",
    )
    parser.add_argument(
        "--max-images",
        type=int,
        default=None,
        help="Maximum number of distinct paid scene images",
    )
    parser.add_argument(
        "--content-style",
        default="cinematic",
        help="Creator's preferred visual direction",
    )
    parser.add_argument("--niche", default="", help="Creator's saved channel niche")
    parser.add_argument("--audience", default="", help="Creator's saved target audience")
    args = parser.parse_args()

    script_path = select_script(args.script_path)
    if not script_path.exists():
        raise SystemExit(f"Script file not found: {script_path}")

    env_path = PROJECT_ROOT / ".env"
    load_dotenv(dotenv_path=env_path, override=True)

    account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID")
    api_token = os.getenv("CLOUDFLARE_API_TOKEN")
    image_model = os.getenv("CLOUDFLARE_IMAGE_MODEL", CLOUDFLARE_MODEL).strip()
    if not account_id or not api_token:
        raise SystemExit(
            "Cloudflare credentials were not found. Add CLOUDFLARE_ACCOUNT_ID "
            "and CLOUDFLARE_API_TOKEN to .env."
        )
    if image_model not in {CLOUDFLARE_MODEL, FAST_IMAGE_MODEL}:
        raise SystemExit(
            "CLOUDFLARE_IMAGE_MODEL must be @cf/leonardo/lucid-origin or "
            "@cf/black-forest-labs/flux-1-schnell."
        )
    print(f"Image model: {image_model}")

    text = script_path.read_text(encoding="utf-8")
    if not text.strip():
        raise SystemExit("The selected script is empty.")
    story_seed = int(
        hashlib.sha256(f"{args.title}\n{text}".encode("utf-8")).hexdigest()[:8], 16
    )
    try:
        max_story_images = args.max_images or int(
            os.getenv("MAX_STORY_IMAGES", str(DEFAULT_MAX_STORY_IMAGES))
        )
    except ValueError as exc:
        raise SystemExit("MAX_STORY_IMAGES must be a positive integer.") from exc
    if max_story_images < 1:
        raise SystemExit("MAX_STORY_IMAGES must be a positive integer.")
    max_story_images = min(max_story_images, DEFAULT_MAX_STORY_IMAGES)

    image_dir = IMAGES_DIR / script_path.stem
    image_dir.mkdir(parents=True, exist_ok=True)
    gemini_api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    plan_path = image_dir / SCENE_PLAN_FILENAME
    if not gemini_api_key:
        raise SystemExit(
            "GEMINI_API_KEY is required for dynamic story-based image planning."
        )
    visual_plan = create_dynamic_visual_plan(
        text,
        args.title,
        gemini_api_key,
        plan_path,
        max_story_images,
        args.content_style,
        args.niche,
        args.audience,
    )

    if visual_plan.get("version") == PLAN_VERSION:
        scene_entries = visual_plan["scenes"]
    else:
        legacy_scenes = split_into_scenes(text)
        scene_entries = [
            {
                "id": f"scene_{index:03d}",
                "prompt": visual_plan["scene_prompts"][index - 1],
                "reuse_scene_id": None,
            }
            for index in range(1, len(legacy_scenes) + 1)
        ]
    distinct_count = sum(not scene.get("reuse_scene_id") for scene in scene_entries)
    print(
        f"Dynamic storyboard: {len(scene_entries)} story beats, "
        f"{distinct_count} distinct images."
    )

    pending_scenes = []
    for i, scene in enumerate(scene_entries, start=1):
        image_stem = scene["id"]
        reused_scene_id = scene.get("reuse_scene_id")
        if reused_scene_id:
            print(
                f"  Scene {i}/{len(scene_entries)} reuses {reused_scene_id}; "
                "no paid image request needed."
            )
            continue
        existing_image = next(
            (
                image_dir / f"{image_stem}{suffix}"
                for suffix in (".jpg", ".jpeg", ".png")
                if (image_dir / f"{image_stem}{suffix}").exists()
            ),
            None,
        )
        if existing_image:
            print(f"  Scene {i}/{len(scene_entries)} already done, skipping.")
            continue
        pending_scenes.append((i, scene))

    def generate_pending_scene(index: int, scene: dict) -> int:
        image_stem = scene["id"]
        prompt = scene["prompt"]
        print(f"  Generating scene {index}/{len(scene_entries)}: {prompt[:70]}...")
        image_data, suffix = generate_image(
            account_id,
            api_token,
            prompt,
            image_model,
            story_seed + int(hashlib.sha256(image_stem.encode()).hexdigest()[:6], 16),
        )
        image_path = image_dir / f"{image_stem}{suffix}"
        temporary_path = image_dir / f"{image_stem}.generating{suffix}"
        temporary_path.write_bytes(image_data)
        temporary_path.replace(image_path)
        return index

    try:
        requested_workers = int(os.getenv("IMAGE_GENERATION_WORKERS", "3"))
    except ValueError:
        requested_workers = 3
    worker_count = min(max(1, requested_workers), 4, len(pending_scenes))
    if pending_scenes:
        print(
            f"Generating {len(pending_scenes)} new images with "
            f"{worker_count} concurrent workers."
        )
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(generate_pending_scene, index, scene): index
                for index, scene in pending_scenes
            }
            for future in as_completed(futures):
                completed_index = future.result()
                print(f"  Scene {completed_index}/{len(scene_entries)} saved.")

    for scene in scene_entries:
        reused_scene_id = scene.get("reuse_scene_id")
        if not reused_scene_id:
            continue
        source_exists = any(
            (image_dir / f"{reused_scene_id}{suffix}").is_file()
            for suffix in (".jpg", ".jpeg", ".png")
        )
        if not source_exists:
            raise RuntimeError(
                f"{scene['id']} reuses {reused_scene_id}, but its source image could not be created."
            )

    existing_thumbnail = next(
        (
            image_dir / f"{THUMBNAIL_STEM}{suffix}"
            for suffix in (".jpg", ".jpeg", ".png")
            if (image_dir / f"{THUMBNAIL_STEM}{suffix}").exists()
        ),
        None,
    )
    if existing_thumbnail:
        print("  Dedicated thumbnail source already done, skipping.")
    else:
        print("  Generating dedicated thumbnail source...")
        thumbnail_data, thumbnail_suffix = generate_image(
            account_id,
            api_token,
            visual_plan["thumbnail_prompt"],
            image_model,
            story_seed + int(hashlib.sha256(b"thumbnail").hexdigest()[:6], 16),
        )
        thumbnail_path = image_dir / f"{THUMBNAIL_STEM}{thumbnail_suffix}"
        temporary_path = image_dir / f"{THUMBNAIL_STEM}.generating{thumbnail_suffix}"
        temporary_path.write_bytes(thumbnail_data)
        temporary_path.replace(thumbnail_path)

    print("Reviewing generated images for severe visual problems...")
    review_and_repair_images(
        image_dir=image_dir,
        scenes=scene_entries,
        thumbnail_prompt=visual_plan["thumbnail_prompt"],
        gemini_api_key=gemini_api_key,
        account_id=account_id,
        api_token=api_token,
        image_model=image_model,
        story_seed=story_seed,
    )

    print(f"\nAll images saved to {image_dir}")
    print(f"Total story beats: {len(scene_entries)}")
    print(f"Distinct generated images: {distinct_count}")


if __name__ == "__main__":
    main()
