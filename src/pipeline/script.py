#!/usr/bin/env python3
"""Generate a duration-controlled sleep narration script using Gemini."""

import argparse
import hashlib
import os
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

from project_paths import PROJECT_ROOT, SCRIPTS_DIR


MODEL = "gemini-2.5-flash"
MAX_PARTS = 20
MAX_FACT_REVISIONS = 1
WORDS_PER_CHUNK = 2_200
WORDS_PER_MINUTE = 150
MINIMUM_WORD_RATIO = 0.9
WORD_PATTERN = re.compile(r"\b[\w]+(?:[’'-][\w]+)*\b", flags=re.UNICODE)
HEADING_PATTERN = re.compile(
    r"^(?:chapter|part|section)\s+(?:\d+|[ivxlcdm]+)\b|^(?:introduction|conclusion)\s*$",
    flags=re.IGNORECASE,
)
FACTUAL_NICHE_PATTERN = re.compile(
    r"\b(?:history|historical|documentary|war|military|science|scientific|biography|true story)\b",
    flags=re.IGNORECASE,
)
NON_NAME_WORDS = {
    "After", "Again", "Before", "But", "Finally", "For", "From", "He", "Her", "His",
    "However", "I", "If", "In", "It", "Later", "Meanwhile", "Now", "She", "So", "Soon",
    "That", "The", "Then", "They", "This", "Though", "When", "While", "With", "You",
}


def story_sentence_hashes(text: str) -> set[str]:
    """Hash substantial normalized sentences for exact passage-reuse detection."""
    hashes = set()
    for sentence in re.split(r"(?<=[.!?])(?:[\"'”’]*)\s+", text):
        normalized = " ".join(WORD_PATTERN.findall(sentence.lower()))
        if len(normalized.split()) >= 8:
            hashes.add(hashlib.sha256(normalized.encode("utf-8")).hexdigest())
    return hashes


def repeated_story_passages(script: str, recent_scripts: list[str]) -> tuple[int, float]:
    candidate_hashes = story_sentence_hashes(script)
    if not candidate_hashes:
        return 0, 0.0
    previous_hashes = set().union(
        *(story_sentence_hashes(recent) for recent in recent_scripts)
    ) if recent_scripts else set()
    repeated = len(candidate_hashes & previous_hashes)
    return repeated, repeated / len(candidate_hashes)


def recent_character_names(recent_scripts: list[str]) -> list[str]:
    """Find recurring capitalized names so the next script can avoid them."""
    counts: Counter[str] = Counter()
    for script in recent_scripts:
        for name in re.findall(r"\b[A-Z][a-z]{2,}\b", script):
            if name not in NON_NAME_WORDS:
                counts[name] += 1
    names = {name for name, count in counts.items() if count >= 2}
    return sorted(names, key=lambda name: (-counts[name], name))[:40]


def weak_opening_reason(script: str) -> str:
    """Flag only unmistakably generic video introductions for one safe retry."""
    opening = " ".join(script.split()[:80]).lower()
    if re.match(
        r"^(?:hello|hi|welcome|welcome back|today we|in this (?:story|video)|"
        r"sit back|relax and)\b",
        opening,
    ):
        return "generic greeting"
    return ""


def needs_grounded_research(niche: str, topic: str) -> bool:
    """Return whether a topic needs evidence before it becomes narration."""
    return bool(FACTUAL_NICHE_PATTERN.search(f"{niche} {topic}"))


def grounding_sources(response) -> list[str]:
    """Extract unique Google Search grounding links without relying on one SDK shape."""
    sources: list[str] = []
    seen_urls: set[str] = set()
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return sources
    metadata = getattr(candidates[0], "grounding_metadata", None)
    for chunk in getattr(metadata, "grounding_chunks", None) or []:
        web = getattr(chunk, "web", None)
        url = str(getattr(web, "uri", "") or "").strip()
        title = str(getattr(web, "title", "") or "").strip()
        if url and url not in seen_urls:
            seen_urls.add(url)
            sources.append(f"- {title + ': ' if title else ''}{url}")
    return sources


def grounded_text(client: genai.Client, prompt: str) -> str:
    """Run a low-temperature Google-grounded request and retain its source links."""
    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            temperature=0.2,
        ),
    )
    text = str(response.text or "").strip()
    if not text:
        raise RuntimeError("Grounded research returned no usable text.")
    sources = grounding_sources(response)
    if sources:
        text = f"{text}\n\nGROUNDING SOURCES\n" + "\n".join(sources)
    return text


def research_factual_topic(client: genai.Client, topic: str, niche: str) -> str:
    """Create a source-backed fact brief before drafting a factual video."""
    return grounded_text(
        client,
        f"""Research this planned factual YouTube video using reliable primary, museum,
government, university, or established historical sources wherever possible.

Topic: {topic}
Channel niche: {niche or 'factual documentary'}

Return a concise production brief with these headings:
VERIFIED FACTS, CHRONOLOGY, DISPUTED OR UNCERTAIN CLAIMS, CLAIMS TO AVOID, PRONUNCIATIONS,
and SOURCE NOTES. Include exact names, dates, places, context, and uncertainty needed to write
the video accurately. Do not invent dialogue, private thoughts, numbers, motives, or events.
Clearly label legends and disputed claims. This is research, not the narration script.""",
    )


def review_factual_script(
    client: genai.Client, topic: str, script: str, research_brief: str
) -> str:
    """Ground-check every material factual claim before downstream paid work starts."""
    return grounded_text(
        client,
        f"""Fact-check the narration below using Google Search and the supplied research brief.
Check names, dates, places, sequence, quantities, quotations, causation, disputed claims, and
whether legend is presented as fact. Also flag invented dialogue or private thoughts.

The first line must be exactly VERDICT: PASS when no material correction is needed, or
VERDICT: FAIL when any material claim needs correction. After that, list each issue with a
specific correction. Do not fail only for writing style.

TOPIC
{topic}

RESEARCH BRIEF
{research_brief}

NARRATION
{script}""",
    )


def revise_factual_script(
    client: genai.Client,
    script: str,
    research_brief: str,
    review: str,
    min_words: int,
    max_words: int,
    niche: str,
    audience: str,
    creator_goal: str,
    content_style: str = "",
    title: str = "",
    avoid_character_names: list[str] | None = None,
) -> str:
    """Correct a failed factual draft once while preserving the requested duration."""
    response = client.models.generate_content(
        model=MODEL,
        contents=f"""Correct the narration using the fact-check report and research brief.
Remove any claim that cannot be supported. Label legends and uncertainty clearly. Do not add
new facts, quotations, dialogue, or private thoughts. Keep one continuous narration between
{min_words:,} and {max_words:,} words and return narration prose only.

RESEARCH BRIEF
{research_brief}

FACT-CHECK REPORT
{review}

CURRENT NARRATION
{script}""",
        config=types.GenerateContentConfig(
            system_instruction=build_system_prompt(
                min_words, max_words, niche, audience, creator_goal,
                content_style, title, avoid_character_names,
            ),
            max_output_tokens=8_000,
            temperature=0.25,
        ),
    )
    if not response.text:
        raise RuntimeError("The factual correction pass returned no narration.")
    return apply_quality_gate(response.text, min_words, max_words)


def build_system_prompt(
    min_words: int, max_words: int, niche: str = "", audience: str = "",
    creator_goal: str = "", content_style: str = "", title: str = "",
    avoid_character_names: list[str] | None = None,
) -> str:
    creator_context = ""
    if niche or audience or creator_goal or content_style:
        creator_context = (
            f"\nCreator context: primary niche is {niche or 'general storytelling'}; "
            f"target audience is {audience or 'a general storytelling audience'}. "
            f"Their goal is {creator_goal or 'make an enjoyable original video'}. "
            f"Preferred picture direction is {content_style or 'choose what fits the story'}. "
            "Use this to choose suitable characters, language, stakes, humor, pacing, and emotional payoff.\n"
        )
    avoided_name_rule = ""
    if avoid_character_names:
        avoided_name_rule = (
            "- Do not reuse these character names from this creator's recent stories: "
            f"{', '.join(avoid_character_names)}. Invent a different name that fits this exact "
            "character, culture, species, place, and time. A required real historical name is allowed.\n"
        )
    profile_text = f"{niche} {audience} {creator_goal}".lower()
    if FACTUAL_NICHE_PATTERN.search(profile_text):
        opening_rule = """- Open inside the most important real moment, choice, discovery, or conflict. Within
  the first 80 spoken words, establish the relevant place and time in simple language, show
  what is at stake, and give the viewer the main historical question or promise. Do not begin
  with a greeting, channel introduction, broad textbook summary, or unsupported dramatic claim."""
    elif any(
        word in profile_text
        for word in ("sleep", "bedtime", "cozy", "relax", "calm", "gentle")
    ):
        opening_rule = """- Open with the named lead already doing something visible, then introduce one gentle
  surprise, problem, missing object, unusual visitor, or unanswered question within the first
  80 spoken words. State what the lead wants or must do. Stay calm, but do not begin with a
  long description of weather, scenery, silence, or sleep."""
    else:
        opening_rule = """- Open with a visible action, difficult choice, surprising fact, or clear mystery.
  Within the first 80 spoken words, establish the focal subject, what they want, and the main
  question that gives the viewer a reason to continue."""
    if FACTUAL_NICHE_PATTERN.search(profile_text):
        closing_rule = """- End by answering the opening historical question with supported facts, then explain
  in two or three plain sentences what changed and why it still matters. Do not end with a
  generic lesson, channel promotion, invented quotation, or request to like and subscribe."""
    elif any(
        word in profile_text
        for word in ("sleep", "bedtime", "cozy", "relax", "calm", "gentle")
    ):
        closing_rule = """- End by paying off the opening surprise and showing the lead safe, changed, or
  satisfied through one visible final action. Settle the sound and movement naturally. Do not
  recap the plot, announce a moral, tell the listener to sleep, or add a channel promotion."""
    else:
        closing_rule = """- End by resolving the opening question through the lead's choices. Show the result
  in one memorable final action or image instead of explaining the whole story again."""
    title_rule = (
        f'- The working video title is "{title}". The opening question, central conflict, climax, '
        "and ending must honestly deliver its promise without repeating the title as narration."
        if title else
        "- Keep the opening question, central conflict, climax, and ending focused on one honest promise."
    )
    return f"""You write narration scripts for a storytelling video channel.
Create one continuous narrative arc with no chapters, chapter headings, numbered sections,
repeated templates, or other structural labels. Match the energy to the creator's niche,
audience, and goal: gentle for sleep, lively and playful for children, mature and tense for
mystery or horror, clear and engaging for history or science, and warm when the topic asks
for it. Do not force every story into a bedtime mood. The finished script must be {min_words:,} to
{max_words:,} words. Do not copy, quote, or closely paraphrase any real source material.
Use original language and follow these writing rules:
- Use very simple spoken English. Write so a ten-year-old can understand it after hearing it
  once, but do not make adult stories childish.
- Use common, short words. Put only one fact, action, or idea in each sentence.
- Keep most sentences between 5 and 11 words. Avoid sentences longer than 16 words unless a
  full name, date, or necessary historical term makes this impossible.
- Explain an uncommon word or historical term immediately with simple words.
- Prefer direct verbs such as walked, saw, opened, found, asked, built, fought, or left.
- Do not use semicolons, long clauses, academic language, formal speech, poetic metaphors, or
  decorative description. Avoid AI-sounding words such as nestled, tranquil, ethereal,
  tapestry, symphony, profound, enigmatic, testament, and emanating unless truly necessary.
- Use only a few useful sensory details. Do not describe the same light, weather, silence,
  feeling, room, or landscape again in different words.
- Open with a simple curiosity hook, then introduce a small discovery, choice, surprise,
  funny detail, or emotional change regularly so the journey stays interesting.
{opening_rule}
{title_rule}
- Establish one clear central character or focal subject, what they want or need, and the
  main story question early. Do not spend a long opening only describing atmosphere.
- Build a cause-and-effect chain: each important action, discovery, decision, and consequence
  must grow naturally from what happened before. Avoid coincidences that solve the problem.
- Give the middle real progress and change. Add a complication, useful discovery, reversal,
  relationship shift, or difficult choice instead of repeating similar scenes.
- Add a meaningful progress beat roughly every 45 to 90 seconds of expected narration: a new
  clue, attempt, obstacle, answer, choice, visual location, relationship change, or consequence.
  Each beat must change what the viewer understands. Do not use fake cliffhangers or repeat the
  same question in different words.
- Keep names, relationships, knowledge, motivations, locations, props, time, weather, travel,
  and physical details consistent unless the story clearly changes them.
- When the topic does not provide a character name, choose a fresh name that fits the setting,
  culture, species, time period, and audience. Avoid stock AI motifs such as glowing forests,
  whispering lights, fallen stars, magical clocks, and forgotten towns unless asked.
{avoided_name_rule}- When the topic or picture direction calls for animation, freely use animals, birds, fish,
  weather, objects, or imaginary beings as main characters. They may live, work, travel, and
  solve problems like people while keeping traits and abilities that fit what they are.
- Make the character's occupation, age, personality, goal, flaw, relationships, and choices
  specific to this premise. Do not recycle the same lonely traveler, child, librarian, baker,
  lighthouse keeper, or mysterious stranger structure when the topic does not require it.
- Give this video a distinct story shape. Vary the opening event, main problem, setting,
  character age, occupation, relationship, key object, turning point, and ending. Never reuse
  the same sequence of arrival, mysterious guide, glowing clue, hidden room, and peaceful rest.
- Pay off important clues, promises, objects, rules, and relationships. The climax should
  result from the central character's accumulated choices, learning, kindness, or courage.
- Resolve the central story question and show the emotional change before a gentle ending.
  Do not rush, preach a moral, recap the whole plot, or introduce a new major conflict.
{closing_rule}
- Use natural dialogue and gentle humor when they fit. Never force jokes into sad, tense,
  historical, or reflective moments.
- Infer the likely audience and genre from the topic. A children's or cartoon story may be
  playful and lively; an adult story should remain mature without becoming stiff or childish.
- Show what happens instead of lecturing, explaining a moral, or repeatedly describing the
  atmosphere. Avoid filler, cliches, recaps, and repeated ideas.
- Keep danger appropriate for the stated audience and avoid graphic or exploitative detail.
- For historical, cultural, scientific, documentary, or folklore topics, do not invent facts
  and present them as verified. Make original fictionalization clear through the narrative.
Vary sentence rhythm so the prose does not sound repetitive or like AI filler. Return only
narration prose, without notes about the request or writing process. Follow the requested word
range closely and include a gentle, satisfying, complete ending.{creator_context}"""


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


def apply_quality_gate(text: str, min_words: int, max_words: int) -> str:
    """Remove structural AI artefacts and reject incomplete narration safely."""
    cleaned: list[str] = []
    previous = ""
    for paragraph in (item.strip() for item in re.split(r"\n\s*\n", text)):
        if not paragraph or HEADING_PATTERN.match(paragraph):
            continue
        fingerprint = re.sub(r"\W+", " ", paragraph.lower()).strip()
        if fingerprint == previous:
            continue
        cleaned.append(paragraph)
        previous = fingerprint
    result = "\n\n".join(cleaned).strip()
    total = word_count(result)
    if total < min_words:
        raise RuntimeError(
            f"Story quality check found an incomplete narration ({total:,} words)."
        )
    result = trim_to_word_limit(result, min_words, max_words)
    if result and result[-1] not in '.!?\"”’':
        result = result.rstrip(" ,;:-") + "."
    return result


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
    niche: str = "",
    audience: str = "",
    creator_goal: str = "",
    content_style: str = "",
    title: str = "",
    research_brief: str = "",
    avoid_character_names: list[str] | None = None,
) -> str:
    draft = ""
    history: list[types.Content] = []

    config = types.GenerateContentConfig(
        system_instruction=build_system_prompt(
            min_words, max_words, niche, audience, creator_goal, content_style, title,
            avoid_character_names
        ),
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
            evidence = (
                "\n\nUse only the verified facts in this grounded research brief. "
                "Clearly label uncertainty and legends. Do not invent missing details.\n\n"
                f"GROUNDED RESEARCH BRIEF\n{research_brief}"
                if research_brief else ""
            )
            title_context = f'\nThe working video title is: "{title}".' if title else ""
            prompt = f'The topic is: "{topic}".{title_context}\n\n{instruction}{evidence}'
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
            return apply_quality_gate(draft, min_words, max_words)

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
    parser.add_argument("--niche", default="", help="Creator niche guidance")
    parser.add_argument("--audience", default="", help="Target audience guidance")
    parser.add_argument("--goal", default="", help="Creator outcome and publishing goal")
    parser.add_argument("--title", default="", help="Working video title promise")
    parser.add_argument("--content-style", default="", help="Preferred picture direction")
    parser.add_argument(
        "--recent-script",
        action="append",
        type=Path,
        default=[],
        help="A recent script used to prevent repeated names and passages",
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

    recent_scripts = []
    scripts_root = SCRIPTS_DIR.resolve()
    for recent_path in args.recent_script[:10]:
        resolved_path = recent_path.resolve()
        if (
            resolved_path.suffix.lower() == ".txt"
            and scripts_root in resolved_path.parents
            and resolved_path.is_file()
        ):
            try:
                recent_scripts.append(resolved_path.read_text(encoding="utf-8"))
            except OSError:
                continue
    avoided_names = recent_character_names(recent_scripts)

    research_brief = ""
    factual_mode = needs_grounded_research(args.niche, topic)
    if factual_mode:
        print("Researching factual claims with Google Search grounding...")
        research_brief = research_factual_topic(client, topic, args.niche)

    print(f"Generating script for: {topic}")
    print(
        f"Target duration: {minutes:g} minute(s) "
        f"({min_words:,}-{max_words:,} words)"
    )
    script = ""
    for generation_attempt in range(2):
        script = generate_script(
            client,
            topic,
            min_words,
            target_words,
            max_words,
            niche=args.niche,
            audience=args.audience,
            creator_goal=args.goal,
            content_style=args.content_style,
            title=args.title,
            research_brief=research_brief,
            avoid_character_names=avoided_names,
        )
        repeated_count, repeated_ratio = repeated_story_passages(
            script, recent_scripts
        )
        repeated_content = repeated_count >= 3 and repeated_ratio >= 0.04
        opening_issue = weak_opening_reason(script)
        if not repeated_content and not opening_issue:
            break
        if generation_attempt == 0:
            reason = (
                "The draft repeated recent story passages"
                if repeated_content
                else f"The draft used a {opening_issue}"
            )
            print(f"{reason}; generating a fresh version...")
        elif repeated_content:
            raise RuntimeError(
                "The script repeated too much wording from the creator's recent stories."
            )

    fact_check = ""
    if factual_mode:
        print("Fact-checking the completed narration...")
        fact_check = review_factual_script(client, topic, script, research_brief)
        for revision_number in range(MAX_FACT_REVISIONS):
            if fact_check.lstrip().upper().startswith("VERDICT: PASS"):
                break
            print(
                f"Correcting factual issues automatically "
                f"({revision_number + 1}/{MAX_FACT_REVISIONS})..."
            )
            script = revise_factual_script(
                client,
                script,
                research_brief,
                fact_check,
                min_words,
                max_words,
                args.niche,
                args.audience,
                args.goal,
                args.content_style,
                args.title,
                avoided_names,
            )
            fact_check = review_factual_script(client, topic, script, research_brief)

    output_dir = SCRIPTS_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"{timestamp}_{safe_topic_slug(topic)}.txt"
    output_path.write_text(script + "\n", encoding="utf-8")
    if factual_mode:
        output_path.with_suffix(".research.md").write_text(
            f"# Grounded research\n\n{research_brief}\n", encoding="utf-8"
        )
        output_path.with_suffix(".fact-check.md").write_text(
            f"# Automated fact-check\n\n{fact_check}\n", encoding="utf-8"
        )

    print(f"\nSaved script to {output_path}")
    if factual_mode:
        print(f"Research: {output_path.with_suffix('.research.md')}")
        print(f"Fact-check: {output_path.with_suffix('.fact-check.md')}")
    print(f"Word count: {word_count(script):,}")


if __name__ == "__main__":
    main()
