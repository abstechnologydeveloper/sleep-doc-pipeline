
"""Automatic sleep-story topic and social metadata generation."""

import json
import os

from google import genai
from google.genai import errors as genai_errors
from google.genai import types


MODEL = "gemini-2.5-flash"
FIELDS = ("topic", "title", "description", "hashtags")


def generate_post_metadata(
    topic: str = "",
    title: str = "",
    description: str = "",
    hashtags: str = "",
    niche: str = "",
    audience: str = "",
    content_style: str = "",
    creator_goal: str = "",
    search_keyword: str = "",
    recent_ideas: list[str] | None = None,
) -> dict[str, str]:
    """Fill missing concept and post fields while preserving supplied values."""
    supplied = {
        "topic": topic.strip(),
        "title": title.strip(),
        "description": description.strip(),
        "hashtags": hashtags.strip(),
    }
    if all(supplied.values()):
        return supplied

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is required for automatic post generation")
    recent_idea_lines = "\n".join(
        f"- {idea}" for idea in (recent_ideas or [])
    ) or "- None yet"

    prompt = f"""Create the missing fields for one original storytelling video.
The concept must be easy to understand, fun to follow, and suitable for the creator's stated
audience and goal. Give it a clear character, goal, curiosity hook, and a few
small discoveries instead of relying only on atmosphere. It may be playful, cozy, historical,
mysterious, gently eerie, for children, or for adults, but never graphic, childish when
the audience is adult, or based closely on an existing copyrighted story.
The central problem, title promise, thumbnail idea, and eventual resolution must describe the
same story. Prefer a premise with a natural cause-and-effect journey, a meaningful choice, and
room to pay off the opening question instead of a mood-only concept.
Use very simple English. Prefer common words and short sentences. Avoid formal, poetic, or
academic wording. The idea must be clear when heard once.

Existing values must be preserved when present:
- topic: {supplied['topic'] or '(generate this)'}
- title: {supplied['title'] or '(generate this)'}
- description: {supplied['description'] or '(generate this)'}
- hashtags: {supplied['hashtags'] or '(generate this)'}

Creator profile guidance:
- niche: {niche or 'general storytelling'}
- target audience: {audience or 'a general storytelling audience'}
- preferred picture style: {content_style or 'choose what fits the story'}
- creator's goal: {creator_goal or 'make an enjoyable original video'}
- vidIQ-validated target search phrase: {search_keyword or '(none supplied)'}
Use this guidance when generating missing fields, but do not mention the profile itself.
When a target search phrase is supplied, keep the concept relevant to its real search intent,
use the phrase once naturally in the title when it reads well, and once in the first sentence
of the description. Never repeat it unnaturally or change factual claims to force it in.

This creator's recent ideas are listed below. Do not repeat their central character, character
name, setting, object, mystery, title pattern, opening event, or ending. If the list is empty,
still avoid stock AI defaults such as Ella, Luna, Lily, Maya, Leo, Oliver, Finn, or Pip, and
avoid defaulting to whispering lights, glowing forests, forgotten towns, magical clocks, or
fallen stars unless the creator explicitly asks for them.
The new concept must differ from recent ideas in at least five ways: character age or role,
setting, time or weather, main goal, important object, story problem, turning point, ending,
or title pattern. Do not only rename an old character and repeat the same plot.
Recent ideas:
{recent_idea_lines}

Return one JSON object with string fields named topic, title, description, and hashtags.
The topic should be one specific story premise written in very simple everyday English. The
title should be natural, intriguing, short, and free of difficult words. The description
should be two short, clear sentences. Hashtags should contain 4-6 relevant tags separated by spaces.
Return JSON only."""

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.9,
            ),
        )
        generated = json.loads(response.text or "")
        if not isinstance(generated, dict):
            raise TypeError("Gemini returned JSON that is not an object")
    except (
        genai_errors.ClientError,
        genai_errors.ServerError,
        ConnectionError,
        TimeoutError,
        json.JSONDecodeError,
        TypeError,
    ) as exc:
        raise RuntimeError(f"Automatic post generation failed: {exc}") from exc

    completed = {}
    for field in FIELDS:
        generated_value = generated.get(field, "")
        if isinstance(generated_value, list):
            generated_value = " ".join(str(value) for value in generated_value)
        completed[field] = supplied[field] or str(generated_value).strip()
        if not completed[field]:
            raise RuntimeError(f"Automatic post generation returned no {field}")
    return completed
