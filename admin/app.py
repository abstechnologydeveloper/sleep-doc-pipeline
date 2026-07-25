import hashlib
import hmac
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import database
from .publishers import PLATFORMS, connector_statuses
from .worker import start_worker


BASE_DIR = Path(__file__).resolve().parents[1]
UPLOAD_DIR = BASE_DIR / "data" / "uploads"
TEMPLATES = Jinja2Templates(directory=BASE_DIR / "admin" / "templates")
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD_SALT = bytes.fromhex("438e1e08c2debc9865471dfbcfa3b952")
ADMIN_PASSWORD_HASH = bytes.fromhex("23268369118539671454ca6b90ccf3f52a38e2ec1475ca78f2f9b35f81190adc")


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
app.mount("/static", StaticFiles(directory=BASE_DIR / "admin" / "static"), name="static")


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


@app.get("/health")
def health():
    return {"status": "ok"}


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
    return TEMPLATES.TemplateResponse(
        request, "dashboard.html",
        {"jobs": database.list_jobs(), "connectors": connector_statuses(), "platforms": PLATFORMS, "csrf": csrf_token(request)},
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
    if not topic or minutes <= 0:
        raise ValueError("Topic and positive duration are required")
    job_id = database.create_job(
        kind="automatic", topic=topic, minutes=minutes,
        title=str(form.get("title") or topic), description=str(form.get("description", "")),
        hashtags=str(form.get("hashtags", "")), platforms=selected_platforms(form),
        scheduled_at=normalized_schedule(str(form.get("scheduled_at", ""))),
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
    return TEMPLATES.TemplateResponse(
        request, "job.html", {"job": job, "publications": publications, "csrf": csrf_token(request)}
    )


@app.post("/jobs/{job_id}/retry")
def retry(request: Request, job_id: int, csrf: str = Form()):
    redirect = login_required(request)
    if redirect:
        return redirect
    verify_csrf(request, csrf)
    database.retry_job(job_id)
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@app.get("/jobs/{job_id}/video")
def job_video(request: Request, job_id: int):
    redirect = login_required(request)
    if redirect:
        return redirect
    job, _ = database.get_job(job_id)
    if not job or not job["video_path"] or not Path(job["video_path"]).is_file():
        return HTMLResponse("Video not available", status_code=404)
    return FileResponse(job["video_path"], media_type="video/mp4")
