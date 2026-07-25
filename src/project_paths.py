"""Shared filesystem locations for source code and generated artifacts."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
AUDIO_DIR = PROJECT_ROOT / "audio"
IMAGES_DIR = PROJECT_ROOT / "images"
VIDEOS_DIR = PROJECT_ROOT / "videos"
THUMBNAILS_DIR = PROJECT_ROOT / "thumbnails"
