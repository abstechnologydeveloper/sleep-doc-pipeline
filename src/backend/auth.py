"""Passwordless email and Google OAuth helpers for My Automation Studio."""

import hashlib
import json
import os
import re
import secrets
from urllib import error, parse, request


EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def normalize_email(value: str) -> str:
    email = value.strip().lower()
    if len(email) > 254 or not EMAIL_PATTERN.fullmatch(email):
        raise ValueError("Enter a valid email address.")
    return email


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def request_ip_hash(client_host: str | None) -> str:
    salt = os.getenv("AUTH_IP_HASH_SALT") or os.getenv("ADMIN_SESSION_SECRET", "")
    return hashlib.sha256(f"{salt}:{client_host or 'unknown'}".encode()).hexdigest()


def public_base_url() -> str:
    return os.getenv("PUBLIC_BASE_URL", "http://localhost:8090").rstrip("/")


def send_magic_link(email: str, token: str) -> None:
    link = f"{public_base_url()}/auth/email/verify?token={parse.quote(token)}"
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    sender = os.getenv("AUTH_FROM_EMAIL", "").strip()
    if not api_key or not sender:
        if os.getenv("AUTH_DEBUG_LINKS", "false").lower() == "true":
            print(f"Development sign-in link for {email}: {link}", flush=True)
            return
        raise RuntimeError("Passwordless email is not configured.")
    payload = json.dumps(
        {
            "from": sender,
            "to": [email],
            "subject": "Your My Automation Studio sign-in link",
            "html": (
                "<h2>Sign in to My Automation Studio</h2>"
                f'<p><a href="{link}">Open your creator workspace</a></p>'
                "<p>This one-time link expires in 15 minutes. "
                "Ignore this email if you did not request it.</p>"
            ),
        }
    ).encode("utf-8")
    api_request = request.Request(
        "https://api.resend.com/emails",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "sleep-studio/1.0",
        },
    )
    try:
        with request.urlopen(api_request, timeout=20) as response:
            if response.status >= 300:
                raise RuntimeError("Email provider rejected the sign-in email.")
    except (error.URLError, error.HTTPError, TimeoutError) as exc:
        raise RuntimeError("The sign-in email could not be sent.") from exc


def send_welcome_email(email: str) -> None:
    """Send a new creator a short product introduction without blocking signup."""
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    sender = os.getenv("AUTH_FROM_EMAIL", "").strip()
    if not api_key or not sender:
        return
    workspace_url = f"{public_base_url()}/storytelling"
    payload = json.dumps(
        {
            "from": sender,
            "to": [email],
            "subject": "Welcome to My Automation Studio",
            "html": (
                "<h2>Welcome to My Automation Studio</h2>"
                "<p>Turn a simple story idea into a complete video without doing "
                "every editing step yourself.</p>"
                "<p>We help you create the story, matching scene pictures, voice, "
                "captions, sound, thumbnail, and finished video.</p>"
                "<p>You can review and download your video, or connect YouTube and "
                "publish it from your account.</p>"
                "<h3>Make your first video</h3>"
                "<ol><li>Enter your story idea.</li><li>Choose the video length, "
                "picture style, and voice.</li><li>Start the job and return when "
                "your video is ready.</li></ol>"
                f'<p><a href="{workspace_url}">Open your creator workspace</a></p>'
                "<p>We are glad to have you here.</p>"
            ),
        }
    ).encode("utf-8")
    api_request = request.Request(
        "https://api.resend.com/emails",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "my-automation-studio/1.0",
        },
    )
    try:
        with request.urlopen(api_request, timeout=20) as response:
            if response.status >= 300:
                print("Welcome email was rejected by the provider.", flush=True)
    except (error.URLError, error.HTTPError, TimeoutError):
        print("Welcome email could not be sent.", flush=True)


def google_enabled() -> bool:
    return bool(os.getenv("GOOGLE_OAUTH_CLIENT_ID") and os.getenv("GOOGLE_OAUTH_CLIENT_SECRET"))


def google_authorization_url(state: str) -> str:
    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")
    if not google_enabled():
        raise RuntimeError("Google sign-in is not configured.")
    query = parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": f"{public_base_url()}/auth/google/callback",
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "access_type": "online",
            "prompt": "select_account",
        }
    )
    return f"https://accounts.google.com/o/oauth2/v2/auth?{query}"


def google_identity(code: str) -> dict:
    body = parse.urlencode(
        {
            "code": code,
            "client_id": os.getenv("GOOGLE_OAUTH_CLIENT_ID", ""),
            "client_secret": os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", ""),
            "redirect_uri": f"{public_base_url()}/auth/google/callback",
            "grant_type": "authorization_code",
        }
    ).encode("utf-8")
    token_request = request.Request(
        "https://oauth2.googleapis.com/token",
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with request.urlopen(token_request, timeout=20) as response:
            tokens = json.loads(response.read().decode("utf-8"))
        id_token = tokens["id_token"]
        with request.urlopen(
            f"https://oauth2.googleapis.com/tokeninfo?id_token={parse.quote(id_token)}",
            timeout=20,
        ) as response:
            profile = json.loads(response.read().decode("utf-8"))
    except (KeyError, ValueError, error.URLError, error.HTTPError, TimeoutError) as exc:
        raise RuntimeError("Google sign-in could not be verified.") from exc
    if profile.get("aud") != os.getenv("GOOGLE_OAUTH_CLIENT_ID"):
        raise RuntimeError("Google returned a token for another application.")
    if str(profile.get("email_verified", "")).lower() != "true":
        raise RuntimeError("Google has not verified this email address.")
    return {
        "subject": str(profile["sub"]),
        "email": normalize_email(str(profile["email"])),
        "name": str(profile.get("name", ""))[:120],
        "avatar_url": str(profile.get("picture", ""))[:500],
    }


def new_token() -> str:
    return secrets.token_urlsafe(32)
