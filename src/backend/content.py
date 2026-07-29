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

    prompt = f"""Create the missing fields for one original sleep-story video.
The concept must be easy to understand, fun to follow, calm enough for bedtime, and suitable
for a storytelling YouTube channel. Give it a clear character, goal, curiosity hook, and a few
small discoveries instead of relying only on atmosphere. It may be playful, cozy, historical,
mysterious, gently eerie, for children, or for adults, but never graphic, loud, childish when
the audience is adult, or based closely on an existing copyrighted story.
The central problem, title promise, thumbnail idea, and eventual resolution must describe the
same story. Prefer a premise with a natural cause-and-effect journey, a meaningful choice, and
room to pay off the opening question instead of a mood-only concept.

Existing values must be preserved when present:
- topic: {supplied['topic'] or '(generate this)'}
- title: {supplied['title'] or '(generate this)'}
- description: {supplied['description'] or '(generate this)'}
- hashtags: {supplied['hashtags'] or '(generate this)'}

Creator profile guidance:
- niche: {niche or 'general storytelling'}
- target audience: {audience or 'a general storytelling audience'}
Use this guidance when generating missing fields, but do not mention the profile itself.

Return one JSON object with string fields named topic, title, description, and hashtags.
The topic should be one specific story premise written in plain everyday English. The title
should be natural, intriguing, and concise without clickbait or difficult words. The description
should be two clear sentences. Hashtags should contain 4-6 relevant tags separated by spaces.
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
