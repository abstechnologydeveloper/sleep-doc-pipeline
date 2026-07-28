import base64
import hashlib
import http.client
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib import error, parse, request

from cryptography.fernet import Fernet, InvalidToken

from . import database


PLATFORMS = ("youtube", "facebook", "instagram", "tiktok")
YOUTUBE_SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
)


@dataclass(frozen=True)
class ConnectorStatus:
    name: str
    configured: bool
    requirement: str
    account_name: str = ""
    connect_url: str = ""


REQUIREMENTS = {
    "youtube": "Connect the YouTube channel that should receive this creator's videos",
    "facebook": "Meta app plus Facebook Page token",
    "instagram": "Linked Instagram Professional account and Meta token",
    "tiktok": "Approved TikTok Content Posting API application",
}

REQUIRED_ENV = {
    "facebook": ("META_ACCESS_TOKEN", "FACEBOOK_PAGE_ID"),
    "instagram": ("META_ACCESS_TOKEN", "INSTAGRAM_ACCOUNT_ID"),
    "tiktok": ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "TIKTOK_ACCESS_TOKEN"),
}


def _public_base_url() -> str:
    return os.getenv("PUBLIC_BASE_URL", "http://localhost:8090").rstrip("/")


def _youtube_redirect_uri() -> str:
    return f"{_public_base_url()}/connections/youtube/callback"


def youtube_oauth_enabled() -> bool:
    return bool(
        os.getenv("GOOGLE_OAUTH_CLIENT_ID")
        and os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")
        and len(os.getenv("OAUTH_TOKEN_ENCRYPTION_KEY", "")) >= 32
    )


def youtube_authorization_url(state: str) -> str:
    if not youtube_oauth_enabled():
        raise RuntimeError("YouTube OAuth is not configured")
    query = parse.urlencode(
        {
            "client_id": os.getenv("GOOGLE_OAUTH_CLIENT_ID", ""),
            "redirect_uri": _youtube_redirect_uri(),
            "response_type": "code",
            "scope": " ".join(YOUTUBE_SCOPES),
            "state": state,
            "access_type": "offline",
            "include_granted_scopes": "true",
            "prompt": "consent",
        }
    )
    return f"https://accounts.google.com/o/oauth2/v2/auth?{query}"


def _token_cipher() -> Fernet:
    secret = os.getenv("OAUTH_TOKEN_ENCRYPTION_KEY", "")
    if len(secret) < 32:
        raise RuntimeError("OAUTH_TOKEN_ENCRYPTION_KEY must contain at least 32 characters")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_token(value: str) -> str:
    return _token_cipher().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_token(value: str) -> str:
    try:
        return _token_cipher().decrypt(value.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError("The stored YouTube connection can no longer be decrypted") from exc


def _json_request(url: str, *, data: bytes | None = None, headers: dict | None = None) -> dict:
    api_request = request.Request(url, data=data, headers=headers or {})
    try:
        with request.urlopen(api_request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except (error.HTTPError, error.URLError, TimeoutError, ValueError) as exc:
        raise RuntimeError("YouTube rejected the connection request") from exc


def exchange_youtube_code(code: str) -> dict:
    if not code or not youtube_oauth_enabled():
        raise RuntimeError("YouTube OAuth is not configured")
    token_data = _json_request(
        "https://oauth2.googleapis.com/token",
        data=parse.urlencode(
            {
                "code": code,
                "client_id": os.getenv("GOOGLE_OAUTH_CLIENT_ID", ""),
                "client_secret": os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", ""),
                "redirect_uri": _youtube_redirect_uri(),
                "grant_type": "authorization_code",
            }
        ).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    access_token = str(token_data.get("access_token", ""))
    if not access_token:
        raise RuntimeError("Google did not return a YouTube access token")
    channel_data = _json_request(
        "https://www.googleapis.com/youtube/v3/channels?part=id%2Csnippet&mine=true",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    channels = channel_data.get("items") or []
    if not channels:
        raise RuntimeError("This Google account does not have a YouTube channel")
    channel = channels[0]
    expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=max(0, int(token_data.get("expires_in", 3600)) - 60)
    )
    return {
        "external_account_id": str(channel.get("id", "")),
        "account_name": str(channel.get("snippet", {}).get("title", "YouTube channel"))[:200],
        "access_token": access_token,
        "refresh_token": str(token_data.get("refresh_token", "")),
        "token_expires_at": expires_at.isoformat(timespec="seconds"),
        "scopes": str(token_data.get("scope", " ".join(YOUTUBE_SCOPES))),
    }


def connector_statuses(user_id: int) -> list[ConnectorStatus]:
    youtube = database.get_social_connection(user_id, "youtube")
    statuses = [
        ConnectorStatus(
            name="youtube",
            configured=bool(youtube and youtube_oauth_enabled()),
            requirement=REQUIREMENTS["youtube"],
            account_name=str(youtube["account_name"]) if youtube else "",
            connect_url="/connections/youtube" if youtube_oauth_enabled() else "",
        )
    ]
    statuses.extend(
        ConnectorStatus(
            name=platform,
            configured=all(os.getenv(key) for key in REQUIRED_ENV[platform]),
            requirement=REQUIREMENTS[platform],
        )
        for platform in PLATFORMS
        if platform != "youtube"
    )
    return statuses


def _refresh_youtube_access_token(user_id: int, connection) -> str:
    refresh_token = decrypt_token(connection["refresh_token_encrypted"])
    token_data = _json_request(
        "https://oauth2.googleapis.com/token",
        data=parse.urlencode(
            {
                "client_id": os.getenv("GOOGLE_OAUTH_CLIENT_ID", ""),
                "client_secret": os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", ""),
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            }
        ).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    access_token = str(token_data.get("access_token", ""))
    if not access_token:
        raise RuntimeError("YouTube did not refresh this creator's access token")
    expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=max(0, int(token_data.get("expires_in", 3600)) - 60)
    )
    database.update_social_access_token(
        user_id,
        "youtube",
        encrypt_token(access_token),
        expires_at.isoformat(timespec="seconds"),
    )
    return access_token


def _upload_youtube(user_id: int, video_path: Path, metadata: dict) -> dict:
    connection = database.get_social_connection(user_id, "youtube")
    if not connection:
        raise RuntimeError(REQUIREMENTS["youtube"])
    access_token = _refresh_youtube_access_token(user_id, connection)
    privacy = os.getenv("YOUTUBE_PRIVACY_STATUS", "private").lower()
    if privacy not in {"private", "unlisted", "public"}:
        privacy = "private"
    description = "\n\n".join(
        value.strip() for value in (metadata.get("description", ""), metadata.get("hashtags", ""))
        if value and value.strip()
    )[:5000]
    payload = json.dumps(
        {
            "snippet": {
                "title": (metadata.get("title") or video_path.stem)[:100],
                "description": description,
            },
            "status": {"privacyStatus": privacy},
        }
    ).encode("utf-8")
    initiate = request.Request(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet%2Cstatus",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": "video/mp4",
            "X-Upload-Content-Length": str(video_path.stat().st_size),
        },
    )
    try:
        with request.urlopen(initiate, timeout=30) as response:
            upload_url = response.headers.get("Location", "")
    except (error.HTTPError, error.URLError, TimeoutError) as exc:
        raise RuntimeError("YouTube could not start the video upload") from exc
    if not upload_url:
        raise RuntimeError("YouTube did not return a resumable upload URL")

    target = parse.urlsplit(upload_url)
    if target.scheme != "https" or not target.hostname:
        raise RuntimeError("YouTube returned an invalid upload URL")
    connection_http = http.client.HTTPSConnection(target.hostname, target.port or 443, timeout=120)
    try:
        connection_http.putrequest("PUT", target.path + (f"?{target.query}" if target.query else ""))
        connection_http.putheader("Authorization", f"Bearer {access_token}")
        connection_http.putheader("Content-Type", "video/mp4")
        connection_http.putheader("Content-Length", str(video_path.stat().st_size))
        connection_http.endheaders()
        with video_path.open("rb") as video:
            while chunk := video.read(1024 * 1024):
                connection_http.send(chunk)
        response = connection_http.getresponse()
        response_body = response.read()
        if response.status not in {200, 201}:
            raise RuntimeError(f"YouTube upload failed with status {response.status}")
        uploaded = json.loads(response_body.decode("utf-8"))
    except (OSError, ValueError, http.client.HTTPException) as exc:
        raise RuntimeError("The YouTube video upload did not complete") from exc
    finally:
        connection_http.close()
    video_id = str(uploaded.get("id", ""))
    if not video_id:
        raise RuntimeError("YouTube completed without returning a video ID")
    return {"remote_id": video_id, "remote_url": f"https://youtu.be/{video_id}"}


def publish(platform: str, owner_id: int | None, video_path: Path, metadata: dict) -> dict:
    if platform == "youtube":
        if owner_id is None:
            raise RuntimeError("Legacy unowned jobs cannot use a creator YouTube connection")
        return _upload_youtube(owner_id, video_path, metadata)
    status = next(item for item in connector_statuses(owner_id or 0) if item.name == platform)
    if not status.configured:
        raise RuntimeError(status.requirement + " is not configured")
    raise RuntimeError(
        f"{platform.title()} credentials are present, but its official upload "
        "connector still requires platform app review and activation."
    )
