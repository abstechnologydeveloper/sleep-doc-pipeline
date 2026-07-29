"""Paystack checkout and webhook verification helpers."""

import hashlib
import hmac
import json
import os
import urllib.error
import urllib.request


PAYSTACK_API_BASE = "https://api.paystack.co"


def configured() -> bool:
    return bool(os.getenv("PAYSTACK_SECRET_KEY", "").strip())


def _request(path: str, *, method: str = "GET", payload: dict | None = None) -> dict:
    secret = os.getenv("PAYSTACK_SECRET_KEY", "").strip()
    if not secret:
        raise RuntimeError("Paystack billing is not configured.")
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{PAYSTACK_API_BASE}{path}",
        data=body,
        headers={
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        detail = "Paystack could not complete the billing request."
        if isinstance(exc, urllib.error.HTTPError):
            try:
                error = json.loads(exc.read().decode("utf-8"))
                detail = str(error.get("message") or detail)
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass
        raise RuntimeError(detail) from exc
    if not result.get("status"):
        raise RuntimeError(str(result.get("message") or "Paystack rejected the request."))
    return result


def initialize_transaction(
    *, email: str, amount_ngn: int, reference: str, user_id: int,
    plan: str, callback_url: str,
) -> str:
    result = _request(
        "/transaction/initialize",
        method="POST",
        payload={
            "email": email,
            "amount": amount_ngn * 100,
            "currency": "NGN",
            "reference": reference,
            "callback_url": callback_url,
            "metadata": {
                "type": "SLEEP_STUDIO_SUBSCRIPTION",
                "user_id": user_id,
                "plan": plan,
            },
        },
    )
    authorization_url = result.get("data", {}).get("authorization_url")
    if not authorization_url:
        raise RuntimeError("Paystack returned no checkout URL.")
    return str(authorization_url)


def verify_transaction(reference: str) -> dict:
    safe_reference = reference.strip()
    if not safe_reference or not all(ch.isalnum() or ch in "-_" for ch in safe_reference):
        raise RuntimeError("Invalid Paystack reference.")
    return _request(f"/transaction/verify/{safe_reference}").get("data", {})


def verify_webhook(payload: bytes, signature: str) -> dict:
    secret = os.getenv("PAYSTACK_SECRET_KEY", "").strip()
    if not secret:
        raise ValueError("Paystack billing is not configured")
    expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha512).hexdigest()
    if not signature or not hmac.compare_digest(expected, signature):
        raise ValueError("Invalid Paystack signature")
    try:
        return json.loads(payload.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("Invalid Paystack event") from exc
