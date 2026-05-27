"""
HTML routes for the Sparki dashboard UI.

Step 3.1 scope — layout shell only:
  GET  /                  Portfolio placeholder (or unauth landing splash)
  GET  /login             Placeholder login page (real Keycloak in 3.2)
  POST /logout            Clear session cookie
  GET  /dev/login         DEV-ONLY stub: log in as a seeded demo user

Routes that produce HTML are explicitly typed with HTMLResponse so FastAPI's
OpenAPI schema lists them correctly (Swagger shows them under the ``web``
tag separately from the JSON API).
"""

from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.schemas import CurrentUser
from app.config import settings
from app.database import get_session
from app.users.models import User, UserRole
from app.web.session import (
    clear_session_cookie,
    get_session_user_optional,
    set_session_cookie,
)
from app.web.templates_env import template_context, templates

logger = logging.getLogger("sparki.web.routes")

# No prefix — HTML lives at the site root (/, /login, /logout, ...).
router = APIRouter(tags=["web"], include_in_schema=False)


# ─── Home / Portfolio ────────────────────────────────────────────────
@router.get("/", response_class=HTMLResponse)
async def home(
    request: Request,
    user: Annotated[CurrentUser | None, Depends(get_session_user_optional)],
) -> HTMLResponse:
    """Site root.

    Anonymous → splash page with a "Inloggen" CTA.
    Logged in → the layout shell with a placeholder portfolio area (the
    real building list lands in Step 3.3).
    """
    if user is None:
        return templates.TemplateResponse(
            request,
            "pages/landing.html",
            template_context(request, user=None),
        )
    return templates.TemplateResponse(
        request,
        "pages/placeholder.html",
        template_context(request, user=user, page_title="Portfolio"),
    )


# ─── Login (placeholder) ─────────────────────────────────────────────
# response_model=None: this route can return either an HTMLResponse OR a
# RedirectResponse depending on whether the user is already logged in.
# Without response_model=None, FastAPI tries to derive a Pydantic schema
# from the union return type, which fails because Response subclasses
# aren't valid Pydantic field types.
@router.get("/login", response_class=HTMLResponse, response_model=None)
async def login_page(
    request: Request,
    user: Annotated[CurrentUser | None, Depends(get_session_user_optional)],
) -> HTMLResponse | RedirectResponse:
    """Login landing page.

    In Step 3.2 this will initiate the OIDC authorization-code flow with
    Keycloak (build the auth URL with state+PKCE, redirect there).
    For Step 3.1 it just renders a placeholder that explains what's coming.
    Already-logged-in users are bounced to the dashboard.
    """
    if user is not None:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request,
        "pages/login.html",
        template_context(request, user=None, page_title="Inloggen"),
    )


# ─── Logout ──────────────────────────────────────────────────────────
@router.post("/logout")
@router.get("/logout")  # support GET too, for simple anchor-tag logout in the header
async def logout(request: Request) -> RedirectResponse:
    """Clear the session cookie and bounce to the landing page.

    In Step 3.2 this will also call Keycloak's ``end_session_endpoint``
    so the user is logged out at the IdP. For now, clearing our cookie
    is enough — they can still hit /dev/login again in dev.
    """
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    clear_session_cookie(response)
    return response


# ─── DEV-ONLY: stub login for layout testing ─────────────────────────
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
    as_: DevRoleParam = "staff",          # use ?as=staff|owner|tenant
) -> RedirectResponse:
    """Dev-only shortcut: set the session cookie to a seeded demo user.

    Removed entirely once Step 3.2 wires the real Keycloak redirect.
    Refuses to run in non-development environments — the seeded
    accounts only exist in dev/test data anyway, but we belt-and-brace.

    Usage during local testing:
        http://localhost:8000/dev/login?as_=staff
        http://localhost:8000/dev/login?as_=owner
        http://localhost:8000/dev/login?as_=tenant
    """
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


# Convenience: the role enum is referenced by templates only via the
# role_label filter, so we don't need to expose UserRole here.
__all__ = ["router"]


# Silence unused-import warning while keeping the symbol available
# for future use in admin pages (Step 3.5+).
_ = UserRole
