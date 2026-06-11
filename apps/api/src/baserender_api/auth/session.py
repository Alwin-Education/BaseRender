from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass

SESSION_COOKIE_NAME = "baserender_session"
DEFAULT_SESSION_TTL_SECONDS = 60 * 60 * 12


@dataclass(frozen=True)
class AuthConfig:
    password: str
    session_secret: str
    session_ttl_seconds: int
    cookie_name: str
    secure_cookie: bool


def get_auth_config() -> AuthConfig:
    password = os.getenv("BASERENDER_AUTH_PASSWORD", "")
    session_secret = os.getenv("BASERENDER_SESSION_SECRET", "")

    if not password:
        raise RuntimeError("BASERENDER_AUTH_PASSWORD is not configured.")
    if not session_secret:
        raise RuntimeError("BASERENDER_SESSION_SECRET is not configured.")

    return AuthConfig(
        password=password,
        session_secret=session_secret,
        session_ttl_seconds=_session_ttl_seconds(),
        cookie_name=os.getenv("BASERENDER_AUTH_COOKIE_NAME", SESSION_COOKIE_NAME),
        secure_cookie=_secure_cookie(),
    )


def is_valid_proxy_bearer(authorization: str | None) -> bool:
    """Accept the shared token the Next.js middleware injects after Cognito auth.

    The web tier authenticates users against Cognito and forwards API calls with
    `Authorization: Bearer <BASERENDER_PROXY_TOKEN>`; an empty/unset env var
    disables this path entirely.
    """
    expected = os.getenv("BASERENDER_PROXY_TOKEN", "")
    if not expected or not authorization:
        return False
    scheme, _, candidate = authorization.partition(" ")
    if scheme.lower() != "bearer" or not candidate:
        return False
    return secrets.compare_digest(candidate, expected)


def password_matches(candidate: str, config: AuthConfig | None = None) -> bool:
    config = config or get_auth_config()
    return secrets.compare_digest(candidate, config.password)


def create_session_token(config: AuthConfig | None = None, *, now: int | None = None) -> str:
    config = config or get_auth_config()
    issued_at = int(now if now is not None else time.time())
    payload = {
        "iat": issued_at,
        "exp": issued_at + config.session_ttl_seconds,
    }
    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_part = _base64url_encode(payload_json)
    signature = _signature(payload_part, config.session_secret)
    return f"{payload_part}.{signature}"


def is_valid_session_token(
    token: str | None,
    config: AuthConfig | None = None,
    *,
    now: int | None = None,
) -> bool:
    if not token:
        return False

    config = config or get_auth_config()

    try:
        payload_part, signature = token.split(".", 1)
        expected_signature = _signature(payload_part, config.session_secret)
        if not secrets.compare_digest(signature, expected_signature):
            return False

        payload = json.loads(_base64url_decode(payload_part))
        expires_at = int(payload["exp"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False

    current_time = int(now if now is not None else time.time())
    return expires_at > current_time


def _signature(payload_part: str, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        payload_part.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _base64url_decode(value: str) -> str:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}").decode("utf-8")


def _session_ttl_seconds() -> int:
    raw_value = os.getenv("BASERENDER_SESSION_TTL_SECONDS")
    if not raw_value:
        return DEFAULT_SESSION_TTL_SECONDS

    try:
        ttl = int(raw_value)
    except ValueError as exc:
        raise RuntimeError("BASERENDER_SESSION_TTL_SECONDS must be an integer.") from exc

    if ttl <= 0:
        raise RuntimeError("BASERENDER_SESSION_TTL_SECONDS must be greater than zero.")

    return ttl


def _secure_cookie() -> bool:
    raw_value = os.getenv("BASERENDER_AUTH_SECURE_COOKIE")
    if raw_value is None:
        return os.getenv("RENDER") == "true"

    return raw_value.strip().lower() in {"1", "true", "yes", "on"}
