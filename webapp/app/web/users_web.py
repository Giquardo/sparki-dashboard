"""
HTML route for the Gebruikers (users) page.

  GET /users  → role-aware people page

What each role sees:
  - sparki_staff : all orgs → sites → owners + tenants (tenants show
                   their assigned buildings)
  - site_owner   : their org's users only, grouped by site for the
                   tenants and a separate section for fellow owners
  - tenant       : 403 (this page is for managers, not residents)

We DO NOT use buildings_visible_to() here because that function is
about which buildings to *show*, not about which users to display.
The user-visibility rules are simpler: scope by org_id (or staff sees
all). They live in this module rather than in core/permissions.py
because they're only used here — premature abstraction would be worse
than keeping the WHERE clause local.

The hierarchy is built in Python after a few flat queries rather than
via SQL joins, because:
  - the staff query touches all orgs (cardinality stays small in
    practice; 39 tests confirm seed=2 orgs / 1 site / 13 users)
  - building it in Python keeps the template simple (nested dict tree)
  - SQL eager-load relationship trees with selectinload pull more rows
    than needed for this read-only summary page
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.schemas import CurrentUser
from app.buildings.models import Building, BuildingAssignment
from app.core.audit import AuditAction
from app.core.audit_service import log_access_denied
from app.database import get_session
from app.organizations.models import Organization
from app.sites.models import Site
from app.users.models import User, UserRole
from app.web.session import get_session_user_required
from app.web.templates_env import template_context, templates

logger = logging.getLogger("sparki.web.users")

router = APIRouter(tags=["web"], include_in_schema=False)


@router.get("/users", response_class=HTMLResponse, name="users_page")
async def users_page(
    request: Request,
    user: Annotated[CurrentUser, Depends(get_session_user_required)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> HTMLResponse:
    """Render the Gebruikers page.

    Role checks at the top — tenants get 403 + audit (no business here).
    Then we build the hierarchical tree the template renders.
    """
    if user.role == UserRole.TENANT:
        await log_access_denied(
            user=user,
            action=AuditAction.VIEW,
            resource_type="users.page",
            resource_id=None,
            request=request,
            detail="tenants are not authorized to view the users page",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This page is not available for your role.",
        )

    # ── Decide the scope of the page ──────────────────────────────────
    if user.role == UserRole.SPARKI_STAFF:
        # All orgs except the Sparki org itself (staff aren't tenants
        # of their own org). We still SHOW Sparki for transparency,
        # but it's at the top of the list.
        org_stmt = select(Organization).order_by(Organization.name)
    else:
        # SITE_OWNER → only their own organization
        org_stmt = select(Organization).where(Organization.id == user.organization_id)

    orgs = list((await db.execute(org_stmt)).scalars().all())
    org_ids = [o.id for o in orgs]

    # ── Fetch the data in flat lists, group in Python ─────────────────
    sites = []
    users_in_scope: list[User] = []
    assignments: list[BuildingAssignment] = []
    buildings_in_scope: dict[uuid.UUID, Building] = {}
    if org_ids:
        site_rows = await db.execute(
            select(Site).where(Site.organization_id.in_(org_ids))
                        .order_by(Site.name)
        )
        sites = list(site_rows.scalars().all())

        user_rows = await db.execute(
            select(User).where(User.organization_id.in_(org_ids))
                        .order_by(User.display_name)
        )
        users_in_scope = list(user_rows.scalars().all())

        # Building assignments scoped to buildings in these orgs (via site→org)
        if sites:
            site_ids = [s.id for s in sites]
            bld_rows = await db.execute(
                select(Building).where(Building.site_id.in_(site_ids))
            )
            buildings_in_scope = {b.id: b for b in bld_rows.scalars().all()}
            if buildings_in_scope:
                assign_rows = await db.execute(
                    select(BuildingAssignment).where(
                        BuildingAssignment.building_id.in_(buildings_in_scope.keys())
                    )
                )
                assignments = list(assign_rows.scalars().all())

    # user_id → list of building names this user is assigned to
    assignments_by_user: dict[uuid.UUID, list[str]] = {}
    for a in assignments:
        b = buildings_in_scope.get(a.building_id)
        if b is not None:
            assignments_by_user.setdefault(a.user_id, []).append(b.name)
    # Sort each user's buildings alphabetically for stable display
    for lst in assignments_by_user.values():
        lst.sort()

    # ── Build the nested tree the template walks ──────────────────────
    # Shape per org:
    #   { org, owners[], tenants_by_site: { site_name → [user, ...] },
    #     unassigned_tenants[] }
    tree: list[dict] = []
    for org in orgs:
        org_users = [u for u in users_in_scope if u.organization_id == org.id]
        owners = [u for u in org_users if u.role == UserRole.SITE_OWNER]
        staff_in_org = [u for u in org_users if u.role == UserRole.SPARKI_STAFF]
        tenants = [u for u in org_users if u.role == UserRole.TENANT]

        org_sites = [s for s in sites if s.organization_id == org.id]
        # Map site_id → name for grouping tenants by their assigned building's site.
        site_id_to_name = {s.id: s.name for s in org_sites}

        # For each tenant: find their first-assigned building's site.
        # A tenant without assignments lands in "unassigned_tenants".
        tenants_by_site: dict[str, list[dict]] = {}
        unassigned: list[dict] = []
        for t in tenants:
            building_names = assignments_by_user.get(t.id, [])
            # Look up the site of the first assignment (most tenants have one)
            assigned_site_name: str | None = None
            for a in assignments:
                if a.user_id != t.id:
                    continue
                b = buildings_in_scope.get(a.building_id)
                if b is None:
                    continue
                assigned_site_name = site_id_to_name.get(b.site_id)
                if assigned_site_name:
                    break

            tenant_entry = {
                "id": t.id, "email": t.email,
                "display_name": t.display_name,
                "buildings": building_names,
            }
            if assigned_site_name:
                tenants_by_site.setdefault(assigned_site_name, []).append(tenant_entry)
            else:
                unassigned.append(tenant_entry)

        # Sort sites alphabetically for the section order
        ordered_sites = sorted(tenants_by_site.keys())

        tree.append({
            "org": {
                "id": org.id, "name": org.name,
                "type": org.type.value,
            },
            "owners": [
                {"id": o.id, "email": o.email, "display_name": o.display_name}
                for o in owners
            ],
            "staff": [
                {"id": s.id, "email": s.email, "display_name": s.display_name}
                for s in staff_in_org
            ],
            "tenants_by_site": [
                (sn, tenants_by_site[sn]) for sn in ordered_sites
            ],
            "unassigned_tenants": unassigned,
            "total_users": len(org_users),
        })

    return templates.TemplateResponse(
        request,
        "pages/users.html",
        template_context(
            request,
            user=user,
            page_title="Gebruikers",
            tree=tree,
        ),
    )


__all__ = ["router"]
