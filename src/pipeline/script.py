#!/usr/bin/env python3
"""Generate a duration-controlled sleep narration script using Gemini."""

import argparse
import os
import re
from datetime import datetime

from dotenv import load_dotenv
from google import genai
from google.genai import types

from project_paths import PROJECT_ROOT, SCRIPTS_DIR


MODEL = "gemini-2.5-flash"
MAX_PARTS = 20
WORDS_PER_CHUNK = 2_200
WORDS_PER_MINUTE = 150
MINIMUM_WORD_RATIO = 0.9
WORD_PATTERN = re.compile(r"\b[\w]+(?:[’'-][\w]+)*\b", flags=re.UNICODE)


def build_system_prompt(min_words: int, max_words: int) -> str:
    return f"""You write narration scripts for a sleep and storytelling YouTube channel.
Create one continuous narrative arc with no chapters, chapter headings, numbered sections,
repeated templates, or other structural labels. Use calm, low-stimulation pacing suitable
for a listener who is falling asleep. The finished script must be {min_words:,} to
{max_words:,} words. Do not copy, quote, or closely paraphrase any real source material.
Use original language and vary sentence length and rhythm so the prose does not sound
repetitive or like AI filler. Return only narration prose, without notes about the request
or the writing process. Follow the requested word range closely and include a gentle,
complete ending."""


def word_count(text: str) -> int:
    return len(WORD_PATTERN.findall(text))


def trim_to_word_limit(text: str, min_words: int, max_words: int) -> str:
    """Cap text at a sentence boundary without exceeding max_words."""
    word_matches = list(WORD_PATTERN.finditer(text))
    if len(word_matches) <= max_words:
        return text

    maximum_end = word_matches[max_words - 1].end()
    minimum_end = word_matches[min(min_words, max_words) - 1].end()
    candidate = text[:maximum_end].rstrip()
    sentence_endings = list(re.finditer(r"[.!?](?:[\"'”’]*)", candidate[minimum_end:]))
    if sentence_endings:
        sentence_end = minimum_end + sentence_endings[-1].end()
        return candidate[:sentence_end].strip()

    return candidate.rstrip(" ,;:-") + "."


def safe_topic_slug(topic: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")
    return slug[:60].rstrip("-") or "sleep-story"


def duration_word_limits(minutes: float) -> tuple[int, int, int]:
    max_words = max(1, round(minutes * WORDS_PER_MINUTE))
    min_words = max(1, round(max_words * MINIMUM_WORD_RATIO))
    target_words = (min_words + max_words) // 2
    return min_words, target_words, max_words


def continuation_instruction(
    current_words: int,
    remaining_total: int,
    min_words: int,
    max_words: int,
) -> tuple[str, bool]:
    chunk_target = min(WORDS_PER_CHUNK, max(remaining_total, 800))

    if current_words == 0:
        if remaining_total <= WORDS_PER_CHUNK:
            return (
                f"Write a complete narrative about the topic in {min_words:,} to "
                f"{max_words:,} words, including a gentle ending.",
                True,
            )
        return (
            f"Begin the narrative. Write at least {chunk_target:,} words in this single "
            "response. Establish the setting and story gently, but do not conclude the "
            "narrative yet. Do not stop until you have written the full amount.",
            False,
        )

    if remaining_total > chunk_target + 500:
        return (
            "Continue directly from the exact point where the draft stops, without a "
            "heading, recap, restart, or repeated passage. Write at least "
            f"{chunk_target:,} more words developing the same narrative arc, but do not "
            "conclude it yet. Do not stop until you have written the full amount.",
            False,
        )

    return (
        "Continue directly from the exact point where the draft stops, without a "
        "heading, recap, restart, or repeated passage. Bring the existing narrative to "
        f"a gentle, complete ending. Write at least {remaining_total:,} more words so "
        f"the complete script lands between {min_words:,} and {max_words:,} words total. "
        "Return only the new continuation, not any of the existing draft.",
        True,
    )


def generate_script(
    client: genai.Client,
    topic: str,
    min_words: int,
    target_words: int,
    max_words: int,
    verbose: bool = True,
) -> str:
    draft = ""
    history: list[types.Content] = []

    config = types.GenerateContentConfig(
        system_instruction=build_system_prompt(min_words, max_words),
        max_output_tokens=8_000,
        temperature=0.8,
    )

    for part_num in range(1, MAX_PARTS + 1):
        current_words = word_count(draft)
        remaining_total = max(target_words - current_words, 0)

        instruction, ending_requested = continuation_instruction(
            current_words,
            remaining_total,
            min_words,
            max_words,
        )

        if not draft:
            prompt = f'The topic is: "{topic}".\n\n{instruction}'
        else:
            prompt = instruction

        history.append(
            types.Content(role="user", parts=[types.Part(text=prompt)])
        )

        response = client.models.generate_content(
            model=MODEL,
            contents=history,
            config=config,
        )
        addition = response.text

        if not addition or not addition.strip():
            if verbose:
                print(f"  [part {part_num}] empty response, retrying...")
            history.pop()
            continue

        history.append(
            types.Content(role="model", parts=[types.Part(text=addition)])
        )

        added_words = word_count(addition)
        draft = f"{draft.rstrip()}\n\n{addition.strip()}".strip()
        current_words = word_count(draft)

        if verbose:
            print(f"  [part {part_num}] +{added_words:,} words -> {current_words:,} total")

        if ending_requested and current_words >= min_words:
            if current_words > max_words:
                draft = trim_to_word_limit(draft, min_words, max_words)
            return draft

    raise RuntimeError(
        f"The model did not reach {min_words:,} words after {MAX_PARTS} requests "
        f"({word_count(draft):,} words generated)."
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a sleep narration script for a chosen duration."
    )
    parser.add_argument(
        "topic",
        nargs="?",
        help="Topic or niche; omit to enter it interactively",
    )
    parser.add_argument(
        "--minutes",
        type=float,
        help="Desired narration duration; omit to enter it interactively",
    )
    args = parser.parse_args()
    if args.minutes is not None and args.minutes <= 0:
        parser.error("--minutes must be greater than zero")
    return args


def prompt_for_topic(configured_topic: str | None) -> str:
    if configured_topic and configured_topic.strip():
        return configured_topic.strip()

    while True:
        try:
            topic = input("Enter the story topic or niche: ").strip()
            if topic:
                return topic
        except (EOFError, KeyboardInterrupt) as exc:
            raise SystemExit("\nTopic selection cancelled.") from exc

        print("Topic cannot be empty.")


def prompt_for_minutes(configured_minutes: float | None) -> float:
    if configured_minutes is not None:
        return configured_minutes

    while True:
        try:
            response = input("Desired narration duration in minutes [1]: ").strip()
            minutes = float(response) if response else 1.0
            if minutes > 0:
                return minutes
        except ValueError:
            pass
        except (EOFError, KeyboardInterrupt) as exc:
            raise SystemExit("\nDuration selection cancelled.") from exc

        print("Enter a number greater than zero, such as 1, 5, or 60.")


def main() -> None:
    args = parse_args()
    topic = prompt_for_topic(args.topic)
    minutes = prompt_for_minutes(args.minutes)
    min_words, target_words, max_words = duration_word_limits(minutes)
    env_path = PROJECT_ROOT / ".env"
    load_dotenv(dotenv_path=env_path, override=True)

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise SystemExit(
            "GEMINI_API_KEY was not found. Get a free key from Google AI Studio "
            "(aistudio.google.com) and add it to .env as GEMINI_API_KEY=your_key_here"
        )

    client = genai.Client(api_key=api_key)

    print(f"Generating script for: {topic}")
    print(
        f"Target duration: {minutes:g} minute(s) "
        f"({min_words:,}-{max_words:,} words)"
    )
    script = generate_script(
        client,
        topic,
        min_words,
        target_words,
        max_words,
    )

    output_dir = SCRIPTS_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"{timestamp}_{safe_topic_slug(topic)}.txt"
    output_path.write_text(script + "\n", encoding="utf-8")

    print(f"\nSaved script to {output_path}")
    print(f"Word count: {word_count(script):,}")


if __name__ == "__main__":
    main()
