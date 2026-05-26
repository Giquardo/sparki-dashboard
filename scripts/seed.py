"""
Seed the Sparki database + Keycloak with demo data.

Run from inside the webapp container:
    docker compose exec webapp python /app/scripts/seed.py

What this creates:
  Keycloak (realm: sparki):
    - 3 users with realm-roles assigned
  Postgres:
    - 2 organizations (Sparki itself + a fictitious customer)
    - 1 site under the customer
    - 10 buildings under the site
    - 3 users (same UUIDs as Keycloak's `sub`)
    - 1 building assignment (tenant → Woning 1)

The script is IDEMPOTENT: running it twice does not produce duplicates.
It uses upserts for orgs/sites/buildings and looks up existing Keycloak
users before creating them.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# ─── Make `app` importable when running from /app ─────────────────────
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.buildings.models import Building, BuildingAssignment  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import AsyncSessionLocal  # noqa: E402
from app.organizations.models import Organization, OrganizationType  # noqa: E402
from app.sites.models import Site  # noqa: E402
from app.users.models import User, UserRole  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("seed")


# ═══════════════════════════════════════════════════════════════════════
# Demo data spec
# ═══════════════════════════════════════════════════════════════════════
DEFAULT_PASSWORD = "Sparki!1234"  # noqa: S105 — dev fixture, not a real credential


@dataclass(frozen=True)
class SeedUser:
    email: str
    username: str
    first_name: str
    last_name: str
    role: UserRole


SEED_USERS: list[SeedUser] = [
    SeedUser(
        email="staff@sparki.test",
        username="staff",
        first_name="Sam",
        last_name="Sparki",
        role=UserRole.SPARKI_STAFF,
    ),
    SeedUser(
        email="owner@sigenburg.test",
        username="owner",
        first_name="Olga",
        last_name="Owner",
        role=UserRole.SITE_OWNER,
    ),
    SeedUser(
        email="tenant@sigenburg.test",
        username="tenant",
        first_name="Tom",
        last_name="Tenant",
        role=UserRole.TENANT,
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# Keycloak admin client
# ═══════════════════════════════════════════════════════════════════════
class KeycloakAdmin:
    """Minimal admin client — only what we need for seeding."""

    def __init__(self, base_url: str, realm: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.realm = realm
        self._token: str | None = None
        self._client = httpx.AsyncClient(timeout=15.0)

    async def login(self, admin_user: str, admin_password: str) -> None:
        """Authenticate against the MASTER realm — that's where the
        Keycloak admin user lives."""
        url = f"{self.base_url}/realms/master/protocol/openid-connect/token"
        resp = await self._client.post(
            url,
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": admin_user,
                "password": admin_password,
            },
        )
        resp.raise_for_status()
        self._token = resp.json()["access_token"]
        logger.info("Keycloak admin login OK")

    @property
    def _headers(self) -> dict[str, str]:
        if not self._token:
            raise RuntimeError("Call .login() first")
        return {"Authorization": f"Bearer {self._token}"}

    async def find_user_by_username(self, username: str) -> dict | None:
        url = f"{self.base_url}/admin/realms/{self.realm}/users"
        resp = await self._client.get(
            url, headers=self._headers, params={"username": username, "exact": "true"},
        )
        resp.raise_for_status()
        users = resp.json()
        return users[0] if users else None

    async def create_user(
        self,
        *,
        username: str,
        email: str,
        first_name: str,
        last_name: str,
        password: str,
    ) -> str:
        """Create a Keycloak user. Returns the user's `sub` UUID.

        If the user already exists, returns its existing UUID — no error.
        """
        existing = await self.find_user_by_username(username)
        if existing:
            logger.info("User %s already exists (id=%s), skipping create", username, existing["id"])
            return existing["id"]

        url = f"{self.base_url}/admin/realms/{self.realm}/users"
        payload = {
            "username": username,
            "email": email,
            "firstName": first_name,
            "lastName": last_name,
            "enabled": True,
            "emailVerified": True,
            "credentials": [
                {"type": "password", "value": password, "temporary": False},
            ],
        }
        resp = await self._client.post(url, headers=self._headers, json=payload)
        if resp.status_code == 409:  # conflict — race or partial state
            logger.warning("Got 409 creating user %s; looking up existing", username)
            existing = await self.find_user_by_username(username)
            if existing:
                return existing["id"]
            raise RuntimeError(f"User {username} reported conflict but cannot be found")
        resp.raise_for_status()

        # Keycloak puts the new user's ID in the Location header
        location = resp.headers["Location"]
        kc_user_id = location.rsplit("/", 1)[-1]
        logger.info("Created Keycloak user %s (id=%s)", username, kc_user_id)
        return kc_user_id

    async def get_realm_role(self, role_name: str) -> dict:
        url = f"{self.base_url}/admin/realms/{self.realm}/roles/{role_name}"
        resp = await self._client.get(url, headers=self._headers)
        resp.raise_for_status()
        return resp.json()

    async def assign_realm_role(self, user_id: str, role_name: str) -> None:
        role = await self.get_realm_role(role_name)
        url = (
            f"{self.base_url}/admin/realms/{self.realm}"
            f"/users/{user_id}/role-mappings/realm"
        )
        # POST is idempotent on Keycloak's side for already-assigned roles
        resp = await self._client.post(url, headers=self._headers, json=[role])
        resp.raise_for_status()
        logger.info("Assigned role %s to user %s", role_name, user_id)

    async def close(self) -> None:
        await self._client.aclose()


# ═══════════════════════════════════════════════════════════════════════
# Postgres seeding (idempotent helpers)
# ═══════════════════════════════════════════════════════════════════════
async def get_or_create_organization(
    db: AsyncSession,
    *,
    name: str,
    type_: OrganizationType,
    email: str | None = None,
) -> Organization:
    existing = await db.execute(select(Organization).where(Organization.name == name))
    org = existing.scalar_one_or_none()
    if org is not None:
        logger.info("Organization %r exists (id=%s)", name, org.id)
        return org

    org = Organization(
        id=uuid.uuid4(),
        name=name,
        type=type_,
        sigen_account_email=email,
    )
    db.add(org)
    await db.flush()
    logger.info("Created organization %r (id=%s)", name, org.id)
    return org


async def get_or_create_site(
    db: AsyncSession,
    *,
    organization: Organization,
    name: str,
    address: str | None = None,
) -> Site:
    stmt = select(Site).where(
        Site.organization_id == organization.id,
        Site.name == name,
    )
    existing = await db.execute(stmt)
    site = existing.scalar_one_or_none()
    if site is not None:
        logger.info("Site %r exists (id=%s)", name, site.id)
        return site

    site = Site(
        id=uuid.uuid4(),
        organization_id=organization.id,
        name=name,
        address=address,
    )
    db.add(site)
    await db.flush()
    logger.info("Created site %r (id=%s)", name, site.id)
    return site


async def get_or_create_building(
    db: AsyncSession,
    *,
    site: Site,
    name: str,
    sigen_system_id: str,
    installed_kwp: float = 6.0,
    battery_kwh: float = 10.0,
) -> Building:
    stmt = select(Building).where(Building.sigen_system_id == sigen_system_id)
    existing = await db.execute(stmt)
    bld = existing.scalar_one_or_none()
    if bld is not None:
        logger.info("Building %r exists (id=%s)", name, bld.id)
        return bld

    bld = Building(
        id=uuid.uuid4(),
        site_id=site.id,
        name=name,
        sigen_system_id=sigen_system_id,
        installed_kwp=installed_kwp,
        battery_kwh=battery_kwh,
    )
    db.add(bld)
    await db.flush()
    logger.info("Created building %r system=%s (id=%s)", name, sigen_system_id, bld.id)
    return bld


async def get_or_create_user(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    organization: Organization,
    email: str,
    display_name: str,
    role: UserRole,
) -> User:
    existing = await db.execute(select(User).where(User.id == user_id))
    user = existing.scalar_one_or_none()
    if user is not None:
        logger.info("User %s exists (id=%s)", email, user.id)
        return user

    user = User(
        id=user_id,
        organization_id=organization.id,
        email=email,
        display_name=display_name,
        role=role,
    )
    db.add(user)
    await db.flush()
    logger.info("Created user %s role=%s (id=%s)", email, role.value, user.id)
    return user


async def ensure_building_assignment(
    db: AsyncSession,
    *,
    building: Building,
    user: User,
) -> None:
    stmt = select(BuildingAssignment).where(
        BuildingAssignment.building_id == building.id,
        BuildingAssignment.user_id == user.id,
    )
    existing = await db.execute(stmt)
    if existing.scalar_one_or_none() is not None:
        logger.info("Assignment %s↔%s exists", user.email, building.name)
        return

    assignment = BuildingAssignment(
        id=uuid.uuid4(),
        building_id=building.id,
        user_id=user.id,
    )
    db.add(assignment)
    await db.flush()
    logger.info("Assigned user %s to building %s", user.email, building.name)


# ═══════════════════════════════════════════════════════════════════════
# Main flow
# ═══════════════════════════════════════════════════════════════════════
async def main() -> None:
    logger.info("=== Sparki seed script starting ===")

    # ─── 1. Keycloak: users + role mappings ──────────────────────────
    kc = KeycloakAdmin(
        base_url=settings.keycloak_internal_url,
        realm=settings.keycloak_realm,
    )

    # We need the master-realm admin credentials. They're in env vars
    # (used originally for KEYCLOAK_ADMIN / KEYCLOAK_ADMIN_PASSWORD).
    import os
    admin_user = os.environ.get("KEYCLOAK_ADMIN", "admin")
    admin_password = os.environ.get("KEYCLOAK_ADMIN_PASSWORD")
    if not admin_password:
        raise RuntimeError(
            "KEYCLOAK_ADMIN_PASSWORD env var not set — pass it through "
            "from compose or set it manually before running.",
        )

    await kc.login(admin_user, admin_password)

    kc_user_ids: dict[str, uuid.UUID] = {}
    for spec in SEED_USERS:
        kc_id = await kc.create_user(
            username=spec.username,
            email=spec.email,
            first_name=spec.first_name,
            last_name=spec.last_name,
            password=DEFAULT_PASSWORD,
        )
        await kc.assign_realm_role(kc_id, spec.role.value)
        kc_user_ids[spec.email] = uuid.UUID(kc_id)

    await kc.close()

    # ─── 2. Postgres: orgs / sites / buildings / users / assignments ──
    async with AsyncSessionLocal() as db:
        # Two organizations
        sparki_org = await get_or_create_organization(
            db,
            name="Sparki",
            type_=OrganizationType.SPARKI,
        )
        customer_org = await get_or_create_organization(
            db,
            name="Stad Sigenburg — Sociale Huisvesting",
            type_=OrganizationType.SITE_OWNER,
            email="sigenburg@example.test",
        )

        # One site under the customer
        site = await get_or_create_site(
            db,
            organization=customer_org,
            name="Wijk Sint-Jan",
            address="Sint-Jansplein 1, 8900 Sigenburg",
        )

        # Ten buildings — sigen_system_id is a placeholder until real
        # Sigencloud systems are linked; Sigencloud's real IDs are numeric.
        buildings: list[Building] = []
        for n in range(1, 11):
            bld = await get_or_create_building(
                db,
                site=site,
                name=f"Woning {n}",
                sigen_system_id=f"MOCK-SYSTEM-{n:03d}",
            )
            buildings.append(bld)

        # Three users — note the org choice per role
        sparki_staff_uuid = kc_user_ids["staff@sparki.test"]
        owner_uuid = kc_user_ids["owner@sigenburg.test"]
        tenant_uuid = kc_user_ids["tenant@sigenburg.test"]

        await get_or_create_user(
            db,
            user_id=sparki_staff_uuid,
            organization=sparki_org,        # staff is in the Sparki org
            email="staff@sparki.test",
            display_name="Sam Sparki",
            role=UserRole.SPARKI_STAFF,
        )
        await get_or_create_user(
            db,
            user_id=owner_uuid,
            organization=customer_org,      # owner belongs to the customer
            email="owner@sigenburg.test",
            display_name="Olga Owner",
            role=UserRole.SITE_OWNER,
        )
        tenant = await get_or_create_user(
            db,
            user_id=tenant_uuid,
            organization=customer_org,      # tenant also belongs to the customer
            email="tenant@sigenburg.test",
            display_name="Tom Tenant",
            role=UserRole.TENANT,
        )

        # Assign the tenant to Woning 1
        await ensure_building_assignment(db, building=buildings[0], user=tenant)

        await db.commit()
        logger.info("Postgres seed committed")

    logger.info("=== Seed complete ===")
    logger.info("Login credentials (all use password '%s'):", DEFAULT_PASSWORD)
    for spec in SEED_USERS:
        logger.info("  %-25s  role=%s", spec.email, spec.role.value)


if __name__ == "__main__":
    asyncio.run(main())
