import asyncio
import hashlib
import hmac
import json
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Form, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from project_paths import DATA_DIR, THUMBNAILS_DIR

from . import database
from .publishers import PLATFORMS, connector_statuses
from .worker import start_worker


WEB_DIR = Path(__file__).resolve().parents[1] / "web"
UPLOAD_DIR = DATA_DIR / "uploads"
TEMPLATES = Jinja2Templates(directory=WEB_DIR / "templates")
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD_SALT = bytes.fromhex("438e1e08c2debc9865471dfbcfa3b952")
ADMIN_PASSWORD_HASH = bytes.fromhex("23268369118539671454ca6b90ccf3f52a38e2ec1475ca78f2f9b35f81190adc")
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


def valid_admin_password(password: str) -> bool:
    candidate = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), ADMIN_PASSWORD_SALT, 600_000
    )
    return hmac.compare_digest(candidate, ADMIN_PASSWORD_HASH)


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


def authenticated(request: Request) -> bool:
    return request.session.get("authenticated") is True


def csrf_token(request: Request) -> str:
    return request.session.setdefault("csrf", secrets.token_urlsafe(24))


def verify_csrf(request: Request, submitted: str) -> None:
    if not hmac.compare_digest(request.session.get("csrf", ""), submitted):
        raise ValueError("Invalid form token")


def login_required(request: Request):
    if not authenticated(request):
        return RedirectResponse("/login", status_code=303)
    return None


def page_context(request: Request, section: str, **values) -> dict:
    return {
        "active_section": section,
        "csrf": csrf_token(request),
        **values,
    }


def reusable_media() -> list[dict]:
    items = []
    for row in database.list_media_jobs():
        video_path = Path(row["video_path"])
        if not video_path.is_file():
            continue
        thumbnail_path = THUMBNAILS_DIR / f"{video_path.stem}.jpg"
        item = dict(row)
        item["thumbnail_ready"] = thumbnail_path.is_file()
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


def render_social_page(
    request: Request, error: str | None = None, status_code: int = 200
):
    return TEMPLATES.TemplateResponse(
        request,
        "social.html",
        page_context(
            request,
            "social",
            media_items=reusable_media(),
            connectors=connector_statuses(),
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
    if websocket.session.get("authenticated") is not True:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    try:
        while True:
            jobs = await asyncio.to_thread(database.list_job_statuses)
            counts = await asyncio.to_thread(database.job_status_counts)
            await websocket.send_json(
                {
                    "jobs": [
                        {
                            "id": job["id"],
                            "status": job["status"],
                            "title": job["title"],
                            "video_ready": bool(
                                job["video_path"] and Path(job["video_path"]).is_file()
                            ),
                            "media_ready": bool(
                                job["video_path"]
                                and Path(job["video_path"]).is_file()
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
    return TEMPLATES.TemplateResponse(request, "login.html", {"csrf": csrf_token(request)})


@app.post("/login")
def login(request: Request, username: str = Form(), password: str = Form(), csrf: str = Form()):
    try:
        verify_csrf(request, csrf)
    except ValueError:
        return RedirectResponse("/login", status_code=303)
    valid_user = hmac.compare_digest(username, ADMIN_USERNAME)
    valid_password = valid_admin_password(password)
    if not valid_user or not valid_password:
        return TEMPLATES.TemplateResponse(
            request, "login.html", {"csrf": csrf_token(request), "error": "Invalid credentials"}, status_code=401
        )
    request.session.clear()
    request.session["authenticated"] = True
    csrf_token(request)
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
def logout(request: Request, csrf: str = Form()):
    verify_csrf(request, csrf)
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    redirect = login_required(request)
    if redirect:
        return redirect
    counts = database.job_status_counts()
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
            jobs=database.list_jobs(limit=8),
            media_items=reusable_media()[:4],
            connectors=connector_statuses(),
        ),
    )


@app.get("/storytelling", response_class=HTMLResponse)
def storytelling_page(request: Request):
    redirect = login_required(request)
    if redirect:
        return redirect
    return TEMPLATES.TemplateResponse(
        request,
        "storytelling.html",
        page_context(
            request,
            "storytelling",
            jobs=database.list_jobs(limit=8, kind="automatic"),
        ),
    )


@app.get("/ambient", response_class=HTMLResponse)
def ambient_page(request: Request):
    redirect = login_required(request)
    if redirect:
        return redirect
    return TEMPLATES.TemplateResponse(
        request,
        "ambient.html",
        page_context(request, "ambient"),
    )


@app.get("/social", response_class=HTMLResponse)
def social_page(request: Request):
    redirect = login_required(request)
    if redirect:
        return redirect
    return render_social_page(request)


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
    return TEMPLATES.TemplateResponse(
        request,
        "jobs.html",
        page_context(
            request,
            "jobs",
            jobs=database.list_jobs(kind=kind, status=status),
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
    redirect = login_required(request)
    if redirect:
        return redirect
    form = await request.form()
    verify_csrf(request, str(form.get("csrf", "")))
    topic = str(form.get("topic", "")).strip()
    minutes = float(str(form.get("minutes", "1")))
    if minutes <= 0:
        raise ValueError("A positive duration is required")
    job_id = database.create_job(
        kind="automatic", topic=topic, minutes=minutes,
        title=str(form.get("title", "")).strip(),
        description=str(form.get("description", "")).strip(),
        hashtags=str(form.get("hashtags", "")), platforms=selected_platforms(form),
        scheduled_at=normalized_schedule(str(form.get("scheduled_at", ""))),
    )
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
    redirect = login_required(request)
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

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    destination = (
        UPLOAD_DIR
        / f"{datetime.now(timezone.utc):%Y%m%d_%H%M%S}_{secrets.token_hex(4)}{suffix}"
    )
    try:
        with destination.open("wb") as output:
            while chunk := await video.read(1024 * 1024):
                output.write(chunk)
        if destination.stat().st_size == 0:
            raise ValueError("The uploaded video is empty.")
        media_id = database.create_uploaded_media(
            title=clean_title,
            description=description.strip(),
            hashtags=hashtags.strip(),
            source_path=str(destination),
        )
    except (OSError, ValueError) as exc:
        destination.unlink(missing_ok=True)
        return render_social_page(request, str(exc), 400)
    return RedirectResponse(f"/social?uploaded={media_id}", status_code=303)


@app.post("/social/posts")
async def create_social_post(request: Request):
    redirect = login_required(request)
    if redirect:
        return redirect
    form = await request.form()
    try:
        verify_csrf(request, str(form.get("csrf", "")))
        media_job_id = int(str(form.get("media_job_id", "")))
    except (ValueError, TypeError):
        return render_social_page(request, "Choose a video from the media library.", 400)

    media_job, _ = database.get_job(media_job_id)
    if not reusable_media_job(media_job):
        return render_social_page(request, "The selected media is not available.", 400)
    video_path = Path(media_job["video_path"])
    if not video_path.is_file():
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
        kind="manual",
        title=title,
        description=description,
        hashtags=hashtags,
        platforms=platforms,
        scheduled_at=scheduled_at,
        source_path=str(video_path),
    )
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@app.post("/jobs/manual")
async def manual_job(
    request: Request, video: UploadFile, title: str = Form(), description: str = Form(""),
    hashtags: str = Form(""), scheduled_at: str = Form(""), csrf: str = Form(),
):
    redirect = login_required(request)
    if redirect:
        return redirect
    verify_csrf(request, csrf)
    form = await request.form()
    platforms = selected_platforms(form)
    suffix = Path(video.filename or "").suffix.lower()
    if suffix not in {".mp4", ".mov", ".m4v"}:
        raise ValueError("Upload an MP4, MOV, or M4V video")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    destination = UPLOAD_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_{secrets.token_hex(4)}{suffix}"
    with destination.open("wb") as output:
        while chunk := await video.read(1024 * 1024):
            output.write(chunk)
    job_id = database.create_job(
        kind="manual", title=title.strip(), description=description, hashtags=hashtags,
        platforms=platforms, scheduled_at=normalized_schedule(scheduled_at), source_path=str(destination),
    )
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: int):
    redirect = login_required(request)
    if redirect:
        return redirect
    job, publications = database.get_job(job_id)
    if not job:
        return HTMLResponse("Job not found", status_code=404)
    thumbnail_path = (
        THUMBNAILS_DIR / f"{Path(job['video_path']).stem}.jpg"
        if job["video_path"]
        else None
    )
    return TEMPLATES.TemplateResponse(
        request,
        "job.html",
        page_context(
            request,
            "jobs",
            job=job,
            publications=publications,
            thumbnail_ready=bool(thumbnail_path and thumbnail_path.is_file()),
        ),
    )


@app.post("/jobs/{job_id}/retry")
def retry(request: Request, job_id: int, csrf: str = Form()):
    redirect = login_required(request)
    if redirect:
        return redirect
    verify_csrf(request, csrf)
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
    database.request_job_deletion(job_id)
    safe_return = return_to if return_to in {"/", "/storytelling", "/jobs"} else "/jobs"
    return RedirectResponse(safe_return, status_code=303)


@app.post("/jobs/delete-selected")
async def delete_selected_jobs(request: Request):
    redirect = login_required(request)
    if redirect:
        return redirect
    form = await request.form()
    verify_csrf(request, str(form.get("csrf", "")))
    job_ids = []
    for raw_id in form.getlist("job_ids"):
        try:
            job_id = int(str(raw_id))
        except (TypeError, ValueError):
            continue
        if job_id > 0:
            job_ids.append(job_id)
    database.request_jobs_deletion(job_ids)
    return_to = str(form.get("return_to", "/jobs"))
    safe_return = return_to if return_to in {"/", "/storytelling", "/jobs"} else "/jobs"
    return RedirectResponse(safe_return, status_code=303)


@app.get("/jobs/{job_id}/video")
def job_video(request: Request, job_id: int):
    redirect = login_required(request)
    if redirect:
        return redirect
    job, _ = database.get_job(job_id)
    if not job or not job["video_path"] or not Path(job["video_path"]).is_file():
        return HTMLResponse("Video not available", status_code=404)
    return FileResponse(job["video_path"], media_type="video/mp4")


@app.get("/jobs/{job_id}/thumbnail")
def job_thumbnail(request: Request, job_id: int):
    redirect = login_required(request)
    if redirect:
        return redirect
    job, _ = database.get_job(job_id)
    if not job or not job["video_path"]:
        return HTMLResponse("Thumbnail not available", status_code=404)
    thumbnail_path = THUMBNAILS_DIR / f"{Path(job['video_path']).stem}.jpg"
    if not thumbnail_path.is_file():
        return HTMLResponse("Thumbnail not available", status_code=404)
    return FileResponse(thumbnail_path, media_type="image/jpeg")
