"""
Server-side session handling for the HTML UI.

Strategy: we issue a signed cookie ``sparki_session`` whose payload is just
the user's UUID (the same UUID that lives on the ``users.id`` primary key
and matches Keycloak's ``sub`` claim). On every request we read the cookie,
verify the signature, and load the matching DB row into a ``CurrentUser``.

We deliberately do NOT store the JWT itself in the cookie:
  - keeps the cookie small (a single signed UUID, ~150 bytes)
  - we never need the JWT again on the server: permissions live in our own DB
  - logout = clear the cookie; no token-revocation dance needed

The cookie is signed (HMAC) with ``WEBAPP_SECRET_KEY`` so it cannot be
forged by the browser. It is ``HttpOnly`` + ``SameSite=Lax`` always, and
``Secure`` in non-development environments.

The full Keycloak Authorization Code + PKCE redirect flow lands in
Step 3.2; for Step 3.1 we expose a dev-only stub endpoint that sets the
cookie directly so the layout can be tested end-to-end immediately.
"""

from __future__ import annotations

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


# ─── Constants ───────────────────────────────────────────────────────
SESSION_COOKIE_NAME: Final[str] = "sparki_session"
SESSION_MAX_AGE_SECONDS: Final[int] = 60 * 60 * 8  # 8 hours
SESSION_SALT: Final[str] = "sparki.session.v1"     # namespacing for the signer


def _serializer() -> URLSafeTimedSerializer:
    """Build the signer. Re-built per call so secret rotation is honored."""
    return URLSafeTimedSerializer(
        secret_key=settings.webapp_secret_key.get_secret_value(),
        salt=SESSION_SALT,
    )


# ─── Cookie write / clear ────────────────────────────────────────────
def set_session_cookie(response: Response, user_id: uuid.UUID) -> None:
    """Sign the user UUID and attach it as the session cookie."""
    token = _serializer().dumps(str(user_id))
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=settings.is_production,    # localhost http in dev needs secure=False
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


# ─── Cookie read ─────────────────────────────────────────────────────
def _read_user_id_from_cookie(request: Request) -> uuid.UUID | None:
    """Verify the cookie signature and return the embedded UUID, else None.

    Returns None on any failure (missing cookie, bad signature, expired,
    or malformed UUID) — the caller decides whether to redirect or 401.
    """
    raw = request.cookies.get(SESSION_COOKIE_NAME)
    if not raw:
        return None
    try:
        unsigned = _serializer().loads(raw, max_age=SESSION_MAX_AGE_SECONDS)
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


# ─── Dependencies ────────────────────────────────────────────────────
async def get_session_user_optional(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> CurrentUser | None:
    """Return the logged-in user from the cookie, or None if anonymous.

    Use this for routes that render different content for guests
    vs. signed-in users without forcing a redirect (e.g. a public
    landing splash that says "Inloggen" when logged out and the
    actual dashboard when logged in).
    """
    user_id = _read_user_id_from_cookie(request)
    if user_id is None:
        return None
    row = await db.execute(select(User).where(User.id == user_id))
    user = row.scalar_one_or_none()
    if user is None:
        # Cookie was valid but the user no longer exists — treat as anonymous.
        logger.warning("Session cookie referenced unknown user_id=%s", user_id)
        return None
    return CurrentUser.model_validate(user)


async def get_session_user_required(
    user: Annotated[CurrentUser | None, Depends(get_session_user_optional)],
) -> CurrentUser:
    """Require a logged-in user; 401 if anonymous.

    In Step 3.2 we'll wrap this so that a 401 from a browser triggers a
    redirect to ``/login`` instead of a JSON error. For now an explicit
    401 is good enough — it surfaces broken auth quickly during dev.
    """
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Niet ingelogd",
        )
    return user
