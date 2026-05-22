"""
FastAPI auth dependencies.

Use these on protected endpoints:

    @app.get("/api/me")
    async def me(user: CurrentUser = Depends(get_current_user)):
        ...

    @app.delete("/api/orgs/{id}")
    async def delete_org(
        user: CurrentUser = Depends(require_role(UserRole.SPARKI_STAFF)),
    ):
        ...
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose.exceptions import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.keycloak import decode_token
from app.auth.schemas import CurrentUser
from app.database import get_session
from app.users.models import User, UserRole

logger = logging.getLogger("sparki.auth.deps")

# auto_error=False so we can raise a clean 401 instead of FastAPI's generic 403
bearer_scheme = HTTPBearer(auto_error=False, description="Keycloak JWT")


async def get_current_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> CurrentUser:
    """Resolve the authenticated user from a Bearer JWT.

    Steps:
      1. Extract Bearer token from Authorization header
      2. Verify JWT signature + claims against Keycloak JWKS
      3. Look up matching user row in Postgres (by Keycloak sub UUID)
      4. Return CurrentUser

    Raises:
      401 Unauthorized — missing/invalid/expired token
      403 Forbidden — token is valid but no matching DB user
    """
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = await decode_token(creds.credentials)
    except JWTError as e:
        logger.info("JWT rejected: %s", e)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e

    # Look up our local mirror of the user. PK = Keycloak sub.
    row = await db.execute(select(User).where(User.id == payload.sub))
    user = row.scalar_one_or_none()
    if user is None:
        logger.warning(
            "Valid JWT for unknown user sub=%s email=%s — login but no DB row",
            payload.sub, payload.email,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Account exists in Keycloak but not yet provisioned in "
                "the application. Contact a Sparki administrator."
            ),
        )

    return CurrentUser.model_validate(user)


def require_role(*allowed: UserRole):
    """Build a dependency that allows only specific roles.

    Usage:
        Depends(require_role(UserRole.SPARKI_STAFF))
        Depends(require_role(UserRole.SPARKI_STAFF, UserRole.SITE_OWNER))
    """
    allowed_set = set(allowed)

    async def _checker(
        user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> CurrentUser:
        if user.role not in allowed_set:
            logger.info(
                "Role check failed: user=%s role=%s required=%s",
                user.email, user.role.value, [r.value for r in allowed_set],
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return user

    return _checker
