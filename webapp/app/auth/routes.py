"""
Auth routes — minimal for Phase 1.

For now we only expose `/api/me` so we can verify the full auth flow
end-to-end: Keycloak login → JWT → middleware validation → DB lookup
→ JSON response.

Full login/logout UI flows come in Phase 3.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.auth.dependencies import get_current_user
from app.auth.schemas import CurrentUser
from app.users.models import UserRole

router = APIRouter(prefix="/api", tags=["auth"])


@router.get("/me")
async def me(
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> dict[str, str | bool]:
    """Return information about the currently authenticated user.

    Requires a valid Bearer token. Used for:
      - end-to-end auth flow verification (Phase 1)
      - the "who am I" widget in the UI header (Phase 3)
    """
    return {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role.value,
        "organization_id": str(user.organization_id),
        "is_sparki_staff": user.is_sparki_staff,
        "is_site_owner": user.is_site_owner,
        "is_tenant": user.is_tenant,
    }


@router.get("/me/roles", description="List all defined roles. Public.")
async def list_roles() -> list[dict[str, str]]:
    """Public reference endpoint — what roles does this system know about?

    Public because rules of the road are not a secret. Used by the UI
    in Phase 3 to label things.
    """
    return [
        {"value": r.value, "label": r.value.replace("_", " ").title()}
        for r in UserRole
    ]
