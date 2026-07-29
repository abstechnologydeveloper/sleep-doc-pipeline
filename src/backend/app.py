import asyncio
import hmac
import json
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

from fastapi import BackgroundTasks, FastAPI, Form, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from project_paths import DATA_DIR, THUMBNAILS_DIR

from . import database
from . import storage
from . import paystack
from .auth import (
    google_authorization_url,
    google_enabled,
    google_identity,
    new_token,
    normalize_email,
    request_ip_hash,
    send_magic_link,
    send_welcome_email,
    token_hash,
)
from .publishers import (
    PLATFORMS,
    connector_statuses,
    encrypt_token,
    exchange_youtube_code,
    youtube_authorization_url,
)
from .worker import start_worker
from .social_preview import landing_preview_png
from .plans import (
    CONTENT_STYLES, NICHE_OPTIONS, PAID_PLAN_KEYS, PLAN_RANK, PLANS,
    VOICE_DIRECTIONS, VOICE_OPTIONS,
    prompt_starters,
)
from .content_policy import validate_creator_content


WEB_DIR = Path(__file__).resolve().parents[1] / "web"
TEMPLATES = Jinja2Templates(directory=WEB_DIR / "templates")
FRIENDLY_STATUSES = {
    "queued": "Waiting to start",
    "processing": "Creating your video",
    "publishing": "Sending to channel",
    "completed": "Ready to watch",
    "published": "Published",
    "waiting_for_connections": "Needs channel connection",
    "failed": "Needs attention",
    "cancel_requested": "Stopping",
    "pending": "Waiting",
    "waiting": "Needs connection",
}
TEMPLATES.env.filters["friendly_status"] = (
    lambda value: FRIENDLY_STATUSES.get(str(value), str(value).replace("_", " ").title())
)
TEMPLATES.env.filters["storage_size"] = lambda value: (
    f"{float(value) / (1024 ** 3):.1f} GB"
    if int(value) >= 1024 ** 3
    else f"{float(value) / (1024 ** 2):.0f} MB"
)
JOB_STATUSES = (
    "queued",
    "processing",
    "publishing",
    "completed",
    "published",
    "waiting_for_connections",
    "failed",
    "cancel_requested",
)
SHAREABLE_STATUSES = {"completed", "published", "waiting_for_connections"}
MAX_ACTIVE_CREATOR_JOBS = 1
EXAMPLE_TESTIMONIALS = (
    {
        "rating": 5,
        "comment": "The pictures followed my story well, and having the voice, captions, and video made together saved me time.",
        "public_display_name": "Anonymous story creator",
        "avatar_letter": "S",
        "is_example": True,
    },
    {
        "rating": 5,
        "comment": "I started with a short idea and received a complete video I could watch and download.",
        "public_display_name": "Anonymous YouTube creator",
        "avatar_letter": "Y",
        "is_example": True,
    },
    {
        "rating": 5,
        "comment": "The steps were easy to understand. I did not need video-editing experience to begin.",
        "public_display_name": "Anonymous first-time creator",
        "avatar_letter": "F",
        "is_example": True,
    },
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.initialize()
    stop_event, worker = start_worker()
    yield
    stop_event.set()
    worker.join(timeout=5)


app = FastAPI(title="My Automation Studio", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("ADMIN_SESSION_SECRET", secrets.token_hex(32)),
    https_only=os.getenv("COOKIE_SECURE", "false").lower() == "true",
    same_site="lax",
)
app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")


@app.middleware("http")
async def search_engine_headers(request: Request, call_next):
    response = await call_next(request)
    public_path = (
        request.url.path in {"/", "/pricing", "/robots.txt", "/sitemap.xml", "/social-preview.png"}
        or request.url.path.startswith("/legal/")
        or request.url.path.startswith("/static/")
    )
    if not public_path or "staging." in (request.url.hostname or "").lower():
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return response


def current_user(request: Request):
    user_id = request.session.get("user_id")
    if not isinstance(user_id, int):
        return None
    user = database.get_user(user_id)
    if not user or user["status"] != "active":
        request.session.clear()
        return None
    return user


def authenticated(request: Request) -> bool:
    return current_user(request) is not None


def is_admin(user) -> bool:
    return bool(user and user["role"] == "admin")


def csrf_token(request: Request) -> str:
    return request.session.setdefault("csrf", secrets.token_urlsafe(24))


def verify_csrf(request: Request, submitted: str) -> None:
    if not hmac.compare_digest(request.session.get("csrf", ""), submitted):
        raise ValueError("Invalid form token")


def login_required(request: Request):
    if not current_user(request):
        return RedirectResponse("/login?next=/app", status_code=303)
    return None


def admin_required(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login?next=/admin/customers", status_code=303)
    if not is_admin(user):
        return HTMLResponse("Administrator access required", status_code=403)
    return None


def creator_required(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login?next=/app", status_code=303)
    if user["role"] != "creator":
        return HTMLResponse("Creator account required", status_code=403)
    return None


def user_scope(request: Request) -> tuple[int, bool]:
    user = current_user(request)
    if not user:
        raise ValueError("Authentication required")
    return int(user["id"]), is_admin(user)


def owned_job(request: Request, job_id: int):
    user_id, include_all = user_scope(request)
    return database.get_job(job_id, owner_id=user_id, include_all=include_all)


def page_context(request: Request, section: str, **values) -> dict:
    user = current_user(request)
    page_names = {
        "overview": "Dashboard",
        "storytelling": "Create Video",
        "showcase": "Story Gallery",
        "social": "Publish",
        "notifications": "Updates",
        "settings": "Account",
        "subscription": "Subscription",
        "jobs": "My Videos",
        "customers": "Customers",
        "payments": "Payments",
        "usage": "Usage",
        "audit": "Activity Log",
    }
    return {
        "active_section": section,
        "title": f"{page_names.get(section, 'Workspace')} · My Automation Studio",
        "csrf": csrf_token(request),
        "current_user": user,
        "is_admin": is_admin(user),
        **values,
    }


def reusable_media(request: Request) -> list[dict]:
    items = []
    user_id, include_all = user_scope(request)
    for row in database.list_media_jobs(owner_id=user_id, include_all=include_all):
        video_reference = str(row["video_path"])
        if not storage.available(video_reference):
            continue
        item = dict(row)
        item["thumbnail_ready"] = thumbnail_available(row)
        items.append(item)
    return items


def reusable_media_job(job) -> bool:
    if not job or not job["video_path"]:
        return False
    try:
        platforms = json.loads(job["platforms"])
    except (TypeError, json.JSONDecodeError):
        return False
    return job["kind"] == "automatic" or (
        job["kind"] == "manual" and not platforms
    )


def shareable_job(job) -> bool:
    return bool(
        job
        and job["status"] in SHAREABLE_STATUSES
        and job["video_path"]
        and storage.available(job["video_path"])
    )


def creator_job_error(error: str | None) -> str:
    """Turn internal pipeline errors into useful messages without exposing server details."""
    detail = str(error or "").lower()
    if "storage is full" in detail:
        return "Your storage is full. Delete an old video or choose a larger plan, then retry."
    if "daily allocation" in detail or "daily limit" in detail or "quota" in detail:
        return "An AI service reached its usage limit. Please retry later."
    if "elevenlabs" in detail or "sound request" in detail:
        return "A sound effect could not be created. Please retry the video."
    if "audio" in detail or "voice" in detail or "tts" in detail:
        return "The narration could not be completed. Please retry the video."
    if "image" in detail or "cloudflare" in detail or "storyboard" in detail:
        return "One or more story pictures could not be completed. Please retry the video."
    if "ffmpeg" in detail or "assembling" in detail or "render" in detail:
        return "The final video could not be assembled. Your saved work can be retried."
    if "incomplete narration" in detail or "quality check" in detail:
        return "The story was incomplete, so it was stopped instead of creating a poor video. Please retry."
    if "timed out" in detail or "timeout" in detail or "temporarily unavailable" in detail:
        return "A video service was temporarily unavailable. Please retry later."
    return "The video could not be completed. Please retry it. If it fails again, contact support with the video number."


def job_log_excerpt(job) -> str:
    """Read only the end of a worker-owned log path for administrator diagnosis."""
    if not job or not job["log_path"]:
        return ""
    log_root = (DATA_DIR / "logs").resolve()
    try:
        log_path = Path(str(job["log_path"])).resolve()
        if not log_path.is_relative_to(log_root) or not log_path.is_file():
            return ""
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-40:])[-6000:]


def thumbnail_reference(job) -> str | Path | None:
    if not job or not job["video_path"]:
        return None
    remote = storage.thumbnail_reference(str(job["video_path"]))
    if remote:
        return remote
    return THUMBNAILS_DIR / f"{Path(job['video_path']).stem}.jpg"


def thumbnail_available(job) -> bool:
    reference = thumbnail_reference(job)
    if reference is None:
        return False
    return storage.available(str(reference))


def media_response(request: Request, reference: str, media_type: str):
    if storage.is_remote(reference):
        try:
            remote = storage.open_object(reference, request.headers.get("range"))
        except RuntimeError:
            return HTMLResponse("Media not available", status_code=404)
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(remote.get("ContentLength", "")),
        }
        if remote.get("ContentRange"):
            headers["Content-Range"] = str(remote["ContentRange"])
        return StreamingResponse(
            storage.iter_body(remote["Body"]),
            media_type=media_type,
            status_code=206 if remote.get("ContentRange") else 200,
            headers=headers,
        )
    if not Path(reference).is_file():
        return HTMLResponse("Media not available", status_code=404)
    return FileResponse(reference, media_type=media_type)


def delete_unreferenced_media(*references: str | None) -> None:
    for reference in {value for value in references if storage.is_remote(value)}:
        if database.media_reference_count(reference) != 0:
            continue
        for stored_reference in (reference, storage.thumbnail_reference(reference)):
            if not stored_reference:
                continue
            try:
                storage.delete_object(stored_reference)
                database.remove_media_asset(stored_reference)
            except RuntimeError:
                pass


def sync_creator_storage(user_id: int) -> None:
    """Index existing media once so legacy R2 files count toward plan storage."""
    references = {
        reference for reference in database.user_media_references(user_id)
        if storage.is_remote(reference)
    }
    references.update(
        thumbnail for thumbnail in (
            storage.thumbnail_reference(reference) for reference in tuple(references)
        ) if thumbnail
    )
    for reference in references:
        if database.media_asset_recorded(reference):
            continue
        try:
            size_bytes = storage.object_size(reference)
        except RuntimeError:
            continue
        database.record_media_asset(
            owner_id=user_id,
            reference=reference,
            size_bytes=size_bytes,
            kind="thumbnail" if reference.endswith("/thumbnail.jpg") else "video",
        )


def creator_storage(user) -> dict[str, int]:
    user_id = int(user["id"])
    sync_creator_storage(user_id)
    used = database.storage_usage_bytes(user_id)
    limit = int(user["storage_limit_bytes"])
    return {
        "used": used,
        "limit": limit,
        "remaining": max(0, limit - used),
        "percent": min(100, round(used * 100 / limit)) if limit else 100,
    }


def render_social_page(
    request: Request, error: str | None = None, status_code: int = 200
):
    connectors = connector_statuses(int(current_user(request)["id"]))
    return TEMPLATES.TemplateResponse(
        request,
        "social.html",
        page_context(
            request,
            "social",
            media_items=reusable_media(request),
            connectors=connectors,
            platforms=[{"name": item.name,
                        "available": item.name == "youtube" and item.configured}
                       for item in connectors],
            storage_usage=creator_storage(current_user(request)),
            error=error,
        ),
        status_code=status_code,
    )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/social-preview.png", name="landing_social_preview")
def landing_social_preview():
    return Response(
        landing_preview_png(),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/robots.txt")
def robots_txt(request: Request):
    base_url = public_base_url() or str(request.base_url).rstrip("/")
    if "staging." in base_url or "nip.io" in base_url:
        content = "User-agent: *\nDisallow: /\n"
    else:
        content = (
            "User-agent: *\n"
            "Allow: /\n"
            "Disallow: /app\nDisallow: /login\nDisallow: /auth/\n"
            "Disallow: /admin/\nDisallow: /jobs\nDisallow: /settings\n"
            "Disallow: /subscription\nDisallow: /social\nDisallow: /showcase\n"
            "Disallow: /share/\nDisallow: /billing/\nDisallow: /connections/\n"
            f"Sitemap: {base_url}/sitemap.xml\n"
        )
    return Response(content, media_type="text/plain")


@app.get("/sitemap.xml")
def sitemap_xml(request: Request):
    base_url = public_base_url() or str(request.base_url).rstrip("/")
    if "staging." in base_url or "nip.io" in base_url:
        return Response(
            '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"/>',
            media_type="application/xml",
            headers={"X-Robots-Tag": "noindex"},
        )
    paths = (
        ("/", "1.0", "weekly"),
        ("/pricing", "0.9", "weekly"),
        ("/legal/privacy", "0.4", "monthly"),
        ("/legal/terms", "0.4", "monthly"),
        ("/legal/acceptable-use", "0.3", "monthly"),
        ("/legal/copyright", "0.3", "monthly"),
        ("/legal/billing-policy", "0.4", "monthly"),
        ("/legal/support", "0.4", "monthly"),
    )
    entries = "".join(
        f"<url><loc>{base_url}{path}</loc><changefreq>{frequency}</changefreq><priority>{priority}</priority></url>"
        for path, priority, frequency in paths
    )
    return Response(
        f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{entries}</urlset>',
        media_type="application/xml",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.websocket("/ws/jobs")
async def job_updates(websocket: WebSocket):
    user_id = websocket.session.get("user_id")
    user = database.get_user(user_id) if isinstance(user_id, int) else None
    if not user or user["status"] != "active":
        await websocket.close(code=4401)
        return

    await websocket.accept()
    try:
        while True:
            user = await asyncio.to_thread(database.get_user, int(user["id"]))
            if not user or user["status"] != "active":
                await websocket.close(code=4403)
                return
            include_all = user["role"] == "admin"
            jobs = await asyncio.to_thread(
                database.list_job_statuses, 100, user["id"], include_all
            )
            counts = await asyncio.to_thread(
                database.job_status_counts, user["id"], include_all
            )
            await websocket.send_json(
                {
                    "jobs": [
                        {
                            "id": job["id"],
                            "status": job["status"],
                            "title": job["title"],
                            "video_ready": bool(
                                storage.available(job["video_path"])
                            ),
                            "media_ready": bool(
                                storage.available(job["video_path"])
                                and reusable_media_job(job)
                            ),
                            "updated_at": job["updated_at"],
                        }
                        for job in jobs
                    ],
                    "summary": {
                        "total": sum(counts.values()),
                        "active": sum(
                            counts.get(status, 0)
                            for status in ("queued", "processing", "publishing")
                        ),
                        "ready": sum(
                            counts.get(status, 0)
                            for status in (
                                "completed",
                                "published",
                                "waiting_for_connections",
                            )
                        ),
                        "failed": counts.get("failed", 0),
                    },
                }
            )
            await asyncio.sleep(1)
    except (WebSocketDisconnect, OSError, RuntimeError):
        pass


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if current_user(request):
        return RedirectResponse("/app", status_code=303)
    return TEMPLATES.TemplateResponse(
        request,
        "login.html",
        {
            "csrf": csrf_token(request),
            "google_enabled": google_enabled(),
            "sent": request.query_params.get("sent") == "1",
            "error": request.query_params.get("error", ""),
        },
    )


@app.post("/auth/email")
def email_login(request: Request, email: str = Form(), csrf: str = Form()):
    try:
        verify_csrf(request, csrf)
        clean_email = normalize_email(email)
    except ValueError as exc:
        return RedirectResponse(f"/login?error={str(exc)}", status_code=303)
    token = new_token()
    hashed_token = token_hash(token)
    created = database.create_magic_link(
        clean_email,
        hashed_token,
        request_ip_hash(request.client.host if request.client else None),
    )
    if created:
        try:
            send_magic_link(clean_email, token)
        except RuntimeError:
            database.discard_magic_link(hashed_token)
            return RedirectResponse(
                "/login?error=Sign-in+email+is+temporarily+unavailable", status_code=303
            )
    return RedirectResponse("/login?sent=1", status_code=303)


@app.get("/auth/email/verify")
def verify_email_login(request: Request, background_tasks: BackgroundTasks, token: str = ""):
    if not token:
        return RedirectResponse("/login?error=Invalid+sign-in+link", status_code=303)
    link = database.consume_magic_link(token_hash(token))
    if not link:
        return RedirectResponse("/login?error=Link+expired+or+already+used", status_code=303)
    user, created = database.get_or_create_user(
        email=link["email"], admin_email=os.getenv("ADMIN_EMAIL", "").strip(),
        return_created=True,
    )
    if user["status"] != "active":
        return RedirectResponse("/login?error=Account+suspended", status_code=303)
    request.session.clear()
    request.session["user_id"] = int(user["id"])
    csrf_token(request)
    if created:
        background_tasks.add_task(send_welcome_email, str(user["email"]))
    return RedirectResponse("/app", status_code=303)


@app.get("/auth/google")
def google_login(request: Request):
    try:
        state = new_token()
        request.session["google_oauth_state"] = state
        return RedirectResponse(google_authorization_url(state), status_code=303)
    except RuntimeError:
        return RedirectResponse("/login?error=Google+sign-in+is+not+configured", status_code=303)


@app.get("/auth/google/callback")
def google_callback(
    request: Request, background_tasks: BackgroundTasks, code: str = "", state: str = ""
):
    expected = str(request.session.pop("google_oauth_state", ""))
    if not expected or not hmac.compare_digest(expected, state):
        return RedirectResponse("/login?error=Invalid+Google+sign-in+state", status_code=303)
    try:
        profile = google_identity(code)
        user, created = database.get_or_create_user(
            email=profile["email"],
            name=profile["name"],
            avatar_url=profile["avatar_url"],
            admin_email=os.getenv("ADMIN_EMAIL", "").strip(),
            return_created=True,
        )
        database.link_identity(int(user["id"]), "google", profile["subject"])
    except RuntimeError:
        return RedirectResponse("/login?error=Google+sign-in+failed", status_code=303)
    if user["status"] != "active":
        return RedirectResponse("/login?error=Account+suspended", status_code=303)
    request.session.clear()
    request.session["user_id"] = int(user["id"])
    csrf_token(request)
    if created:
        background_tasks.add_task(send_welcome_email, str(user["email"]))
    return RedirectResponse("/app", status_code=303)


@app.post("/logout")
def logout(request: Request, csrf: str = Form()):
    verify_csrf(request, csrf)
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@app.get("/", response_class=HTMLResponse)
def landing(request: Request):
    base_url = public_base_url() or str(request.base_url).rstrip("/")
    testimonials = [dict(row) for row in database.list_public_testimonials(limit=3)]
    for testimonial in testimonials:
        testimonial["avatar_letter"] = str(
            testimonial["public_display_name"] or "S"
        )[0].upper()
        testimonial["is_example"] = False
    if len(testimonials) < 3:
        testimonials.extend(EXAMPLE_TESTIMONIALS[:3 - len(testimonials)])
    return TEMPLATES.TemplateResponse(
        request,
        "landing.html",
        {
            "current_user": current_user(request),
            "plans": PLANS.values(),
            "testimonials": testimonials,
            "landing_url": f"{base_url}/",
            "preview_url": f"{base_url}/social-preview.png",
        },
    )


@app.get("/pricing", response_class=HTMLResponse)
def pricing_page(request: Request):
    user = current_user(request)
    base_url = public_base_url() or str(request.base_url).rstrip("/")
    subscription = (
        database.subscription_for_user(int(user["id"]))
        if user and user["role"] == "creator" else None
    )
    return TEMPLATES.TemplateResponse(
        request,
        "pricing.html",
        {
            "current_user": user,
            "plans": PLANS.values(),
            "csrf": csrf_token(request) if user else "",
            "billing_configured": paystack.configured(),
            "subscription": subscription,
            "canonical_url": f"{base_url}/pricing",
            "preview_url": f"{base_url}/social-preview.png",
            "blocked_downgrades": {
                key for key in PAID_PLAN_KEYS
                if user and subscription and subscription["status"] == "active"
                and PLAN_RANK.get(key, 0) < PLAN_RANK.get(str(user["plan"]), 0)
            },
        },
    )


@app.get("/app", response_class=HTMLResponse)
def dashboard(request: Request):
    redirect = login_required(request)
    if redirect:
        return redirect
    user_id, include_all = user_scope(request)
    counts = database.job_status_counts(user_id, include_all)
    summary = {
        "total": sum(counts.values()),
        "active": sum(counts.get(status, 0) for status in ("queued", "processing", "publishing")),
        "failed": counts.get("failed", 0),
        "ready": sum(
            counts.get(status, 0)
            for status in ("completed", "published", "waiting_for_connections")
        ),
    }
    if include_all:
        customers = database.list_users()
        summary.update(
            customers=sum(row["role"] == "creator" for row in customers),
            active_customers=sum(
                row["role"] == "creator" and row["status"] == "active"
                for row in customers
            ),
            paid_revenue=database.confirmed_revenue_ngn(),
        )
    return TEMPLATES.TemplateResponse(
        request,
        "overview.html",
        page_context(
            request,
            "overview",
            summary=summary,
            jobs=database.list_jobs(limit=8, owner_id=user_id, include_all=include_all),
            media_items=reusable_media(request)[:4],
            connectors=[] if include_all else connector_statuses(user_id),
            storage_usage=None if include_all else creator_storage(current_user(request)),
        ),
    )


@app.get("/storytelling", response_class=HTMLResponse)
def storytelling_page(request: Request):
    redirect = creator_required(request)
    if redirect:
        return redirect
    user_id, include_all = user_scope(request)
    user = current_user(request)
    usage = database.monthly_job_usage(user_id)
    remaining = None if include_all else max(
        0, int(user["monthly_job_limit"]) - usage
    )
    return TEMPLATES.TemplateResponse(
        request,
        "storytelling.html",
        page_context(
            request,
            "storytelling",
            jobs=database.list_jobs(
                limit=8, kind="automatic", owner_id=user_id, include_all=include_all
            ),
            usage=usage,
            remaining=remaining,
            storage_usage=creator_storage(user),
            voice_options=VOICE_OPTIONS,
            voice_directions=VOICE_DIRECTIONS,
            prompt_starters=prompt_starters(str(user["creator_niche"])),
        ),
    )


@app.get("/showcase", response_class=HTMLResponse)
def showcase_page(request: Request):
    redirect = creator_required(request)
    if redirect:
        return redirect
    items = []
    for row in database.list_showcase_jobs():
        item = dict(row)
        item["thumbnail_ready"] = thumbnail_available(row)
        items.append(item)
    return TEMPLATES.TemplateResponse(
        request,
        "showcase.html",
        page_context(request, "showcase", showcase_items=items),
    )


@app.get("/showcase/{job_id}", response_class=HTMLResponse)
def showcase_detail(request: Request, job_id: int):
    redirect = creator_required(request)
    if redirect:
        return redirect
    job = database.get_showcase_job(job_id)
    if not job:
        return HTMLResponse("Story not found", status_code=404)
    return TEMPLATES.TemplateResponse(
        request,
        "showcase_detail.html",
        page_context(request, "showcase", job=job),
    )


@app.get("/showcase/{job_id}/video")
def showcase_video(request: Request, job_id: int):
    redirect = creator_required(request)
    if redirect:
        return redirect
    job = database.get_showcase_job(job_id)
    if not job:
        return HTMLResponse("Video not available", status_code=404)
    response = media_response(request, str(job["video_path"]), "video/mp4")
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Content-Disposition"] = "inline"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.get("/showcase/{job_id}/thumbnail")
def showcase_thumbnail(request: Request, job_id: int):
    redirect = creator_required(request)
    if redirect:
        return redirect
    job = database.get_showcase_job(job_id)
    thumbnail = thumbnail_reference(job)
    if not job or thumbnail is None or not storage.available(str(thumbnail)):
        return HTMLResponse("Thumbnail not available", status_code=404)
    return media_response(request, str(thumbnail), "image/jpeg")


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    redirect = creator_required(request)
    if redirect:
        return redirect
    user = current_user(request)
    subscription = database.subscription_for_user(int(user["id"]))
    if subscription and subscription["status"] == "active" and subscription["current_period_end"]:
        try:
            remaining = datetime.fromisoformat(str(subscription["current_period_end"])) - datetime.now(timezone.utc)
            if 0 <= remaining.days <= 7:
                database.add_notification_once(
                    int(user["id"]), "billing",
                    f"Your {str(subscription['plan']).title()} access ends on {str(subscription['current_period_end'])[:10]}. Renew from Pricing if you want uninterrupted access.",
                )
        except ValueError:
            pass
    return TEMPLATES.TemplateResponse(
        request,
        "settings.html",
        page_context(
            request,
            "settings",
            voice_options=VOICE_OPTIONS,
            voice_directions=VOICE_DIRECTIONS,
            current_plan=PLANS.get(user["plan"], PLANS["free"]),
            niche_options=NICHE_OPTIONS,
            content_styles=CONTENT_STYLES,
        ),
    )


@app.get("/subscription", response_class=HTMLResponse)
def subscription_page(request: Request):
    redirect = creator_required(request)
    if redirect:
        return redirect
    user = current_user(request)
    return TEMPLATES.TemplateResponse(
        request,
        "subscription.html",
        page_context(
            request,
            "subscription",
            current_plan=PLANS.get(user["plan"], PLANS["free"]),
            subscription=database.subscription_for_user(int(user["id"])),
            payments=database.payment_history(int(user["id"]), limit=20),
            storage_usage=creator_storage(user),
        ),
    )


@app.post("/settings")
def update_settings(
    request: Request,
    name: str = Form(""),
    channel_name: str = Form(""),
    creator_niche: str = Form(""),
    target_audience: str = Form(""),
    content_style: str = Form("cinematic"),
    creator_goal: str = Form(""),
    narration_voice: str = Form(),
    voice_direction: str = Form("neutral"),
    default_story_minutes: float = Form(),
    csrf: str = Form(),
):
    redirect = creator_required(request)
    if redirect:
        return redirect
    user = current_user(request)
    try:
        verify_csrf(request, csrf)
    except ValueError:
        return HTMLResponse("Invalid form token", status_code=400)
    if narration_voice not in VOICE_OPTIONS:
        return HTMLResponse("Choose a supported narration voice", status_code=400)
    if voice_direction not in VOICE_DIRECTIONS:
        return HTMLResponse("Choose a supported voice direction", status_code=400)
    if not creator_niche.strip() or len(creator_niche.strip()) > 120:
        return HTMLResponse("Enter a story type using 120 characters or fewer", status_code=400)
    if not content_style.strip() or len(content_style.strip()) > 120:
        return HTMLResponse("Enter a picture style using 120 characters or fewer", status_code=400)
    if not 0.5 <= default_story_minutes <= float(user["max_minutes_per_job"]):
        return HTMLResponse("Default duration exceeds your plan limit", status_code=400)
    database.update_creator_settings(
        int(user["id"]),
        name=name.strip()[:120],
        channel_name=channel_name.strip()[:120],
        creator_niche=creator_niche.strip(),
        target_audience=target_audience.strip()[:160],
        content_style=content_style.strip(),
        creator_goal=creator_goal.strip()[:300],
        narration_voice=narration_voice,
        voice_direction=voice_direction,
        default_story_minutes=default_story_minutes,
    )
    return RedirectResponse("/settings?saved=1", status_code=303)


def public_base_url() -> str:
    return os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")


@app.post("/billing/checkout/{plan}")
def billing_checkout(request: Request, plan: str, csrf: str = Form()):
    redirect = creator_required(request)
    if redirect:
        return redirect
    try:
        verify_csrf(request, csrf)
    except ValueError:
        return HTMLResponse("Invalid form token", status_code=400)
    if plan not in PAID_PLAN_KEYS:
        return HTMLResponse("Unknown subscription plan", status_code=404)
    if not paystack.configured():
        return HTMLResponse("Paystack billing is not configured.", status_code=503)
    user = current_user(request)
    selected_plan = PLANS[plan]
    subscription = database.subscription_for_user(int(user["id"]))
    if (
        subscription and subscription["status"] == "active"
        and subscription["current_period_end"]
        and str(subscription["current_period_end"]) > datetime.now(timezone.utc).isoformat(timespec="seconds")
        and PLAN_RANK.get(plan, 0) < PLAN_RANK.get(str(user["plan"]), 0)
    ):
        return HTMLResponse(
            "A lower plan can be selected after your current paid access ends. "
            "Your existing limits will not be reduced early.", status_code=409,
        )
    reference = f"SS-{user['id']}-{secrets.token_hex(8)}"
    base_url = public_base_url()
    if not base_url.startswith("https://") and not base_url.startswith("http://localhost"):
        return HTMLResponse("PUBLIC_BASE_URL must be configured for Paystack.", status_code=503)
    database.create_payment_attempt(
        reference=reference, user_id=int(user["id"]), plan=plan,
        amount_ngn=selected_plan.monthly_price_ngn,
    )
    try:
        url = paystack.initialize_transaction(
            user_id=int(user["id"]), email=str(user["email"]), plan=plan,
            amount_ngn=selected_plan.monthly_price_ngn, reference=reference,
            callback_url=f"{base_url}/billing/paystack/callback",
        )
    except RuntimeError as exc:
        database.fail_payment_attempt(reference, str(exc))
        return HTMLResponse(str(exc), status_code=503)
    return RedirectResponse(url, status_code=303)


@app.get("/billing/paystack/callback")
def paystack_callback(request: Request, reference: str = ""):
    redirect = creator_required(request)
    if redirect:
        return redirect
    try:
        data = paystack.verify_transaction(reference)
        if data.get("status") != "success" or str(data.get("reference")) != reference:
            raise RuntimeError("Paystack has not confirmed this payment.")
        metadata = data.get("metadata") or {}
        if str(metadata.get("user_id")) != str(current_user(request)["id"]):
            raise RuntimeError("This payment belongs to a different account.")
        activated = database.activate_paystack_payment(
            reference=reference,
            event_id=f"verify:{data.get('id') or reference}",
            event_type="transaction.verify",
            amount_kobo=int(data.get("amount") or 0),
            currency=str(data.get("currency") or ""),
            provider_transaction_id=str(data.get("id") or ""),
            customer_code=str((data.get("customer") or {}).get("customer_code") or ""),
        )
        if activated:
            database.add_notification(
                int(current_user(request)["id"]), "billing",
                f"Your {str(metadata.get('plan') or 'paid').title()} plan payment was confirmed.",
            )
    except (RuntimeError, ValueError) as exc:
        database.fail_payment_attempt(reference, str(exc))
        return RedirectResponse("/subscription?billing=failed", status_code=303)
    return RedirectResponse("/subscription?billing=success", status_code=303)


@app.post("/billing/webhook")
async def billing_webhook(request: Request):
    payload = await request.body()
    try:
        event = paystack.verify_webhook(
            payload, request.headers.get("x-paystack-signature", "")
        )
    except ValueError:
        return HTMLResponse("Invalid webhook signature", status_code=400)
    event_type = str(event.get("event") or "")
    data = event.get("data") or {}
    if event_type == "charge.dispute.create":
        affected_user = database.revoke_disputed_payment(str(data.get("reference") or ""))
        if affected_user:
            database.add_notification(
                affected_user, "billing",
                "Paid access was paused because Paystack reported a payment dispute. Contact support if this is unexpected.",
            )
        return {"received": True}
    metadata = data.get("metadata") or {}
    if metadata.get("type") != "SLEEP_STUDIO_SUBSCRIPTION":
        return {"received": True}
    reference = str(data.get("reference") or "")
    if event_type == "charge.failed":
        reason = str(
            data.get("gateway_response")
            or data.get("message")
            or "Payment was not completed."
        )
        database.fail_payment_attempt(reference, reason)
        return {"received": True}
    if event_type != "charge.success" or data.get("status") != "success":
        return {"received": True}
    activated = database.activate_paystack_payment(
        reference=reference,
        event_id=f"charge.success:{data.get('id') or reference}",
        event_type=event_type,
        amount_kobo=int(data.get("amount") or 0),
        currency=str(data.get("currency") or ""),
        provider_transaction_id=str(data.get("id") or ""),
        customer_code=str((data.get("customer") or {}).get("customer_code") or ""),
    )
    if activated:
        attempt = database.payment_attempt(reference)
        if attempt:
            database.add_notification(
                int(attempt["user_id"]), "billing",
                f"Your {str(attempt['plan']).title()} plan payment was confirmed.",
            )
    return {"received": True}


@app.get("/social", response_class=HTMLResponse)
def social_page(request: Request):
    redirect = creator_required(request)
    if redirect:
        return redirect
    return render_social_page(request, request.query_params.get("connection_error"))


@app.get("/connections/youtube")
def connect_youtube(request: Request):
    redirect = creator_required(request)
    if redirect:
        return redirect
    try:
        state = new_token()
        request.session["youtube_oauth_state"] = state
        return RedirectResponse(youtube_authorization_url(state), status_code=303)
    except RuntimeError:
        return RedirectResponse(
            "/social?connection_error=YouTube+connection+is+not+configured",
            status_code=303,
        )


@app.get("/connections/youtube/callback")
def youtube_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    error_description: str = "",
):
    redirect = creator_required(request)
    if redirect:
        return redirect
    expected = str(request.session.pop("youtube_oauth_state", ""))
    if not expected or not hmac.compare_digest(expected, state):
        return RedirectResponse(
            "/social?connection_error=Invalid+YouTube+connection+state",
            status_code=303,
        )
    if error:
        print(
            f"YouTube authorization was denied: {error} {error_description}".strip(),
            flush=True,
        )
        message = (
            "You cancelled the YouTube connection. Please try again and allow access."
            if error == "access_denied"
            else "Google did not approve the YouTube connection. Please try again."
        )
        return RedirectResponse(
            f"/social?connection_error={quote_plus(message)}", status_code=303
        )
    user_id = int(current_user(request)["id"])
    try:
        details = exchange_youtube_code(code)
        existing = database.get_social_connection(user_id, "youtube")
        refresh_token_encrypted = (
            encrypt_token(details["refresh_token"])
            if details["refresh_token"]
            else (existing["refresh_token_encrypted"] if existing else "")
        )
        if not refresh_token_encrypted:
            raise RuntimeError("Google did not return offline YouTube access")
        database.save_social_connection(
            user_id,
            "youtube",
            external_account_id=details["external_account_id"],
            account_name=details["account_name"],
            access_token_encrypted=encrypt_token(details["access_token"]),
            refresh_token_encrypted=refresh_token_encrypted,
            token_expires_at=details["token_expires_at"],
            scopes=details["scopes"],
        )
    except RuntimeError as exc:
        print(f"YouTube connection failed for user {user_id}: {exc}", flush=True)
        reason = str(exc).lower()
        if "does not have a youtube channel" in reason:
            message = "This Google account does not have a YouTube channel yet."
        elif "has not been used in project" in reason or "api has not been used" in reason or "accessnotconfigured" in reason:
            message = "YouTube Data API v3 is not enabled for this Google app."
        elif "redirect_uri_mismatch" in reason:
            message = "The YouTube callback address does not match the Google app settings."
        elif "invalid_grant" in reason:
            message = "The Google approval expired. Please connect YouTube again."
        elif "offline youtube access" in reason:
            message = "Google did not provide long-term YouTube access. Please connect again."
        else:
            message = "YouTube could not be connected. Please try again."
        return RedirectResponse(
            f"/social?connection_error={quote_plus(message)}", status_code=303
        )
    return RedirectResponse("/social", status_code=303)


@app.post("/connections/youtube/disconnect")
def disconnect_youtube(request: Request, csrf: str = Form()):
    redirect = creator_required(request)
    if redirect:
        return redirect
    try:
        verify_csrf(request, csrf)
    except ValueError:
        return HTMLResponse("Invalid form token", status_code=400)
    database.delete_social_connection(int(current_user(request)["id"]), "youtube")
    return RedirectResponse("/social", status_code=303)


@app.get("/jobs", response_class=HTMLResponse)
def jobs_page(request: Request):
    redirect = login_required(request)
    if redirect:
        return redirect
    workflow = request.query_params.get("workflow", "all")
    selected_status = request.query_params.get("status", "all")
    kind = {"storytelling": "automatic", "social": "manual"}.get(workflow)
    status = selected_status if selected_status in JOB_STATUSES else None
    if workflow not in {"all", "storytelling", "social"}:
        workflow = "all"
        kind = None
    user_id, include_all = user_scope(request)
    return TEMPLATES.TemplateResponse(
        request,
        "jobs.html",
        page_context(
            request,
            "jobs",
            jobs=database.list_jobs(
                kind=kind, status=status, owner_id=user_id, include_all=include_all
            ),
            workflow=workflow,
            selected_status=selected_status if status else "all",
            job_statuses=JOB_STATUSES,
        ),
    )


def selected_platforms(form) -> list[str]:
    return [name for name in PLATFORMS if form.get(name) == "on"]


def normalized_schedule(value: str) -> str | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


@app.post("/jobs/automatic")
async def automatic_job(request: Request):
    redirect = creator_required(request)
    if redirect:
        return redirect
    user = current_user(request)
    form = await request.form()
    try:
        verify_csrf(request, str(form.get("csrf", "")))
        topic = str(form.get("topic", "")).strip()
        minutes = float(str(form.get("minutes", "1")))
        scheduled_at = normalized_schedule(str(form.get("scheduled_at", "")))
        validate_creator_content(
            topic, str(form.get("title", "")), str(form.get("description", ""))
        )
    except ValueError:
        return HTMLResponse("Invalid story request", status_code=400)
    if minutes < 0.5 or (
        not is_admin(user) and minutes > float(user["max_minutes_per_job"])
    ):
        return HTMLResponse(
            f"Duration must be between 0.5 and {user['max_minutes_per_job']:g} minutes.",
            status_code=409,
        )
    storage_usage = creator_storage(user)
    if storage_usage["used"] >= storage_usage["limit"]:
        return HTMLResponse(
            "Your storage is full. Delete an old video or upgrade before creating another.",
            status_code=409,
        )
    job_id, limit_error = database.create_story_job(
        owner_id=int(user["id"]),
        topic=topic, minutes=minutes,
        title=str(form.get("title", "")).strip(),
        description=str(form.get("description", "")).strip(),
        hashtags=str(form.get("hashtags", "")), platforms=selected_platforms(form),
        scheduled_at=scheduled_at,
        active_limit=MAX_ACTIVE_CREATOR_JOBS,
    )
    if limit_error:
        return HTMLResponse(limit_error, status_code=409)
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@app.post("/media/upload")
async def upload_media(
    request: Request,
    video: UploadFile,
    title: str = Form(),
    description: str = Form(""),
    hashtags: str = Form(""),
    csrf: str = Form(),
):
    redirect = creator_required(request)
    if redirect:
        return redirect
    try:
        verify_csrf(request, csrf)
    except ValueError:
        return render_social_page(request, "Your form expired. Please try again.", 400)

    suffix = Path(video.filename or "").suffix.lower()
    if suffix not in {".mp4", ".mov", ".m4v"}:
        return render_social_page(request, "Upload an MP4, MOV, or M4V video.", 400)
    clean_title = title.strip()
    if not clean_title:
        return render_social_page(request, "A title is required for uploaded media.", 400)
    try:
        validate_creator_content(clean_title, description, hashtags)
    except ValueError as exc:
        return render_social_page(request, str(exc), 400)
    max_upload_bytes = int(os.getenv("MAX_UPLOAD_MB", "2048")) * 1024 * 1024
    if video.size is None:
        video.file.seek(0, 2)
        upload_size = video.file.tell()
        video.file.seek(0)
    else:
        upload_size = int(video.size)
    if upload_size > max_upload_bytes:
        return render_social_page(
            request, "The uploaded video exceeds the account upload limit.", 413
        )

    user = current_user(request)
    user_id = int(user["id"])
    sync_creator_storage(user_id)
    object_key = storage.creator_upload_key(
        user_id,
        f"{datetime.now(timezone.utc):%Y%m%d_%H%M%S}_{secrets.token_hex(8)}{suffix}",
    )
    media_reference = storage.object_reference(object_key)
    if not database.reserve_media_assets(
        user_id, [(media_reference, upload_size, "upload")]
    ):
        return render_social_page(
            request,
            "This upload would exceed your storage limit. Delete old media or upgrade your plan.",
            413,
        )
    try:
        if upload_size == 0:
            raise ValueError("The uploaded video is empty.")
        media_reference = await asyncio.to_thread(
            storage.upload_fileobj,
            video.file,
            object_key,
            video.content_type or "video/mp4",
        )
        media_id = database.create_uploaded_media(
            owner_id=user_id,
            title=clean_title,
            description=description.strip(),
            hashtags=hashtags.strip(),
            source_path=media_reference,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        database.remove_media_asset(media_reference)
        if media_reference:
            try:
                storage.delete_object(media_reference)
            except RuntimeError:
                pass
        return render_social_page(request, str(exc), 400)
    return RedirectResponse(f"/social?uploaded={media_id}", status_code=303)


@app.post("/social/posts")
async def create_social_post(request: Request):
    redirect = creator_required(request)
    if redirect:
        return redirect
    form = await request.form()
    try:
        verify_csrf(request, str(form.get("csrf", "")))
        media_job_id = int(str(form.get("media_job_id", "")))
    except (ValueError, TypeError):
        return render_social_page(request, "Choose a video from the media library.", 400)

    media_job, _ = owned_job(request, media_job_id)
    if not reusable_media_job(media_job):
        return render_social_page(request, "The selected media is not available.", 400)
    video_reference = str(media_job["video_path"])
    if not storage.available(video_reference):
        return render_social_page(request, "The selected video file is missing.", 400)

    platforms = [name for name in selected_platforms(form) if name == "youtube"]
    if not platforms:
        return render_social_page(request, "Connect YouTube and select it before publishing.", 400)
    youtube_privacy = str(form.get("youtube_privacy", "public")).strip().lower()
    if youtube_privacy not in {"public", "unlisted", "private"}:
        return render_social_page(request, "Choose a valid YouTube visibility.", 400)
    try:
        scheduled_at = normalized_schedule(str(form.get("scheduled_at", "")))
    except ValueError:
        return render_social_page(request, "Enter a valid publication date and time.", 400)

    title = str(form.get("title", "")).strip() or media_job["title"]
    description = (
        str(form.get("description", "")).strip() or media_job["description"]
    )
    hashtags = str(form.get("hashtags", "")).strip() or media_job["hashtags"]
    try:
        validate_creator_content(title, description, hashtags)
    except ValueError as exc:
        return render_social_page(request, str(exc), 400)
    owner_id = int(current_user(request)["id"])
    if media_job["kind"] == "automatic":
        if not database.queue_story_publication(
            job_id=media_job_id,
            owner_id=owner_id,
            title=title,
            description=description,
            hashtags=hashtags,
            platforms=platforms,
            scheduled_at=scheduled_at,
            youtube_privacy=youtube_privacy,
        ):
            return render_social_page(
                request,
                "This video is already busy. Wait for it to finish before publishing again.",
                409,
            )
        job_id = media_job_id
    else:
        job_id = database.create_job(
            owner_id=owner_id,
            kind="manual",
            title=title,
            description=description,
            hashtags=hashtags,
            platforms=platforms,
            scheduled_at=scheduled_at,
            source_path=video_reference,
            youtube_privacy=youtube_privacy,
        )
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@app.post("/jobs/manual")
async def manual_job(
    request: Request, video: UploadFile, title: str = Form(), description: str = Form(""),
    hashtags: str = Form(""), scheduled_at: str = Form(""), csrf: str = Form(),
):
    redirect = creator_required(request)
    if redirect:
        return redirect
    verify_csrf(request, csrf)
    form = await request.form()
    platforms = [name for name in selected_platforms(form) if name == "youtube"]
    validate_creator_content(title, description, hashtags)
    suffix = Path(video.filename or "").suffix.lower()
    if suffix not in {".mp4", ".mov", ".m4v"}:
        raise ValueError("Upload an MP4, MOV, or M4V video")
    max_upload_bytes = int(os.getenv("MAX_UPLOAD_MB", "2048")) * 1024 * 1024
    if video.size is not None and video.size > max_upload_bytes:
        return HTMLResponse("The uploaded video exceeds the account upload limit.", status_code=413)
    user_id = int(current_user(request)["id"])
    object_key = storage.creator_upload_key(
        user_id,
        f"{datetime.now(timezone.utc):%Y%m%d_%H%M%S}_{secrets.token_hex(8)}{suffix}",
    )
    source_reference = await asyncio.to_thread(
        storage.upload_fileobj,
        video.file,
        object_key,
        video.content_type or "video/mp4",
    )
    try:
        job_id = database.create_job(
            owner_id=user_id,
            kind="manual", title=title.strip(), description=description, hashtags=hashtags,
            platforms=platforms, scheduled_at=normalized_schedule(scheduled_at), source_path=source_reference,
        )
    except Exception:
        storage.delete_object(source_reference)
        raise
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: int):
    redirect = login_required(request)
    if redirect:
        return redirect
    job, publications = owned_job(request, job_id)
    if not job:
        return HTMLResponse("Job not found", status_code=404)
    return TEMPLATES.TemplateResponse(
        request,
        "job.html",
        page_context(
            request,
            "jobs",
            job=job,
            publications=publications,
            feedback=database.get_job_feedback(job_id),
            job_error_message=creator_job_error(job["error"]),
            admin_job_log=(
                job_log_excerpt(job) or (
                    "No saved pipeline log is available for this failed job. "
                    "The failure may have happened before logging started or the job was created by an older deployment."
                )
                if is_admin(current_user(request)) and job["status"] == "failed"
                else ""
            ),
            thumbnail_ready=thumbnail_available(job),
            shareable=shareable_job(job),
            share_url=(
                str(request.url_for("public_share", token=job["share_token"]))
                if job["share_token"]
                else None
            ),
        ),
    )


@app.post("/jobs/{job_id}/feedback")
def save_job_feedback(
    request: Request,
    job_id: int,
    rating: int = Form(),
    comment: str = Form(""),
    public_display_name: str = Form(""),
    public_consent: str = Form(""),
    csrf: str = Form(),
):
    redirect = creator_required(request)
    if redirect:
        return redirect
    verify_csrf(request, csrf)
    job, _ = owned_job(request, job_id)
    user = current_user(request)
    if not job or int(job["owner_id"]) != int(user["id"]):
        return HTMLResponse("Video not found", status_code=404)
    if job["status"] not in SHAREABLE_STATUSES:
        return HTMLResponse("You can rate a video after it is finished.", status_code=409)
    clean_comment = comment.strip()
    clean_name = public_display_name.strip()
    if rating not in range(1, 6):
        return HTMLResponse("Choose a rating from 1 to 5 stars.", status_code=400)
    if not clean_comment:
        return HTMLResponse("Write a short comment with your rating.", status_code=400)
    if len(clean_comment) > 600 or len(clean_name) > 60:
        return HTMLResponse("Your feedback or display name is too long.", status_code=400)
    database.save_job_feedback(
        job_id=job_id,
        user_id=int(user["id"]),
        rating=rating,
        comment=clean_comment,
        public_display_name=clean_name,
        public_consent=public_consent == "yes",
    )
    database.record_audit(
        int(user["id"]), "job.feedback_saved", "job", str(job_id),
        f"rating={rating}; public={public_consent == 'yes'}",
    )
    return RedirectResponse(f"/jobs/{job_id}?feedback=saved", status_code=303)


@app.post("/admin/jobs/{job_id}/feedback-approval")
def approve_job_feedback(
    request: Request,
    job_id: int,
    approved: str = Form("no"),
    csrf: str = Form(),
):
    redirect = admin_required(request)
    if redirect:
        return redirect
    verify_csrf(request, csrf)
    job, _ = owned_job(request, job_id)
    feedback = database.get_job_feedback(job_id)
    if not job or not feedback:
        return HTMLResponse("Feedback not found", status_code=404)
    is_approved = approved == "yes" and bool(feedback["public_consent"])
    database.set_job_feedback_approved(job_id, is_approved)
    administrator = current_user(request)
    database.record_audit(
        int(administrator["id"]), "job.feedback_moderated", "job", str(job_id),
        f"approved={is_approved}",
    )
    return RedirectResponse(f"/jobs/{job_id}?feedback=moderated", status_code=303)


@app.post("/jobs/{job_id}/share")
def create_share_link(request: Request, job_id: int, csrf: str = Form()):
    redirect = login_required(request)
    if redirect:
        return redirect
    verify_csrf(request, csrf)
    job, _ = owned_job(request, job_id)
    if not shareable_job(job):
        return HTMLResponse("Only finished videos can be shared", status_code=409)
    if not job["share_token"]:
        database.set_job_share_token(job_id, secrets.token_urlsafe(24))
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@app.post("/jobs/{job_id}/share/revoke")
def revoke_share_link(request: Request, job_id: int, csrf: str = Form()):
    redirect = login_required(request)
    if redirect:
        return redirect
    verify_csrf(request, csrf)
    job, _ = owned_job(request, job_id)
    if not job:
        return HTMLResponse("Job not found", status_code=404)
    database.set_job_share_token(job_id, None)
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@app.post("/jobs/{job_id}/showcase")
def set_showcase_visibility(
    request: Request, job_id: int, visible: str = Form("no"), csrf: str = Form()
):
    redirect = creator_required(request)
    if redirect:
        return redirect
    verify_csrf(request, csrf)
    job, _ = owned_job(request, job_id)
    if not shareable_job(job):
        return HTMLResponse("Only finished videos can be added to the Story Gallery", status_code=409)
    user = current_user(request)
    database.set_job_showcase_visibility(
        job_id, int(user["id"]), visible == "yes"
    )
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@app.get("/share/{token}", response_class=HTMLResponse, name="public_share")
def public_share(request: Request, token: str):
    job = database.get_job_by_share_token(token)
    if not shareable_job(job):
        return HTMLResponse("Shared video not found", status_code=404)
    return TEMPLATES.TemplateResponse(
        request,
        "share.html",
        {
            "job": job,
            "token": token,
            "thumbnail_ready": thumbnail_available(job),
        },
    )


@app.get("/share/{token}/video")
def public_share_video(request: Request, token: str):
    job = database.get_job_by_share_token(token)
    if not shareable_job(job):
        return HTMLResponse("Shared video not found", status_code=404)
    return media_response(request, str(job["video_path"]), "video/mp4")


@app.get("/share/{token}/thumbnail")
def public_share_thumbnail(request: Request, token: str):
    job = database.get_job_by_share_token(token)
    if not shareable_job(job):
        return HTMLResponse("Shared thumbnail not found", status_code=404)
    thumbnail = thumbnail_reference(job)
    if thumbnail is None:
        return HTMLResponse("Shared thumbnail not found", status_code=404)
    return media_response(request, str(thumbnail), "image/jpeg")


@app.get("/admin/customers", response_class=HTMLResponse)
def admin_customers(request: Request):
    redirect = admin_required(request)
    if redirect:
        return redirect
    query = request.query_params.get("q", "").strip().lower()
    customers = database.list_users()
    if query:
        customers = [row for row in customers if query in str(row["email"]).lower()
                     or query in str(row["name"]).lower()]
    return TEMPLATES.TemplateResponse(
        request,
        "admin_customers.html",
        page_context(
            request,
            "customers",
            customers=customers,
            plans=PLANS,
            query=query,
        ),
    )


@app.get("/admin/customers/{user_id}", response_class=HTMLResponse)
def admin_customer_detail(request: Request, user_id: int):
    redirect = admin_required(request)
    if redirect:
        return redirect
    customer = database.get_user(user_id)
    if not customer or customer["role"] != "creator":
        return HTMLResponse("Customer not found", status_code=404)
    customer_storage = creator_storage(customer)
    return TEMPLATES.TemplateResponse(
        request, "admin_customer_detail.html",
        page_context(
            request, "customers", customer=customer, plans=PLANS,
            jobs=database.list_jobs(limit=50, owner_id=user_id),
            payments=database.payment_history(user_id, limit=50),
            usage=database.monthly_job_usage(user_id),
            storage_usage=customer_storage,
            subscription=database.subscription_for_user(user_id),
        ),
    )


@app.get("/admin/payments", response_class=HTMLResponse)
def admin_payments(request: Request):
    redirect = admin_required(request)
    if redirect:
        return redirect
    return TEMPLATES.TemplateResponse(
        request, "admin_payments.html",
        page_context(request, "payments", payments=database.payment_history(limit=250)),
    )


@app.get("/admin/usage", response_class=HTMLResponse)
def admin_usage(request: Request):
    redirect = admin_required(request)
    if redirect:
        return redirect
    customers = database.list_users()
    for customer in customers:
        if customer["role"] == "creator":
            sync_creator_storage(int(customer["id"]))
    return TEMPLATES.TemplateResponse(
        request, "admin_usage.html",
        page_context(request, "usage", customers=database.list_users()),
    )


@app.get("/admin/audit", response_class=HTMLResponse)
def admin_audit(request: Request):
    redirect = admin_required(request)
    if redirect:
        return redirect
    return TEMPLATES.TemplateResponse(
        request, "admin_audit.html",
        page_context(request, "audit", audit_events=database.list_audit_events(limit=250)),
    )


@app.get("/notifications", response_class=HTMLResponse)
def notifications_page(request: Request):
    redirect = creator_required(request)
    if redirect:
        return redirect
    user = current_user(request)
    notices = database.list_notifications(int(user["id"]))
    database.mark_notifications_read(int(user["id"]))
    return TEMPLATES.TemplateResponse(
        request, "notifications.html",
        page_context(request, "notifications", notifications=notices),
    )


@app.get("/account/export")
def export_account(request: Request):
    redirect = creator_required(request)
    if redirect:
        return redirect
    user = current_user(request)
    payload = json.dumps(database.user_export(int(user["id"])), indent=2, default=str)
    return Response(
        payload, media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=my-automation-studio-export.json"},
    )


@app.post("/account/delete")
def delete_account(request: Request, confirmation: str = Form(), csrf: str = Form()):
    redirect = creator_required(request)
    if redirect:
        return redirect
    verify_csrf(request, csrf)
    user = current_user(request)
    if confirmation.strip().lower() != str(user["email"]).lower():
        return HTMLResponse("Enter your account email exactly to confirm deletion.", status_code=400)
    references = database.user_media_references(int(user["id"]))
    database.record_audit(int(user["id"]), "account.deleted", "user", str(user["id"]))
    database.delete_creator(int(user["id"]))
    request.session.clear()
    for reference in {value for value in references if storage.is_remote(value)}:
        try:
            storage.delete_object(reference)
            storage.delete_object(storage.thumbnail_reference(reference))
        except RuntimeError:
            pass
    return RedirectResponse("/?account=deleted", status_code=303)


LEGAL_PAGES = {
    "privacy": ("Privacy policy", "We collect account, job, billing and usage data needed to operate My Automation Studio. Creator media is stored in Cloudflare R2 and the application exposes it through authenticated workspace routes or creator-enabled share links. We do not sell personal information."),
    "terms": ("Terms of service", "Creators must own or have permission to use submitted material. Accounts may not be used for unlawful, abusive or rights-infringing content. Paid access lasts 30 days and provider availability is not guaranteed."),
    "acceptable-use": ("Acceptable use", "Do not submit sexual content involving minors, instructions for serious harm, praise for mass violence, impersonation, malware, fraud, or material that violates another person's rights."),
    "copyright": ("Copyright policy", "My Automation Studio requires original stories and does not permit copying or close paraphrasing of protected works. Rights holders may contact the operator with the work, URL and proof of authority."),
    "billing-policy": ("Billing policy", "Payments are processed by Paystack in naira. Access is purchased for 30 days and renews only when the creator starts another payment. Each plan includes a storage limit for finished videos, thumbnails and uploaded media. Reaching the limit blocks new media but does not automatically delete stored files. A lower tier takes effect only after current paid access ends."),
    "support": ("Support", "For account, billing, copyright or production help, contact the support address configured by the My Automation Studio operator."),
}


@app.get("/legal/{page}", response_class=HTMLResponse)
def legal_page(request: Request, page: str):
    content = LEGAL_PAGES.get(page)
    if not content:
        return HTMLResponse("Page not found", status_code=404)
    page_content = content[1]
    if page in {"support", "copyright"} and os.getenv("SUPPORT_EMAIL", "").strip():
        page_content += f" Contact: {os.getenv('SUPPORT_EMAIL', '').strip()}."
    base_url = public_base_url() or str(request.base_url).rstrip("/")
    return TEMPLATES.TemplateResponse(
        request, "legal.html", {"current_user": current_user(request),
                                "page_title": content[0], "page_content": page_content,
                                "canonical_url": f"{base_url}/legal/{page}"},
    )


@app.post("/admin/customers")
def admin_create_customer(
    request: Request,
    email: str = Form(),
    csrf: str = Form(),
):
    redirect = admin_required(request)
    if redirect:
        return redirect
    try:
        verify_csrf(request, csrf)
        clean_email = normalize_email(email)
    except ValueError:
        return HTMLResponse("Enter a valid customer email", status_code=400)
    _customer, created = database.create_creator(clean_email)
    database.record_audit(
        int(current_user(request)["id"]),
        "customer.onboarded" if created else "customer.onboard_attempted",
        "user", str(_customer["id"]), clean_email,
    )
    result = "created" if created else "exists"
    return RedirectResponse(f"/admin/customers?onboarded={result}", status_code=303)


@app.post("/admin/customers/{user_id}/limits")
def admin_update_customer(
    request: Request,
    user_id: int,
    plan: str = Form(),
    monthly_job_limit: int = Form(),
    max_minutes_per_job: float = Form(),
    max_images_per_job: int = Form(),
    storage_limit_gb: int = Form(),
    status: str = Form(),
    csrf: str = Form(),
):
    redirect = admin_required(request)
    if redirect:
        return redirect
    try:
        verify_csrf(request, csrf)
    except ValueError:
        return HTMLResponse("Invalid form token", status_code=400)
    if not 0 <= monthly_job_limit <= 10_000:
        return HTMLResponse("Monthly limit must be between 0 and 10,000", status_code=400)
    if not 0.5 <= max_minutes_per_job <= 600:
        return HTMLResponse("Duration limit must be between 0.5 and 600 minutes", status_code=400)
    if plan not in PLANS:
        return HTMLResponse("Invalid customer plan", status_code=400)
    if not 1 <= max_images_per_job <= 48:
        return HTMLResponse("Image limit must be between 1 and 48", status_code=400)
    if not 1 <= storage_limit_gb <= 10_000:
        return HTMLResponse("Storage limit must be between 1 and 10,000 GB", status_code=400)
    if status not in {"active", "suspended"}:
        return HTMLResponse("Invalid account status", status_code=400)
    database.update_user_limits(
        user_id,
        plan=plan,
        monthly_job_limit=monthly_job_limit,
        max_minutes_per_job=max_minutes_per_job,
        max_images_per_job=max_images_per_job,
        storage_limit_bytes=storage_limit_gb * 1024 ** 3,
        status=status,
    )
    database.record_audit(
        int(current_user(request)["id"]), "customer.limits_updated", "user",
        str(user_id), f"plan={plan}; status={status}; jobs={monthly_job_limit}; minutes={max_minutes_per_job}; images={max_images_per_job}; storage_gb={storage_limit_gb}",
    )
    return RedirectResponse(f"/admin/customers/{user_id}?saved=1", status_code=303)


@app.post("/admin/customers/{user_id}/subscription")
def admin_grant_subscription(
    request: Request,
    user_id: int,
    plan: str = Form(),
    days: int = Form(30),
    csrf: str = Form(),
):
    redirect = admin_required(request)
    if redirect:
        return redirect
    verify_csrf(request, csrf)
    if plan not in PAID_PLAN_KEYS:
        return HTMLResponse("Choose Basic, Pro, or Studio", status_code=400)
    if not 1 <= days <= 3650:
        return HTMLResponse("Subscription days must be between 1 and 3,650", status_code=400)
    reference = f"ADMIN-{user_id}-{secrets.token_hex(8)}"
    if not database.grant_admin_subscription(
        user_id=user_id, plan_key=plan, days=days, reference=reference
    ):
        return HTMLResponse("Customer not found", status_code=404)
    administrator = current_user(request)
    database.record_audit(
        int(administrator["id"]), "customer.subscription_granted", "user",
        str(user_id), f"plan={plan}; days={days}",
    )
    database.add_notification(
        user_id, "billing",
        f"An administrator added the {PLANS[plan].name} plan to your account for {days} days.",
    )
    return RedirectResponse(
        f"/admin/customers/{user_id}?subscribed=1", status_code=303
    )


@app.post("/jobs/{job_id}/retry")
def retry(request: Request, job_id: int, csrf: str = Form()):
    redirect = login_required(request)
    if redirect:
        return redirect
    verify_csrf(request, csrf)
    job, _ = owned_job(request, job_id)
    if not job:
        return HTMLResponse("Job not found", status_code=404)
    user = current_user(request)
    if user["role"] == "creator" and job["kind"] == "automatic" and job["status"] == "failed":
        if database.monthly_job_usage(int(user["id"])) >= int(user["monthly_job_limit"]):
            return HTMLResponse("You have used all videos included in your current plan.", status_code=409)
        storage_usage = creator_storage(user)
        if storage_usage["used"] >= storage_usage["limit"]:
            return HTMLResponse(
                "Your storage is full. Delete an old video or upgrade before retrying.",
                status_code=409,
            )
        if database.active_job_count(int(user["id"])) >= MAX_ACTIVE_CREATOR_JOBS:
            return HTMLResponse("Wait for your current video to finish before trying again.", status_code=409)
    database.retry_job(job_id)
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@app.post("/jobs/{job_id}/delete")
def delete_job(
    request: Request,
    job_id: int,
    csrf: str = Form(),
    return_to: str = Form("/jobs"),
):
    redirect = login_required(request)
    if redirect:
        return redirect
    verify_csrf(request, csrf)
    job, _ = owned_job(request, job_id)
    if not job:
        return HTMLResponse("Job not found", status_code=404)
    media_references = (job["video_path"], job["source_path"])
    database.request_job_deletion(job_id)
    delete_unreferenced_media(*media_references)
    safe_return = return_to if return_to in {"/app", "/storytelling", "/jobs"} else "/jobs"
    return RedirectResponse(safe_return, status_code=303)


@app.post("/jobs/delete-selected")
async def delete_selected_jobs(request: Request):
    redirect = login_required(request)
    if redirect:
        return redirect
    form = await request.form()
    verify_csrf(request, str(form.get("csrf", "")))
    job_ids = []
    media_references = []
    for raw_id in form.getlist("job_ids"):
        try:
            job_id = int(str(raw_id))
        except (TypeError, ValueError):
            continue
        job, _ = owned_job(request, job_id)
        if job_id > 0 and job:
            job_ids.append(job_id)
            media_references.extend((job["video_path"], job["source_path"]))
    database.request_jobs_deletion(job_ids)
    delete_unreferenced_media(*media_references)
    return_to = str(form.get("return_to", "/jobs"))
    safe_return = return_to if return_to in {"/app", "/storytelling", "/jobs"} else "/jobs"
    return RedirectResponse(safe_return, status_code=303)


@app.get("/jobs/{job_id}/video")
def job_video(request: Request, job_id: int):
    redirect = login_required(request)
    if redirect:
        return redirect
    job, _ = owned_job(request, job_id)
    if not job or not job["video_path"]:
        return HTMLResponse("Video not available", status_code=404)
    return media_response(request, str(job["video_path"]), "video/mp4")


@app.get("/jobs/{job_id}/thumbnail")
def job_thumbnail(request: Request, job_id: int):
    redirect = login_required(request)
    if redirect:
        return redirect
    job, _ = owned_job(request, job_id)
    if not job or not job["video_path"]:
        return HTMLResponse("Thumbnail not available", status_code=404)
    thumbnail = thumbnail_reference(job)
    if thumbnail is None:
        return HTMLResponse("Thumbnail not available", status_code=404)
    return media_response(request, str(thumbnail), "image/jpeg")
