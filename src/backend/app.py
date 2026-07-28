import asyncio
import hmac
import json
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Form, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from project_paths import THUMBNAILS_DIR

from . import database
from . import storage
from .auth import (
    google_authorization_url,
    google_enabled,
    google_identity,
    new_token,
    normalize_email,
    request_ip_hash,
    send_magic_link,
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


WEB_DIR = Path(__file__).resolve().parents[1] / "web"
TEMPLATES = Jinja2Templates(directory=WEB_DIR / "templates")
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.initialize()
    stop_event, worker = start_worker()
    yield
    stop_event.set()
    worker.join(timeout=5)


app = FastAPI(title="Sleep Studio", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("ADMIN_SESSION_SECRET", secrets.token_hex(32)),
    https_only=os.getenv("COOKIE_SECURE", "false").lower() == "true",
    same_site="lax",
)
app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")


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
    return {
        "active_section": section,
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
        try:
            storage.delete_object(reference)
            storage.delete_object(storage.thumbnail_reference(reference))
        except RuntimeError:
            pass


def render_social_page(
    request: Request, error: str | None = None, status_code: int = 200
):
    return TEMPLATES.TemplateResponse(
        request,
        "social.html",
        page_context(
            request,
            "social",
            media_items=reusable_media(request),
            connectors=connector_statuses(int(current_user(request)["id"])),
            platforms=PLATFORMS,
            error=error,
        ),
        status_code=status_code,
    )


@app.get("/health")
def health():
    return {"status": "ok"}


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
def verify_email_login(request: Request, token: str = ""):
    if not token:
        return RedirectResponse("/login?error=Invalid+sign-in+link", status_code=303)
    link = database.consume_magic_link(token_hash(token))
    if not link:
        return RedirectResponse("/login?error=Link+expired+or+already+used", status_code=303)
    user = database.get_or_create_user(
        email=link["email"], admin_email=os.getenv("ADMIN_EMAIL", "").strip()
    )
    if user["status"] != "active":
        return RedirectResponse("/login?error=Account+suspended", status_code=303)
    request.session.clear()
    request.session["user_id"] = int(user["id"])
    csrf_token(request)
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
def google_callback(request: Request, code: str = "", state: str = ""):
    expected = str(request.session.pop("google_oauth_state", ""))
    if not expected or not hmac.compare_digest(expected, state):
        return RedirectResponse("/login?error=Invalid+Google+sign-in+state", status_code=303)
    try:
        profile = google_identity(code)
        user = database.get_or_create_user(
            email=profile["email"],
            name=profile["name"],
            avatar_url=profile["avatar_url"],
            admin_email=os.getenv("ADMIN_EMAIL", "").strip(),
        )
        database.link_identity(int(user["id"]), "google", profile["subject"])
    except RuntimeError:
        return RedirectResponse("/login?error=Google+sign-in+failed", status_code=303)
    if user["status"] != "active":
        return RedirectResponse("/login?error=Account+suspended", status_code=303)
    request.session.clear()
    request.session["user_id"] = int(user["id"])
    csrf_token(request)
    return RedirectResponse("/app", status_code=303)


@app.post("/logout")
def logout(request: Request, csrf: str = Form()):
    verify_csrf(request, csrf)
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@app.get("/", response_class=HTMLResponse)
def landing(request: Request):
    return TEMPLATES.TemplateResponse(
        request,
        "landing.html",
        {"current_user": current_user(request)},
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
        ),
    )


@app.get("/ambient", response_class=HTMLResponse)
def ambient_page(request: Request):
    redirect = creator_required(request)
    if redirect:
        return redirect
    return TEMPLATES.TemplateResponse(
        request,
        "ambient.html",
        page_context(request, "ambient"),
    )


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
def youtube_callback(request: Request, code: str = "", state: str = ""):
    redirect = creator_required(request)
    if redirect:
        return redirect
    expected = str(request.session.pop("youtube_oauth_state", ""))
    if not expected or not hmac.compare_digest(expected, state):
        return RedirectResponse(
            "/social?connection_error=Invalid+YouTube+connection+state",
            status_code=303,
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
    except RuntimeError:
        return RedirectResponse(
            "/social?connection_error=YouTube+could+not+be+connected",
            status_code=303,
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
    except ValueError:
        return HTMLResponse("Invalid story request", status_code=400)
    if minutes < 0.5 or (
        not is_admin(user) and minutes > float(user["max_minutes_per_job"])
    ):
        return HTMLResponse(
            f"Duration must be between 0.5 and {user['max_minutes_per_job']:g} minutes.",
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

    user_id = int(current_user(request)["id"])
    object_key = (
        f"sleep-studio/creators/{user_id}/uploads/"
        f"{datetime.now(timezone.utc):%Y%m%d_%H%M%S}_{secrets.token_hex(8)}{suffix}"
    )
    media_reference = None
    try:
        if video.size == 0:
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

    platforms = selected_platforms(form)
    if not platforms:
        return render_social_page(request, "Select at least one publishing platform.", 400)
    try:
        scheduled_at = normalized_schedule(str(form.get("scheduled_at", "")))
    except ValueError:
        return render_social_page(request, "Enter a valid publication date and time.", 400)

    title = str(form.get("title", "")).strip() or media_job["title"]
    description = (
        str(form.get("description", "")).strip() or media_job["description"]
    )
    hashtags = str(form.get("hashtags", "")).strip() or media_job["hashtags"]
    job_id = database.create_job(
        owner_id=int(current_user(request)["id"]),
        kind="manual",
        title=title,
        description=description,
        hashtags=hashtags,
        platforms=platforms,
        scheduled_at=scheduled_at,
        source_path=video_reference,
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
    platforms = selected_platforms(form)
    suffix = Path(video.filename or "").suffix.lower()
    if suffix not in {".mp4", ".mov", ".m4v"}:
        raise ValueError("Upload an MP4, MOV, or M4V video")
    user_id = int(current_user(request)["id"])
    object_key = f"sleep-studio/creators/{user_id}/uploads/{datetime.now(timezone.utc):%Y%m%d_%H%M%S}_{secrets.token_hex(8)}{suffix}"
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
            thumbnail_ready=thumbnail_available(job),
            shareable=shareable_job(job),
            share_url=(
                str(request.url_for("public_share", token=job["share_token"]))
                if job["share_token"]
                else None
            ),
        ),
    )


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
    return TEMPLATES.TemplateResponse(
        request,
        "admin_customers.html",
        page_context(
            request,
            "customers",
            customers=database.list_users(),
        ),
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
    result = "created" if created else "exists"
    return RedirectResponse(f"/admin/customers?onboarded={result}", status_code=303)


@app.post("/admin/customers/{user_id}/limits")
def admin_update_customer(
    request: Request,
    user_id: int,
    monthly_job_limit: int = Form(),
    max_minutes_per_job: float = Form(),
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
    if status not in {"active", "suspended"}:
        return HTMLResponse("Invalid account status", status_code=400)
    database.update_user_limits(
        user_id,
        monthly_job_limit=monthly_job_limit,
        max_minutes_per_job=max_minutes_per_job,
        status=status,
    )
    return RedirectResponse("/admin/customers", status_code=303)


@app.post("/jobs/{job_id}/retry")
def retry(request: Request, job_id: int, csrf: str = Form()):
    redirect = login_required(request)
    if redirect:
        return redirect
    verify_csrf(request, csrf)
    job, _ = owned_job(request, job_id)
    if not job:
        return HTMLResponse("Job not found", status_code=404)
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
