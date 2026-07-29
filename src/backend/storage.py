"""Private Cloudflare R2 storage for creator media."""

import mimetypes
import os
import tempfile
import hashlib
import hmac
from contextlib import contextmanager
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError


R2_SCHEME = "r2://"


def _settings() -> tuple[str, str, str, str]:
    values = (
        os.getenv("R2_ACCOUNT_ID", "").strip(),
        os.getenv("R2_ACCESS_KEY_ID", "").strip(),
        os.getenv("R2_SECRET_ACCESS_KEY", "").strip(),
        os.getenv("R2_BUCKET", "").strip(),
    )
    if not all(values):
        raise RuntimeError("R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, and R2_BUCKET are required")
    return values


def _client():
    account_id, access_key, secret_key, _bucket = _settings()
    return boto3.client(
        "s3",
        region_name="auto",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )


def object_reference(key: str) -> str:
    return f"{R2_SCHEME}{key.lstrip('/')}"


def is_remote(reference: str | None) -> bool:
    return bool(reference and reference.startswith(R2_SCHEME))


def object_key(reference: str) -> str:
    if not is_remote(reference):
        raise ValueError("Not an R2 object reference")
    return reference[len(R2_SCHEME):]


def available(reference: str | None) -> bool:
    return bool(reference) and (is_remote(reference) or Path(reference).is_file())


def exists(reference: str | None) -> bool:
    if not reference:
        return False
    if not is_remote(reference):
        return Path(reference).is_file()
    _account, _access, _secret, bucket = _settings()
    try:
        _client().head_object(Bucket=bucket, Key=object_key(reference))
        return True
    except (BotoCoreError, ClientError):
        return False


def object_size(reference: str) -> int:
    if not is_remote(reference):
        try:
            return Path(reference).stat().st_size
        except OSError as exc:
            raise RuntimeError("Media size could not be read") from exc
    _account, _access, _secret, bucket = _settings()
    try:
        response = _client().head_object(Bucket=bucket, Key=object_key(reference))
        return int(response.get("ContentLength", 0))
    except (BotoCoreError, ClientError) as exc:
        raise RuntimeError("Media size could not be read from R2") from exc


def delete_object(reference: str | None) -> None:
    if not is_remote(reference):
        return
    _account, _access, _secret, bucket = _settings()
    try:
        _client().delete_object(Bucket=bucket, Key=object_key(reference))
    except (BotoCoreError, ClientError) as exc:
        raise RuntimeError("Media could not be removed from R2") from exc


def upload_file(path: Path, key: str, content_type: str | None = None) -> str:
    _account, _access, _secret, bucket = _settings()
    extra = {"ContentType": content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"}
    try:
        _client().upload_file(str(path), bucket, key, ExtraArgs=extra)
    except (BotoCoreError, ClientError, OSError) as exc:
        raise RuntimeError("Media could not be saved to R2") from exc
    return object_reference(key)


def upload_fileobj(file_object, key: str, content_type: str) -> str:
    _account, _access, _secret, bucket = _settings()
    try:
        file_object.seek(0)
        _client().upload_fileobj(file_object, bucket, key, ExtraArgs={"ContentType": content_type})
    except (BotoCoreError, ClientError, OSError) as exc:
        raise RuntimeError("Media could not be saved to R2") from exc
    return object_reference(key)


def iter_object(reference: str, chunk_size: int = 1024 * 1024):
    _account, _access, _secret, bucket = _settings()
    body = None
    try:
        body = _client().get_object(Bucket=bucket, Key=object_key(reference))["Body"]
        while chunk := body.read(chunk_size):
            yield chunk
    except (BotoCoreError, ClientError, OSError) as exc:
        raise RuntimeError("Media could not be read from R2") from exc
    finally:
        if body is not None:
            body.close()


def open_object(reference: str, byte_range: str | None = None):
    """Open an R2 object, optionally honoring a validated HTTP byte range."""
    _account, _access, _secret, bucket = _settings()
    parameters = {"Bucket": bucket, "Key": object_key(reference)}
    if byte_range and byte_range.startswith("bytes="):
        parameters["Range"] = byte_range
    try:
        response = _client().get_object(**parameters)
    except (BotoCoreError, ClientError) as exc:
        raise RuntimeError("Media could not be read from R2") from exc
    return response


def iter_body(body, chunk_size: int = 1024 * 1024):
    try:
        while chunk := body.read(chunk_size):
            yield chunk
    finally:
        body.close()


@contextmanager
def local_copy(reference: str, suffix: str = ""):
    if not is_remote(reference):
        yield Path(reference)
        return
    descriptor, temporary_name = tempfile.mkstemp(prefix="sleep_studio_", suffix=suffix)
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        with temporary_path.open("wb") as output:
            for chunk in iter_object(reference):
                output.write(chunk)
        yield temporary_path
    finally:
        temporary_path.unlink(missing_ok=True)


def job_video_key(owner_id: int, job_id: int, suffix: str = ".mp4") -> str:
    token = hmac.new(
        os.getenv("ADMIN_SESSION_SECRET", "sleep-studio").encode("utf-8"),
        f"{owner_id}:{job_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:24]
    return f"sleep-studio/creators/{owner_id}/jobs/{job_id}-{token}/video{suffix.lower()}"


def job_thumbnail_key(owner_id: int, job_id: int) -> str:
    video_key = job_video_key(owner_id, job_id)
    return f"{video_key.rsplit('/', 1)[0]}/thumbnail.jpg"


def thumbnail_reference(video_reference: str | None) -> str | None:
    if not is_remote(video_reference):
        return None
    key = object_key(video_reference)
    if "/jobs/" not in key:
        return None
    return object_reference(f"{key.rsplit('/', 1)[0]}/thumbnail.jpg")
