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
The concept must be calm, low-stimulation, atmospheric, and suitable for a sleep ambient
YouTube channel. It may be cozy, historical, mysterious, or gently eerie, but never
graphic, loud, or based closely on an existing copyrighted story.

Existing values must be preserved when present:
- topic: {supplied['topic'] or '(generate this)'}
- title: {supplied['title'] or '(generate this)'}
- description: {supplied['description'] or '(generate this)'}
- hashtags: {supplied['hashtags'] or '(generate this)'}

Return one JSON object with string fields named topic, title, description, and hashtags.
The topic should be one specific story premise. The title should be natural and concise.
The description should be two calm sentences. Hashtags should contain 4-6 relevant tags
separated by spaces. Return JSON only."""

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
