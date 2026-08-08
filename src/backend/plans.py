"""Commercial plans and supported creator narration voices."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Plan:
    key: str
    name: str
    monthly_price_ngn: int
    monthly_price_usd: int
    monthly_jobs: int
    max_minutes: int
    max_images: int
    storage_gb: int
    description: str
    best_for: str
    publishing_pace: str


PLANS = {
    "free": Plan(
        "free", "Free", 0, 0, 0, 5, 8, 1,
        "Explore the workspace and plan your first video before you pay.",
        "People who want to browse the tools and plan their videos.",
        "View the workspace and edit your settings. Video creation requires a paid plan.",
    ),
    "basic": Plan(
        "basic", "Basic", 8_000, 5, 10, 10, 16, 10,
        "Make videos often for a new or small channel.",
        "Creators who post about two videos each week.",
        "Make up to 10 videos in 30 days.",
    ),
    "pro": Plan(
        "pro", "Pro", 24_000, 15, 30, 20, 32, 50,
        "Make longer videos and post almost every day.",
        "Active creators who want to post every day.",
        "Make up to 30 videos in 30 days.",
    ),
    "studio": Plan(
        "studio", "Studio", 80_000, 50, 100, 30, 48, 200,
        "Make many long videos every month.",
        "Busy creators who need several videos each day.",
        "Make up to 100 videos in 30 days.",
    ),
}

PAID_PLAN_KEYS = ("basic", "pro", "studio")
PLAN_RANK = {"free": 0, "basic": 1, "pro": 2, "studio": 3}

# Verified provider-prefixed AI33.Pro voice IDs sent to the narration API.
DEFAULT_NARRATION_VOICE = "edge_en-US-AvaNeural"
VOICE_OPTIONS = {
    "edge_en-US-AvaNeural": "Ava — natural American female narrator",
    "edge_en-US-EmmaNeural": "Emma — warm American female narrator",
    "edge_en-GB-SoniaNeural": "Sonia — clear British female narrator",
    "edge_en-US-AndrewNeural": "Andrew — natural American male narrator",
    "edge_en-US-BrianNeural": "Brian — steady American male narrator",
    "edge_en-GB-RyanNeural": "Ryan — clear British male narrator",
    "edge_en-NG-EzinneNeural": "Ezinne — Nigerian female narrator",
    "edge_en-NG-AbeoNeural": "Abeo — Nigerian male narrator",
}

VOICE_DIRECTIONS = {
    "neutral": "Natural",
    "youthful": "Youthful",
    "mature": "Mature",
    "deep": "Deep and steady",
    "warm": "Warm",
    "bright": "Bright",
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
    "war_history": "War and military history",
    "deep_history": "Deep history and ancient worlds",
    "science": "Science and discoveries",
    "space": "Space and astronomy",
    "technology": "Technology and inventions",
    "true_crime": "True crime and investigations",
    "adventure": "Adventure and survival",
    "romance": "Romance and relationships",
    "comedy": "Comedy and funny stories",
    "business": "Business and success stories",
    "biography": "Biographies and famous lives",
    "religion": "Faith and religious stories",
    "african_folklore": "African folklore and legends",
    "mythology": "Mythology and ancient gods",
    "education": "Simple learning videos",
    "animals": "Animals and wildlife",
    "ocean": "Oceans and underwater life",
    "future": "The future and imagined worlds",
    "unsolved": "Unsolved events and strange places",
    "family": "Family and life lessons",
}

CONTENT_STYLES = {
    "cinematic": "Cinematic and realistic",
    "photorealistic": "Photo-realistic",
    "documentary": "Documentary realism",
    "historical_documentary": "History documentary",
    "ancient_history": "Ancient history",
    "deep_history": "Deep history",
    "war_history": "War history",
    "military_documentary": "Military documentary",
    "scientific": "Scientific pictures",
    "space_science": "Space and astronomy",
    "medical_illustration": "Medical illustration",
    "nature_documentary": "Nature documentary",
    "wildlife": "Wildlife photography",
    "archaeology": "Archaeology and ruins",
    "museum_archive": "Museum and archive",
    "vintage_film": "Old vintage film",
    "film_noir": "Black-and-white film noir",
    "gothic": "Gentle gothic atmosphere",
    "horror": "Dark horror",
    "dark_fantasy": "Dark fantasy",
    "epic_fantasy": "Epic fantasy",
    "folklore": "Folklore art",
    "mythology": "Mythology art",
    "animated_3d": "Colorful 3D animation",
    "cartoon_2d": "Colorful 2D cartoon",
    "storybook": "Children's storybook",
    "watercolor": "Soft watercolor",
    "oil_painting": "Classic oil painting",
    "anime": "Anime",
    "comic_book": "Comic book",
    "graphic_novel": "Graphic novel",
    "clay_animation": "Clay animation",
    "paper_cutout": "Paper cutout",
    "minimal": "Calm and visually minimal",
    "dreamy": "Soft and dreamy",
    "cozy": "Warm and cozy",
    "surreal": "Surreal dream world",
    "cyberpunk": "Cyberpunk city",
    "futuristic": "Clean futuristic",
    "news_report": "News report style",
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
    normalized = niche.strip().lower()
    key = next(
        (option_key for option_key, label in NICHE_OPTIONS.items()
         if normalized in {option_key.lower(), label.lower()}),
        normalized,
    )
    specific = NICHE_PROMPTS.get(key, ())
    return specific + (
        "A stranger arrives with one unusual object, changing a quiet community in an unexpected way.",
        "Tell a simple journey built around curiosity, one clear problem, and a satisfying emotional ending.",
    )


def plan_for(key: str) -> Plan:
    return PLANS.get(key, PLANS["free"])
