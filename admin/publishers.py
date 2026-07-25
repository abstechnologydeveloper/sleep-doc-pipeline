import os
from dataclasses import dataclass
from pathlib import Path


PLATFORMS = ("youtube", "facebook", "instagram", "tiktok")


@dataclass(frozen=True)
class ConnectorStatus:
    name: str
    configured: bool
    requirement: str


REQUIREMENTS = {
    "youtube": "YouTube OAuth client and refresh token",
    "facebook": "Meta app plus Facebook Page token",
    "instagram": "Linked Instagram Professional account and Meta token",
    "tiktok": "Approved TikTok Content Posting API application",
}

REQUIRED_ENV = {
    "youtube": ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN"),
    "facebook": ("META_ACCESS_TOKEN", "FACEBOOK_PAGE_ID"),
    "instagram": ("META_ACCESS_TOKEN", "INSTAGRAM_ACCOUNT_ID"),
    "tiktok": ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "TIKTOK_ACCESS_TOKEN"),
}


def connector_statuses() -> list[ConnectorStatus]:
    return [
        ConnectorStatus(
            name=platform,
            configured=all(os.getenv(key) for key in REQUIRED_ENV[platform]),
            requirement=REQUIREMENTS[platform],
        )
        for platform in PLATFORMS
    ]


def publish(platform: str, video_path: Path, metadata: dict) -> dict:
    """Publish through an official connector once its OAuth credentials exist.

    Connectors deliberately fail closed until implemented and approved instead
    of pretending a social upload succeeded.
    """
    status = next(item for item in connector_statuses() if item.name == platform)
    if not status.configured:
        raise RuntimeError(status.requirement + " is not configured")
    raise RuntimeError(
        f"{platform.title()} credentials are present, but its official upload "
        "connector still requires platform app review and activation."
    )
