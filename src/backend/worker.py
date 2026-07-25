import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from project_paths import DATA_DIR, PROJECT_ROOT, VIDEOS_DIR

from . import database
from .publishers import publish


BASE_DIR = PROJECT_ROOT
LOG_DIR = DATA_DIR / "logs"


class JobCancelled(Exception):
    pass


def newest_video(before: set[Path]) -> Path:
    candidates = [p for p in VIDEOS_DIR.glob("*.mp4") if p not in before]
    if not candidates:
        raise RuntimeError("Pipeline completed without producing a new video")
    return max(candidates, key=lambda path: path.stat().st_mtime_ns)


def run_automatic(job, log_file) -> Path:
    existing = set(VIDEOS_DIR.glob("*.mp4"))
    process = subprocess.Popen(
        [sys.executable, str(BASE_DIR / "run_pipeline.py"), job["topic"], str(job["minutes"])],
        cwd=BASE_DIR, stdout=log_file, stderr=subprocess.STDOUT, start_new_session=True,
    )
    while process.poll() is None:
        if database.cancellation_requested(job["id"]):
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=10)
            except ProcessLookupError:
                pass
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise JobCancelled
        time.sleep(1)
    if process.returncode:
        raise subprocess.CalledProcessError(process.returncode, process.args)
    return newest_video(existing)


def process_job(job) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"job_{job['id']}.log"
    database.update_job(job["id"], "processing", log_path=str(log_path))
    try:
        with log_path.open("a", encoding="utf-8") as log_file:
            if job["kind"] == "automatic":
                video_path = run_automatic(job, log_file)
            else:
                video_path = Path(job["source_path"])
            if database.cancellation_requested(job["id"]):
                raise JobCancelled
            if not video_path.is_file():
                raise RuntimeError(f"Video file does not exist: {video_path}")

        platforms = json.loads(job["platforms"])
        if not platforms:
            if database.cancellation_requested(job["id"]):
                raise JobCancelled
            database.update_job(job["id"], "completed", video_path=str(video_path))
            return

        database.update_job(job["id"], "publishing", video_path=str(video_path))
        metadata = dict(title=job["title"], description=job["description"], hashtags=job["hashtags"])
        waiting = False
        for platform in platforms:
            if database.cancellation_requested(job["id"]):
                raise JobCancelled
            try:
                result = publish(platform, video_path, metadata)
                database.update_publication(job["id"], platform, "published", **result)
            except RuntimeError as exc:
                waiting = True
                database.update_publication(job["id"], platform, "waiting", error=str(exc))
        if database.cancellation_requested(job["id"]):
            raise JobCancelled
        database.update_job(job["id"], "waiting_for_connections" if waiting else "published")
    except JobCancelled:
        database.delete_job(job["id"])
    except (OSError, subprocess.CalledProcessError, RuntimeError) as exc:
        database.update_job(job["id"], "failed", error=str(exc))


def worker_loop(stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        job = database.claim_job()
        if job:
            process_job(job)
        else:
            stop_event.wait(2)


def start_worker() -> tuple[threading.Event, threading.Thread]:
    stop_event = threading.Event()
    thread = threading.Thread(target=worker_loop, args=(stop_event,), daemon=True)
    thread.start()
    return stop_event, thread
