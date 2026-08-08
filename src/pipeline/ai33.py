"""Small stdlib client for asynchronous AI33.Pro media tasks."""

import json
import time
import uuid
from pathlib import Path
from urllib import error, request


BASE_URL = "https://api.ai33.pro"
POLL_SECONDS = 3
TASK_TIMEOUT_SECONDS = 900


def _response_json(api_request: request.Request, timeout: int = 180) -> dict:
    try:
        with request.urlopen(api_request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(
            f"AI33.Pro rejected the request ({exc.code}): {detail}"
        ) from exc
    except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"AI33.Pro request failed: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("AI33.Pro returned an invalid response.")
    return payload


def submit_json_task(api_key: str, path: str, payload: dict) -> str:
    body = json.dumps(payload).encode("utf-8")
    api_request = request.Request(
        f"{BASE_URL}{path}", data=body, method="POST",
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "my-automation-studio/1.0",
        },
    )
    return _task_id(_response_json(api_request))


def submit_multipart_task(api_key: str, path: str, fields: dict[str, object]) -> str:
    boundary = f"----MyAutomationStudio{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.extend(
            (
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                str(value).encode("utf-8"),
                b"\r\n",
            )
        )
    parts.append(f"--{boundary}--\r\n".encode())
    api_request = request.Request(
        f"{BASE_URL}{path}", data=b"".join(parts), method="POST",
        headers={
            "xi-api-key": api_key,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
            "User-Agent": "my-automation-studio/1.0",
        },
    )
    return _task_id(_response_json(api_request))


def submit_file_task(
    api_key: str,
    path: str,
    file_path: Path,
    fields: dict[str, object] | None = None,
) -> str:
    """Submit one local media file using the multipart contract."""
    boundary = f"----MyAutomationStudio{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for name, value in (fields or {}).items():
        parts.extend(
            (
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                str(value).encode("utf-8"),
                b"\r\n",
            )
        )
    parts.extend(
        (
            f"--{boundary}\r\n".encode(),
            (
                f'Content-Disposition: form-data; name="file"; '
                f'filename="{file_path.name}"\r\n'
            ).encode(),
            b"Content-Type: audio/wav\r\n\r\n",
            file_path.read_bytes(),
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        )
    )
    api_request = request.Request(
        f"{BASE_URL}{path}", data=b"".join(parts), method="POST",
        headers={
            "xi-api-key": api_key,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
            "User-Agent": "my-automation-studio/1.0",
        },
    )
    return _task_id(_response_json(api_request))


def _task_id(payload: dict) -> str:
    task_id = str(payload.get("task_id", "")).strip()
    if not task_id:
        raise RuntimeError("AI33.Pro did not return a task ID.")
    return task_id


def wait_for_task(api_key: str, task_id: str) -> dict:
    deadline = time.monotonic() + TASK_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        api_request = request.Request(
            f"{BASE_URL}/v1/task/{task_id}",
            headers={
                "xi-api-key": api_key,
                "Accept": "application/json",
                "User-Agent": "my-automation-studio/1.0",
            },
        )
        task = _response_json(api_request)
        status = str(task.get("status", "")).lower()
        if status == "done":
            return task
        if status in {"failed", "error", "cancelled", "canceled"}:
            message = str(task.get("error_message") or "media generation failed")[:500]
            raise RuntimeError(f"AI33.Pro task failed: {message}")
        time.sleep(POLL_SECONDS)
    raise RuntimeError("AI33.Pro task timed out before the media was ready.")


def download_url(url: str) -> bytes:
    if not url.startswith(("https://", "http://")):
        raise RuntimeError("AI33.Pro returned an invalid output URL.")
    last_error = None
    for attempt in range(1, 5):
        media_request = request.Request(
            url,
            headers={
                "Accept": "audio/*,image/*,application/octet-stream,*/*;q=0.8",
                "Referer": f"{BASE_URL}/",
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "Chrome/124.0 Safari/537.36"
                ),
            },
        )
        try:
            with request.urlopen(media_request, timeout=180) as response:
                payload = response.read()
            if not payload:
                raise RuntimeError("AI33.Pro returned an empty output file.")
            return payload
        except (error.HTTPError, error.URLError, TimeoutError, RuntimeError) as exc:
            last_error = exc
            if attempt < 4:
                time.sleep(attempt * 2)
    raise RuntimeError(f"AI33.Pro output download failed: {last_error}") from last_error


def wait_for_audio(api_key: str, task_id: str) -> bytes:
    task = wait_for_task(api_key, task_id)
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    audio_url = task.get("output_uri") or metadata.get("audio_url") or metadata.get("output_uri")
    if not isinstance(audio_url, str):
        raise RuntimeError("AI33.Pro completed the task without an audio URL.")
    return download_url(audio_url)
