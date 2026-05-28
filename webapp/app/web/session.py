"""
Server-side session handling for the HTML UI.

Two cookies in play:

  1. ``sparki_session`` — the long-lived (8h) authenticated session.
     Payload: the user's UUID. Set by /auth/callback after a successful
     Keycloak login, cleared by /logout.

  2. ``sparki_oauth_flight`` — a short-lived (10 min) "in-flight" cookie
     that carries the OAuth ``state`` + PKCE ``code_verifier`` while the
     browser is at Keycloak. Set when /login redirects out, deleted on
     callback (success OR failure). Separate cookie + separate signing
     salt so a leaked flight cookie can never be used as a session.

Both cookies are HMAC-signed with ``WEBAPP_SECRET_KEY`` via
``itsdangerous`` and carry only opaque payloads — no JWTs ever land
in the browser.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Annotated, Final

from fastapi import Depends, HTTPException, Request, Response, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.schemas import CurrentUser
from app.config import settings
from app.database import get_session
from app.users.models import User

logger = logging.getLogger("sparki.web.session")


# ─── Session cookie constants ────────────────────────────────────────
SESSION_COOKIE_NAME: Final[str] = "sparki_session"
SESSION_MAX_AGE_SECONDS: Final[int] = 60 * 60 * 8            # 8 hours
SESSION_SALT: Final[str] = "sparki.session.v1"

# ─── OAuth flight cookie constants ───────────────────────────────────
OAUTH_FLIGHT_COOKIE_NAME: Final[str] = "sparki_oauth_flight"
OAUTH_FLIGHT_MAX_AGE_SECONDS: Final[int] = 600               # 10 minutes
OAUTH_FLIGHT_SALT: Final[str] = "sparki.oauth-flight.v1"     # DIFFERENT salt


def _session_serializer() -> URLSafeTimedSerializer:
    """Signer for the long-lived auth session cookie."""
    return URLSafeTimedSerializer(
        secret_key=settings.webapp_secret_key.get_secret_value(),
        salt=SESSION_SALT,
    )


def _flight_serializer() -> URLSafeTimedSerializer:
    """Signer for the short-lived OAuth-in-flight cookie.

    Uses a different salt than the session signer so the two cookies'
    signatures cannot be cross-validated even with the same secret key.
    """
    return URLSafeTimedSerializer(
        secret_key=settings.webapp_secret_key.get_secret_value(),
        salt=OAUTH_FLIGHT_SALT,
    )


# ═══════════════════════════════════════════════════════════════════════
# Session cookie (long-lived authenticated session)
# ═══════════════════════════════════════════════════════════════════════

def set_session_cookie(response: Response, user_id: uuid.UUID) -> None:
    """Sign the user UUID and attach it as the session cookie."""
    token = _session_serializer().dumps(str(user_id))
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    """Remove the session cookie (used by logout)."""
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
    )


def _read_user_id_from_cookie(request: Request) -> uuid.UUID | None:
    """Verify the session cookie and return the embedded UUID, else None."""
    raw = request.cookies.get(SESSION_COOKIE_NAME)
    if not raw:
        return None
    try:
        unsigned = _session_serializer().loads(raw, max_age=SESSION_MAX_AGE_SECONDS)
    except SignatureExpired:
        logger.info("Session cookie expired")
        return None
    except BadSignature:
        logger.warning("Session cookie failed signature verification")
        return None
    try:
        return uuid.UUID(unsigned)
    except (ValueError, TypeError):
        logger.warning("Session cookie carried a non-UUID payload")
        return None


# ═══════════════════════════════════════════════════════════════════════
# OAuth flight cookie (short-lived state + PKCE verifier stash)
# ═══════════════════════════════════════════════════════════════════════

def set_oauth_flight_cookie(
    response: Response,
    *,
    state: str,
    code_verifier: str,
) -> None:
    """Stash state + PKCE verifier in a signed short-lived cookie.

    The payload is a JSON blob `{"s": state, "v": verifier}` so we can
    add fields later (e.g. a `?next=` target) without breaking older
    flight cookies.
    """
    payload = json.dumps({"s": state, "v": code_verifier})
    token = _flight_serializer().dumps(payload)
    response.set_cookie(
        key=OAUTH_FLIGHT_COOKIE_NAME,
        value=token,
        max_age=OAUTH_FLIGHT_MAX_AGE_SECONDS,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        path="/",
    )


def clear_oauth_flight_cookie(response: Response) -> None:
    """Delete the flight cookie (call on callback, success OR failure)."""
    response.delete_cookie(
        key=OAUTH_FLIGHT_COOKIE_NAME,
        path="/",
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
    )


def read_oauth_flight_cookie(request: Request) -> tuple[str, str] | None:
    """Read and verify the flight cookie. Returns (state, verifier) or None.

    Returns None if the cookie is missing, expired, tampered, or
    malformed — caller treats all four cases identically (reject the
    callback as suspicious).
    """
    raw = request.cookies.get(OAUTH_FLIGHT_COOKIE_NAME)
    if not raw:
        return None
    try:
        unsigned = _flight_serializer().loads(
            raw, max_age=OAUTH_FLIGHT_MAX_AGE_SECONDS,
        )
    except SignatureExpired:
        logger.info("OAuth flight cookie expired (login took >10 min)")
        return None
    except BadSignature:
        logger.warning("OAuth flight cookie failed signature verification")
        return None
    try:
        payload = json.loads(unsigned)
        state = payload["s"]
        verifier = payload["v"]
        if not isinstance(state, str) or not isinstance(verifier, str):
            raise TypeError("non-string fields in flight cookie")
        return state, verifier
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning("OAuth flight cookie malformed: %s", e)
        return None


# ═══════════════════════════════════════════════════════════════════════
# FastAPI dependencies
# ═══════════════════════════════════════════════════════════════════════

async def get_session_user_optional(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> CurrentUser | None:
    """Return the logged-in user from the cookie, or None if anonymous."""
    user_id = _read_user_id_from_cookie(request)
    if user_id is None:
        return None
    row = await db.execute(select(User).where(User.id == user_id))
    user = row.scalar_one_or_none()
    if user is None:
        logger.warning("Session cookie referenced unknown user_id=%s", user_id)
        return None
    return CurrentUser.model_validate(user)


async def get_session_user_required(
    user: Annotated[CurrentUser | None, Depends(get_session_user_optional)],
) -> CurrentUser:
    """Require a logged-in user; 401 if anonymous.

    In a future iteration we may rewrite this to redirect to /login
    for browser requests (detect Accept: text/html). For now an
    explicit 401 keeps the behavior predictable.
    """
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Niet ingelogd",
        )
    return user
