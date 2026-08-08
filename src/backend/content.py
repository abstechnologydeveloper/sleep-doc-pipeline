
"""Automatic sleep-story topic and social metadata generation."""

import json
import os
import re

from google import genai
from google.genai import errors as genai_errors
from google.genai import types


MODEL = "gemini-2.5-flash"
FIELDS = ("topic", "title", "description", "hashtags")
FACTUAL_TOPIC_PATTERN = re.compile(
    r"\b(?:history|historical|science|biography|true story|invent|invented|invention|"
    r"origin|origins|archaeology|ancient|discovery|who created|how did humans|"
    r"how .{1,80} changed .{1,80} forever)\b",
    flags=re.IGNORECASE,
)


def generate_post_metadata(
    topic: str = "",
    title: str = "",
    description: str = "",
    hashtags: str = "",
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
    factual_context = " ".join((*supplied.values(), search_keyword))
    factual_topic = bool(FACTUAL_TOPIC_PATTERN.search(factual_context))
    mode_rule = (
        "This is a factual topic. Preserve its real question. Do not invent a named main "
        "character, inventor, witness, quotation, event, or answer. Never change an unknown "
        "origin into a fictional story. The title and description must clearly allow for "
        "uncertainty and gradual development when no single inventor is known."
        if factual_topic else
        "This is a narrative topic. Give it a clear character, goal, curiosity hook, and a "
        "few small discoveries instead of relying only on atmosphere."
    )
    novelty_rule = (
        "For this factual topic, vary the angle and title wording without inventing names, "
        "people, evidence, or events."
        if factual_topic else
        "Invent names that fit the exact character, culture, species, place, and time instead "
        "of relying on a familiar default."
    )
    structure_rule = (
        "Keep the title, thumbnail idea, and description focused on the same factual question. "
        "Build interest from evidence, how the subject worked, what changed over time, and "
        "what remains unknown. Do not force a hero, personal goal, conflict, climax, moral, "
        "or neat answer when the evidence does not support one."
        if factual_topic else
        "The central problem, title promise, thumbnail idea, and eventual resolution must "
        "describe the same story. Prefer a natural cause-and-effect journey, a meaningful "
        "choice, and a real payoff instead of a mood-only concept."
    )
    recent_rule = (
        "Do not repeat the exact factual subject, evidence angle, or title wording of a recent "
        "video. Do not manufacture differences by inventing people or events."
        if factual_topic else
        "Do not repeat a recent central character, name, setting, object, mystery, title "
        "pattern, opening event, or ending. Make the new concept differ in several meaningful "
        "ways instead of merely renaming an old character."
    )
    topic_rule = (
        "The topic must remain the supplied factual subject. The title should state its real "
        "question clearly. The description should promise only what evidence can explain."
        if factual_topic else
        "The topic should be one specific story premise written in simple everyday English."
    )

    prompt = f"""Create the missing fields for one original storytelling video.
The concept must be easy to understand, fun to follow, and suitable for the audience implied
by this video's idea. {mode_rule} It may be playful, cozy, historical,
mysterious, gently eerie, for children, or for adults, but never graphic, childish when
the audience is adult, or based closely on an existing copyrighted story.
{structure_rule}
Use very simple English. Prefer common words and short sentences. Avoid formal, poetic, or
academic wording. The idea must be clear when heard once.

Existing values must be preserved when present:
- topic: {supplied['topic'] or '(generate this)'}
- title: {supplied['title'] or '(generate this)'}
- description: {supplied['description'] or '(generate this)'}
- hashtags: {supplied['hashtags'] or '(generate this)'}

- vidIQ-validated target search phrase: {search_keyword or '(none supplied)'}
When a target search phrase is supplied, keep the concept relevant to its real search intent,
use the phrase once naturally in the title when it reads well, and once in the first sentence
of the description. Never repeat it unnaturally or change factual claims to force it in.

This creator's recent ideas are listed below. {recent_rule}
{novelty_rule}
Avoid defaulting to whispering lights, glowing forests, forgotten towns, magical clocks, or
fallen stars unless the creator explicitly asks for them.
Recent ideas:
{recent_idea_lines}

Return one JSON object with string fields named topic, title, description, and hashtags.
{topic_rule} The
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
                temperature=0.4 if factual_topic else 0.9,
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
