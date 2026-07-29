"""Commercial plans and supported creator narration voices."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Plan:
    key: str
    name: str
    monthly_price_ngn: int
    monthly_jobs: int
    max_minutes: int
    max_images: int
    storage_gb: int
    description: str
    best_for: str
    publishing_pace: str


PLANS = {
    "free": Plan(
        "free", "Free", 0, 3, 5, 8, 1,
        "Try making full videos before you pay.",
        "People who want to test Sleep Studio.",
        "Make up to 3 short videos in 30 days.",
    ),
    "basic": Plan(
        "basic", "Basic", 15_000, 10, 10, 16, 10,
        "Make videos often for a new or small channel.",
        "Creators who post about two videos each week.",
        "Make up to 10 videos in 30 days.",
    ),
    "pro": Plan(
        "pro", "Pro", 40_000, 30, 20, 32, 50,
        "Make longer videos and post almost every day.",
        "Active creators who want to post every day.",
        "Make up to 30 videos in 30 days.",
    ),
    "studio": Plan(
        "studio", "Studio", 100_000, 100, 30, 48, 200,
        "Make many long videos every month.",
        "Busy creators who need several videos each day.",
        "Make up to 100 videos in 30 days.",
    ),
}

PAID_PLAN_KEYS = ("basic", "pro", "studio")
PLAN_RANK = {"free": 0, "basic": 1, "pro": 2, "studio": 3}

# Google Gemini prebuilt TTS voices. Labels describe tone rather than promising
# a specific gender, which can be subjective across languages and listeners.
VOICE_OPTIONS = {
    "Kore": "Kore — firm and clear",
    "Achird": "Achird — friendly",
    "Algenib": "Algenib — gravelly",
    "Algieba": "Algieba — smooth",
    "Charon": "Charon — informative",
    "Despina": "Despina — smooth",
    "Erinome": "Erinome — clear",
    "Fenrir": "Fenrir — excitable",
    "Laomedeia": "Laomedeia — upbeat",
    "Leda": "Leda — youthful",
    "Orus": "Orus — firm",
    "Puck": "Puck — upbeat",
    "Sulafat": "Sulafat — warm",
    "Umbriel": "Umbriel — relaxed",
    "Vindemiatrix": "Vindemiatrix — gentle",
    "Zephyr": "Zephyr — bright",
}

NICHE_OPTIONS = {
    "bedtime": "Bedtime and gentle sleep stories",
    "mystery": "Mystery and calm suspense",
    "children": "Children's stories and cartoons",
    "horror": "Atmospheric horror",
    "history": "History and forgotten places",
    "fantasy": "Fantasy and magical journeys",
    "nature": "Nature and animal stories",
    "folklore": "Folklore, legends and cultural tales",
    "motivation": "Reflective and motivational stories",
    "documentary": "Narrative documentaries",
}

CONTENT_STYLES = {
    "cinematic": "Cinematic and realistic",
    "animated": "Colorful 3D animation",
    "storybook": "Illustrated storybook",
    "documentary": "Documentary realism",
    "gothic": "Gentle gothic atmosphere",
    "minimal": "Calm and visually minimal",
}

NICHE_PROMPTS = {
    "bedtime": (
        "A tired traveler discovers a quiet village where every window glows with a different memory.",
        "A night train makes one gentle, impossible stop beneath the northern lights.",
    ),
    "mystery": (
        "A librarian finds a returned book containing tomorrow's weather in handwritten notes.",
        "Every evening, one lamp turns on inside a lighthouse that has been empty for fifty years.",
    ),
    "children": (
        "A shy cloud learns to make tiny rainbows with help from a cheerful garden snail.",
        "A young baker discovers that the moon has fallen into a bag of flour.",
    ),
    "horror": (
        "A radio host receives calm calls from a town that disappeared decades ago.",
        "A hotel guest notices that the hallway becomes one door longer every midnight.",
    ),
    "history": (
        "Follow one handwritten letter as it travels through a forgotten coastal kingdom.",
        "Spend a quiet night with the last keeper of an ancient mountain observatory.",
    ),
    "fantasy": (
        "A mapmaker discovers a peaceful island that appears only when nobody is searching for it.",
        "An apprentice lantern keeper must guide a lost constellation home before sunrise.",
    ),
    "nature": (
        "Follow a river otter through one changing day in a hidden forest valley.",
        "A migrating bird rests on an island where the trees seem to remember every season.",
    ),
    "folklore": (
        "A village drummer follows a mysterious rhythm into a moonlit forest and returns with a gift.",
        "An old bridge keeper meets the gentle spirit who protects travelers during storms.",
    ),
    "motivation": (
        "A discouraged gardener learns patience from a seed that blooms only under starlight.",
        "A clock repairer helps a busy town rediscover one quiet hour each day.",
    ),
    "documentary": (
        "Trace the hidden nighttime life of an old railway station from sunset to dawn.",
        "Explore how a remote island community reads the sea, wind and stars.",
    ),
}


def prompt_starters(niche: str) -> tuple[str, ...]:
    specific = NICHE_PROMPTS.get(niche, ())
    return specific + (
        "A stranger arrives with one unusual object, changing a quiet community in an unexpected way.",
        "Tell a simple journey built around curiosity, one clear problem, and a satisfying emotional ending.",
    )


def plan_for(key: str) -> Plan:
    return PLANS.get(key, PLANS["free"])
