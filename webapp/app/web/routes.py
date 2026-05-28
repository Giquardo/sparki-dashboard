"""
HTML auth routes for the Sparki dashboard UI.

Step 3.2 — real Keycloak Authorization Code + PKCE flow.
Step 3.3 — the "/" (home/portfolio) route moved to app/web/buildings_web.py.

Routes here:
  GET  /login             302 → Keycloak login page
  GET  /auth/callback     Exchanges the auth code for tokens, sets session
  GET  /logout            Full SSO logout (clear cookie + Keycloak logout)
  POST /logout            Same as GET — both kept for form/anchor flexibility
  GET  /dev/login         DEV-ONLY stub: log in as a seeded demo user

The home route ("/") lives in buildings_web.py because it now renders
the portfolio (a buildings concern).
"""

from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from jose.exceptions import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.keycloak import decode_token
from app.auth.schemas import CurrentUser
from app.config import settings
from app.database import get_session
from app.users.models import User, UserRole
from app.web import oauth
from app.web.session import (
    clear_oauth_flight_cookie,
    clear_session_cookie,
    get_session_user_optional,
    read_oauth_flight_cookie,
    set_oauth_flight_cookie,
    set_session_cookie,
)
from app.web.templates_env import template_context, templates

logger = logging.getLogger("sparki.web.routes")

router = APIRouter(tags=["web"], include_in_schema=False)


# ─── Helpers ─────────────────────────────────────────────────────────
def _callback_url(request: Request) -> str:
    """Build the absolute /auth/callback URL using the request's host."""
    return str(request.url_for("auth_callback"))


def _site_root_url(request: Request) -> str:
    """Absolute URL of the site root, used as post-logout redirect."""
    return str(request.url_for("home"))


# ─── Login: kick off the OAuth dance ─────────────────────────────────
@router.get("/login", name="login", response_model=None)
async def login(
    request: Request,
    user: Annotated[CurrentUser | None, Depends(get_session_user_optional)],
) -> RedirectResponse:
    """Redirect the browser to Keycloak's login page."""
    if user is not None:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    state = oauth.new_state()
    pkce = oauth.new_pkce_pair()
    redirect_uri = _callback_url(request)

    auth_url = oauth.build_login_url(
        state=state,
        code_challenge=pkce.challenge,
        redirect_uri=redirect_uri,
    )
    logger.info(
        "Starting OAuth login: state=%s... redirect_uri=%s",
        state[:8], redirect_uri,
    )

    response = RedirectResponse(url=auth_url, status_code=status.HTTP_303_SEE_OTHER)
    set_oauth_flight_cookie(response, state=state, code_verifier=pkce.verifier)
    return response


# ─── OAuth callback: exchange code for session ───────────────────────
@router.get("/auth/callback", name="auth_callback", response_model=None)
async def auth_callback(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_session)],
    code: Annotated[str | None, Query(description="OAuth auth code")] = None,
    state: Annotated[str | None, Query(description="CSRF state token")] = None,
    error: Annotated[str | None, Query(description="OAuth error code")] = None,
    error_description: Annotated[str | None, Query()] = None,
) -> RedirectResponse | HTMLResponse:
    """Handle Keycloak's redirect back to us after the user authenticates."""
    if error is not None:
        logger.info("Keycloak returned error: %s (%s)", error, error_description)
        return _render_login_error(
            request,
            title="Inloggen geannuleerd",
            detail=error_description or error,
        )

    if not code or not state:
        return _render_login_error(
            request,
            title="Ongeldige callback",
            detail="Keycloak stuurde geen geldige antwoord (code/state ontbreekt).",
        )

    flight = read_oauth_flight_cookie(request)
    if flight is None:
        return _render_login_error(
            request,
            title="Sessie verlopen",
            detail="De login duurde te lang of de browser blokkeerde cookies. "
                   "Probeer opnieuw in te loggen.",
        )
    expected_state, code_verifier = flight
    if not _constant_time_eq(state, expected_state):
        logger.warning(
            "OAuth state mismatch: got %s... expected %s...",
            state[:8], expected_state[:8],
        )
        return _render_login_error(
            request,
            title="Beveiligingsfout",
            detail="State-parameter komt niet overeen. Mogelijk een CSRF-poging. "
                   "Probeer opnieuw in te loggen.",
        )

    try:
        tokens = await oauth.exchange_code_for_token(
            code=code,
            code_verifier=code_verifier,
            redirect_uri=_callback_url(request),
        )
    except oauth.TokenExchangeError as e:
        return _render_login_error(
            request,
            title="Token-uitwisseling mislukt",
            detail=str(e),
        )

    try:
        payload = await decode_token(tokens.access_token)
    except JWTError as e:
        logger.warning("Token returned by Keycloak failed validation: %s", e)
        return _render_login_error(
            request,
            title="Ongeldig token",
            detail="Het door Keycloak afgegeven token is ongeldig.",
        )

    row = await db.execute(select(User).where(User.id == payload.sub))
    user = row.scalar_one_or_none()
    if user is None:
        logger.warning(
            "Keycloak login succeeded but no local user row for sub=%s email=%s",
            payload.sub, payload.email,
        )
        return _render_login_error(
            request,
            title="Account niet geprovisioneerd",
            detail=f"Het account {payload.email} bestaat in Keycloak maar is "
                   "nog niet aangemaakt in Sparki. Neem contact op met de beheerder.",
        )

    logger.info(
        "OAuth login complete: user=%s role=%s",
        user.email, user.role.value,
    )
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    set_session_cookie(response, user.id)
    clear_oauth_flight_cookie(response)
    return response


def _constant_time_eq(a: str, b: str) -> bool:
    """Compare two strings in constant time to prevent timing attacks."""
    import hmac
    return hmac.compare_digest(a.encode("ascii"), b.encode("ascii"))


def _render_login_error(
    request: Request,
    *,
    title: str,
    detail: str,
) -> HTMLResponse:
    """Render the login-error page and clear the flight cookie."""
    response = templates.TemplateResponse(
        request,
        "pages/login_error.html",
        template_context(request, user=None, page_title=title, detail=detail),
        status_code=status.HTTP_400_BAD_REQUEST,
    )
    clear_oauth_flight_cookie(response)
    return response


# ─── Logout: full SSO logout ─────────────────────────────────────────
@router.post("/logout", name="logout_post")
@router.get("/logout", name="logout")
async def logout(request: Request) -> RedirectResponse:
    """Clear our session cookie AND log out at Keycloak."""
    keycloak_logout_url = oauth.build_logout_url(
        post_logout_redirect_uri=_site_root_url(request),
    )
    response = RedirectResponse(
        url=keycloak_logout_url, status_code=status.HTTP_303_SEE_OTHER,
    )
    clear_session_cookie(response)
    clear_oauth_flight_cookie(response)
    return response


# ─── DEV-ONLY: stub login ────────────────────────────────────────────
DevRoleParam = Literal["staff", "owner", "tenant"]

_DEV_ROLE_TO_EMAIL: dict[str, str] = {
    "staff": "staff@sparki.test",
    "owner": "owner@sigenburg.test",
    "tenant": "tenant@sigenburg.test",
}


@router.get("/dev/login")
async def dev_login(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_session)],
    as_: DevRoleParam = "staff",
) -> RedirectResponse:
    """Dev-only shortcut: set the session cookie to a seeded demo user."""
    if settings.is_production:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found",
        )

    email = _DEV_ROLE_TO_EMAIL[as_]
    row = await db.execute(select(User).where(User.email == email))
    user = row.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Demo user {email} not found. Did you run "
                "`docker compose exec webapp python /app/scripts/seed.py`?"
            ),
        )

    logger.info("DEV stub login: %s (role=%s)", user.email, user.role.value)
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    set_session_cookie(response, user.id)
    return response


__all__ = ["router"]

_ = UserRole
