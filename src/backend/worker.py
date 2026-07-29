import json
import os
import signal
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from contextlib import ExitStack

from project_paths import (
    AUDIO_DIR,
    DATA_DIR,
    IMAGES_DIR,
    PROJECT_ROOT,
    SCRIPTS_DIR,
    SOUNDS_DIR,
    THUMBNAILS_DIR,
    VIDEOS_DIR,
)
from pipeline.script import safe_topic_slug

from . import database, storage
from .content import generate_post_metadata
from .publishers import publish


BASE_DIR = PROJECT_ROOT
LOG_DIR = DATA_DIR / "logs"


class JobCancelled(Exception):
    pass


def notify_creator(user_id: int | None, message: str) -> None:
    if not user_id:
        return
    try:
        database.add_notification(int(user_id), "job", message)
    except Exception:
        # Notifications are secondary and must not change the job result.
        pass


def newest_video(before: set[Path]) -> Path:
    candidates = [p for p in VIDEOS_DIR.glob("*.mp4") if p not in before]
    if not candidates:
        raise RuntimeError("Pipeline completed without producing a new video")
    return max(candidates, key=lambda path: path.stat().st_mtime_ns)


def resumable_script(job) -> Path | None:
    configured = job.get("script_path")
    if configured:
        script_path = Path(configured)
        if script_path.is_file():
            return script_path.resolve()

    if not job.get("log_path") or not job.get("topic"):
        return None
    slug = safe_topic_slug(job["topic"])
    candidates = [
        path for path in SCRIPTS_DIR.glob(f"*_{slug}.txt") if path.is_file()
    ]
    return (
        max(candidates, key=lambda path: path.stat().st_mtime_ns).resolve()
        if candidates
        else None
    )


def run_automatic(job, log_file) -> Path:
    existing_videos = set(VIDEOS_DIR.glob("*.mp4"))
    existing_scripts = set(SCRIPTS_DIR.glob("*.txt"))
    script_path = resumable_script(job)
    command = [sys.executable, str(BASE_DIR / "run_pipeline.py")]
    if script_path:
        print(f"Resuming saved script: {script_path.name}", file=log_file, flush=True)
        command.extend(["--resume-script", str(script_path)])
    else:
        command.extend([job["topic"], str(job["minutes"])])
    command.extend([
        "--title", job["title"],
        "--voice", job.get("narration_voice") or "Kore",
        "--voice-direction", job.get("voice_direction") or "neutral",
        "--max-images", str(job.get("max_images") or 8),
        "--niche", job.get("creator_niche") or "",
        "--audience", job.get("target_audience") or "",
        "--content-style", job.get("content_style") or "cinematic",
    ])
    process = subprocess.Popen(
        command,
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
            cancelled_script = script_path
            if cancelled_script is None:
                new_scripts = [
                    path for path in SCRIPTS_DIR.glob("*.txt")
                    if path not in existing_scripts
                ]
                if new_scripts:
                    cancelled_script = max(
                        new_scripts, key=lambda path: path.stat().st_mtime_ns
                    )
            if cancelled_script:
                cleanup_cancelled_pipeline(cancelled_script)
            raise JobCancelled
        time.sleep(1)
    if not script_path:
        new_scripts = [
            path for path in SCRIPTS_DIR.glob("*.txt") if path not in existing_scripts
        ]
        if new_scripts:
            script_path = max(
                new_scripts, key=lambda path: path.stat().st_mtime_ns
            ).resolve()
    if script_path:
        database.update_job(
            job["id"], "processing", script_path=str(script_path)
        )
    if process.returncode:
        raise subprocess.CalledProcessError(process.returncode, process.args)
    if script_path:
        expected_video = VIDEOS_DIR / f"{script_path.stem}.mp4"
        if expected_video.is_file():
            return expected_video
    return newest_video(existing_videos)


def cleanup_generated_media(video_path: Path) -> None:
    """Remove successful pipeline working media after durable R2 storage."""
    stem = video_path.stem
    video_path.unlink(missing_ok=True)
    (THUMBNAILS_DIR / f"{stem}.jpg").unlink(missing_ok=True)
    (AUDIO_DIR / f"{stem}.wav").unlink(missing_ok=True)
    shutil.rmtree(IMAGES_DIR / stem, ignore_errors=True)
    shutil.rmtree(SOUNDS_DIR / stem, ignore_errors=True)


def cleanup_cancelled_pipeline(script_path: Path) -> None:
    """Remove resumable working files after the creator deletes the job."""
    stem = script_path.stem
    script_path.unlink(missing_ok=True)
    for path in (
        AUDIO_DIR / f"{stem}.wav",
        AUDIO_DIR / f"{stem}.timings.json",
        VIDEOS_DIR / f"{stem}.mp4",
        VIDEOS_DIR / f"{stem}.rendering.mp4",
        THUMBNAILS_DIR / f"{stem}.jpg",
    ):
        path.unlink(missing_ok=True)
    shutil.rmtree(AUDIO_DIR / f"{stem}_chunks", ignore_errors=True)
    shutil.rmtree(IMAGES_DIR / stem, ignore_errors=True)
    shutil.rmtree(SOUNDS_DIR / stem, ignore_errors=True)


def migrate_one_local_media() -> bool:
    """Move one legacy finished video to R2 while the worker is otherwise idle."""
    candidates = database.list_local_media_jobs()
    job = next((item for item in candidates if Path(item["video_path"]).is_file()), None)
    if job is None:
        return False
    local_video = Path(job["video_path"])
    owner_id = int(job.get("owner_id") or 0)
    remote_video = storage.upload_file(
        local_video,
        storage.job_video_key(owner_id, int(job["id"]), local_video.suffix or ".mp4"),
        "video/mp4",
    )
    database.record_media_asset(
        owner_id=owner_id,
        reference=remote_video,
        size_bytes=local_video.stat().st_size,
        kind="upload" if job["kind"] == "manual" else "video",
    )
    local_thumbnail = THUMBNAILS_DIR / f"{local_video.stem}.jpg"
    if local_thumbnail.is_file():
        remote_thumbnail = storage.upload_file(
            local_thumbnail,
            storage.job_thumbnail_key(owner_id, int(job["id"])),
            "image/jpeg",
        )
        database.record_media_asset(
            owner_id=owner_id,
            reference=remote_thumbnail,
            size_bytes=local_thumbnail.stat().st_size,
            kind="thumbnail",
        )
    database.update_job(int(job["id"]), str(job["status"]), video_path=remote_video)
    if job["kind"] == "automatic":
        cleanup_generated_media(local_video)
    else:
        local_video.unlink(missing_ok=True)
        local_thumbnail.unlink(missing_ok=True)
    return True


def process_job(job) -> None:
    job = dict(job)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"job_{job['id']}.log"
    database.update_job(job["id"], "processing", log_path=str(log_path))
    generated_video: Path | None = None
    try:
        with ExitStack() as stack:
            with log_path.open("a", encoding="utf-8") as log_file:
                if job["kind"] == "automatic":
                    print("Preparing topic and post metadata...", file=log_file, flush=True)
                    metadata = generate_post_metadata(
                        topic=job["topic"] or "",
                        title=job["title"] or "",
                        description=job["description"] or "",
                        hashtags=job["hashtags"] or "",
                        niche=job.get("creator_niche") or "",
                        audience=job.get("target_audience") or "",
                    )
                    if database.cancellation_requested(job["id"]):
                        raise JobCancelled
                    job.update(metadata)
                    database.update_job(job["id"], "processing", **metadata)
                    if storage.is_remote(job.get("video_path")):
                        print("Reusing finished video from R2...", file=log_file, flush=True)
                        video_path = stack.enter_context(
                            storage.local_copy(str(job["video_path"]), ".mp4")
                        )
                    else:
                        print(f"Topic: {job['topic']}", file=log_file, flush=True)
                        video_path = run_automatic(job, log_file)
                        generated_video = video_path
                else:
                    source_reference = str(job["source_path"])
                    video_path = stack.enter_context(
                        storage.local_copy(source_reference, Path(source_reference).suffix or ".mp4")
                    )
                if database.cancellation_requested(job["id"]):
                    raise JobCancelled
                if not video_path.is_file():
                    raise RuntimeError(f"Video file does not exist: {video_path}")

            owner_id = int(job.get("owner_id") or 0)
            if job["kind"] == "manual" and storage.is_remote(job.get("source_path")):
                remote_video = str(job["source_path"])
            elif storage.is_remote(job.get("video_path")):
                remote_video = str(job["video_path"])
            else:
                local_thumbnail = THUMBNAILS_DIR / f"{video_path.stem}.jpg"
                video_key = storage.job_video_key(
                    owner_id, int(job["id"]), video_path.suffix or ".mp4"
                )
                expected_video = storage.object_reference(video_key)
                assets = [(expected_video, video_path.stat().st_size, "video")]
                if local_thumbnail.is_file():
                    expected_thumbnail = storage.object_reference(
                        storage.job_thumbnail_key(owner_id, int(job["id"]))
                    )
                    assets.append(
                        (expected_thumbnail, local_thumbnail.stat().st_size, "thumbnail")
                    )
                if not database.reserve_media_assets(owner_id, assets):
                    if generated_video:
                        cleanup_generated_media(generated_video)
                    raise RuntimeError(
                        "Storage is full. Delete an old video or upgrade the plan, then retry."
                    )
                try:
                    remote_video = storage.upload_file(
                        video_path, video_key, "video/mp4"
                    )
                except RuntimeError:
                    for reference, _size, _kind in assets:
                        database.remove_media_asset(reference)
                    raise
                database.update_job(
                    int(job["id"]), "processing", video_path=remote_video
                )
                job["video_path"] = remote_video
                if local_thumbnail.is_file():
                    try:
                        storage.upload_file(
                            local_thumbnail,
                            storage.job_thumbnail_key(owner_id, int(job["id"])),
                            "image/jpeg",
                        )
                    except RuntimeError:
                        database.remove_media_asset(expected_thumbnail)
                        raise
            job["video_path"] = remote_video

            platforms = json.loads(job["platforms"])
            if not platforms:
                if database.cancellation_requested(job["id"]):
                    raise JobCancelled
                database.update_job(job["id"], "completed", video_path=remote_video)
                notify_creator(
                    job.get("owner_id"),
                    f"Video #{job.get('owner_job_number') or job['id']} is ready to watch.",
                )
                if generated_video:
                    cleanup_generated_media(generated_video)
                return

            database.update_job(job["id"], "publishing", video_path=remote_video)
            metadata = dict(
                title=job["title"],
                description=job["description"],
                hashtags=job["hashtags"],
                youtube_privacy=job.get("youtube_privacy") or "public",
            )
            waiting = False
            for platform in platforms:
                if database.cancellation_requested(job["id"]):
                    raise JobCancelled
                try:
                    result = publish(platform, job.get("owner_id"), video_path, metadata)
                    database.update_publication(job["id"], platform, "published", **result)
                except RuntimeError as exc:
                    waiting = True
                    database.update_publication(job["id"], platform, "waiting", error=str(exc))
            if database.cancellation_requested(job["id"]):
                raise JobCancelled
            database.update_job(job["id"], "waiting_for_connections" if waiting else "published")
            notify_creator(
                job.get("owner_id"),
                f"Video #{job.get('owner_job_number') or job['id']} is finished.",
            )
            if generated_video:
                cleanup_generated_media(generated_video)
    except JobCancelled:
        references = (job.get("video_path"), job.get("source_path"))
        database.delete_job(job["id"])
        for reference in {value for value in references if storage.is_remote(value)}:
            if database.media_reference_count(reference) == 0:
                for stored_reference in (reference, storage.thumbnail_reference(reference)):
                    if not stored_reference:
                        continue
                    try:
                        storage.delete_object(stored_reference)
                        database.remove_media_asset(stored_reference)
                    except RuntimeError:
                        pass
        log_path.unlink(missing_ok=True)
    except (OSError, subprocess.CalledProcessError, RuntimeError) as exc:
        database.update_job(job["id"], "failed", error=str(exc))
        storage_full = str(exc).startswith("Storage is full.")
        notify_creator(
            job.get("owner_id"),
            (
                "Your storage is full. Delete an old video or upgrade your plan, "
                f"then retry video #{job.get('owner_job_number') or job['id']}."
                if storage_full else
                f"Video #{job.get('owner_job_number') or job['id']} could not be finished. It did not use one of your plan videos, and you can try again."
            ),
        )


def worker_loop(stop_event: threading.Event) -> None:
    next_maintenance = 0.0
    while not stop_event.is_set():
        if time.monotonic() >= next_maintenance:
            try:
                database.create_expiry_notifications()
            except Exception:
                # A reminder failure must never stop story processing.
                pass
            next_maintenance = time.monotonic() + 3600
        job = database.claim_job()
        if job:
            process_job(job)
        else:
            try:
                if migrate_one_local_media():
                    continue
            except RuntimeError:
                pass
            stop_event.wait(2)


def start_worker() -> tuple[threading.Event, threading.Thread]:
    database.purge_cancel_requested_jobs()
    stop_event = threading.Event()
    thread = threading.Thread(target=worker_loop, args=(stop_event,), daemon=True)
    thread.start()
    return stop_event, thread
