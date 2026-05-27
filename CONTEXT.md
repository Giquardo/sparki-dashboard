# Sparki — Project Context (Living Document)

> **Audience:** Future-me, future-you, future-Claude. This file is the
> single source of truth on where the Sparki project stands at any
> point. Update at the end of every meaningful work session.

---

## 1. Mission Statement

**Sparki** is an independent, multi-tenant dashboarding platform for
Sigenergy installations (PV + battery + EV + heat pump). It breaks
through the closed mySigen ecosystem so a single `site_owner` can
monitor 10+ houses ("Wijk Sint-Jan" — 10 social-housing units in
Sigenburg) from one interface, and each tenant can see only their
own building.

**Context:** Bachelor thesis (Luik B), Giquardo Vandaele.
**Deadline:** 12 June 2026.
**Status:** Backend complete, ahead of schedule. UI is next.

---

## 2. Architecture (6 Containers, Docker Compose)

| Container | Tech | Purpose |
|-----------|------|---------|
| `postgres` | PostgreSQL 16 | Organisations, sites, buildings, users, audit_log |
| `influxdb` | InfluxDB 2.7 | Time-series: energy metrics + ENTSO-E prices |
| `keycloak` | Keycloak 25 | OAuth2/OIDC, JWT, 3 roles |
| `node-red` | Node-RED 4 (custom image) | Data ingestion (2 flows: mock energy + ENTSO-E prices) |
| `webapp` | FastAPI + uv + asyncpg | REST API, JWT validation, permissions, audit |
| `caddy` | Caddy 2 | Reverse proxy (not yet active — Phase 4) |

---

## 3. Tech Stack Decisions (DO NOT REWRITE)

- **Backend:** FastAPI (Python 3.12, async), SQLAlchemy 2.0 async,
  Alembic migrations, Pydantic v2 schemas.
- **Frontend (planned, not yet built):** Server-rendered Jinja2 +
  Tailwind CSS via CDN + HTMX (30s polling) + Chart.js. NO build
  pipeline. NO React/Vue/Svelte. Goal: <1500 lines of webapp code.
- **Package manager:** `uv` (Astral). Lockfile `uv.lock` committed.
- **Dev dependencies in container:** Baked in via Dockerfile
  `ARG INSTALL_DEV=true` → `uv pip install pytest pytest-asyncio
  pytest-cov`. Set to `false` for prod images (saves ~30MB).
- **Migrations:** Manual via
  `docker compose exec webapp alembic upgrade head`.
- **Test framework:** pytest 8.3 + pytest-asyncio 0.24 + httpx 0.27.
  Tests run INSIDE the webapp container against the live stack
  (no mocks).

---

## 4. Domain Model — Hierarchical Multi-Tenancy

```
Organization (owns Sigencloud credentials — Fernet-encrypted at rest)
  └── Site (logical group, e.g. "Wijk Sint-Jan")
        └── Building (physical house — sigen_system_id, PV kWp, battery kWh)
              └── BuildingAssignment (links a tenant User to one building)
```

---

## 5. Access Control (CRITICAL — defines the entire security model)

Three roles, three data-scopes, ONE central function:

| Role | Sees |
|------|------|
| `sparki_staff` | ALL active buildings, across all orgs |
| `site_owner` | All active buildings in sites of the user's org |
| `tenant` | Only buildings explicitly assigned via `building_assignments` |

**The central function:** `app/core/permissions.py :: buildings_visible_to(user, db) -> set[UUID]`

Every route that touches a building MUST derive visibility from this
function. The `_check_visibility()` helper in `buildings/routes.py`
raises 403 BEFORE the existence (404) check — this prevents UUID
enumeration attacks.

---

## 6. Audit Logging (GDPR Compliance)

Two-channel design:

- **Postgres `audit_log` table:** only `denied` access attempts
  (queryable evidence for GDPR audits, government clients).
- **Structured stdout logs (INFO):** every `allowed` access
  (operational debug, captured by Docker, cheap and high-volume).

**Critical implementation detail:** `log_access_denied()` opens its
OWN database session and commits BEFORE the route raises
`HTTPException`. Without this, the request-scoped session rolls back
on the exception and the audit row is lost. See
`app/core/audit_service.py :: _write_audit_row()`.

---

## 7. Data Ingestion (Node-RED, 2 Flows)

### Flow A: "Sparki Mock Ingestion"
- Trigger: every 60s
- Postgres SELECT all active buildings (per-building context)
- JS function generates realistic mock energy data (PV curve based on
  hour of day, evening load boost, battery SoC carried in `flow`
  context per building)
- HTTP POST → InfluxDB v2 `/api/v2/write` with Bearer token
- Measurement: `sigen`, tags: `org_id,site_id,building_id,source=mock`
- Fields: `pv_kw, load_kw, ev_charger_kw, heatpump_kw, battery_kw,
  battery_soc, grid_kw, export_kw, import_kw, self_consumption_kw`
- **Grid sign convention:** positive = exporting (Sigencloud-aligned)

### Flow B: "ENTSO-E Prices (BE)"
- Trigger: hourly
- **Auto-routing in JS function:**
  - If `ENTSOE_API_TOKEN` is set and not the placeholder
    `changeme-entsoe-token` → LIVE path:
    GET `https://web-api.tp.entsoe.eu/api`, parse XML
    (`Publication_MarketDocument > TimeSeries > Period > Point`),
    write `source=entsoe`
  - Otherwise → MOCK path: realistic BE day-ahead price pattern
    (night baseload, morning peak, midday solar dip with ~15% chance
    of negative prices, evening peak), write `source=mock`
- Same downstream: Line Protocol → InfluxDB
- Measurement: `price`, tags: `zone=BE,source=entsoe|mock`,
  field: `eur_per_mwh`
- **One env var to switch:** `ENTSOE_API_TOKEN` in `.env`,
  recreate node-red container

---

## 8. REST API Endpoints

All under base path. JWT required unless noted.

| Method | Path | Purpose | Public? |
|--------|------|---------|---------|
| GET | `/` | Root info | ✓ |
| GET | `/healthz` | Liveness probe | ✓ |
| GET | `/readyz` | Readiness (Postgres + InfluxDB ping) | ✓ |
| GET | `/api/me` | Current user identity + role booleans | — |
| GET | `/api/me/roles` | List of available roles | — |
| GET | `/api/buildings` | Buildings visible to current user | — |
| GET | `/api/buildings/{id}/current` | Latest 10 measurements | — |
| GET | `/api/buildings/{id}/history` | Time-series, default 24h @ 60s | — |
| GET | `/api/prices/{zone}` | Day-ahead price series (BE/NL/DE-LU) | — |
| GET | `/api/prices/{zone}/current` | Current hourly price | — |

History query guards: `interval_seconds` 10–3600, max range 30 days,
`end` must be after `start`.

---

## 9. Seed Data (`scripts/seed.py`, Idempotent)

- **3 demo users** — single shared password for the demo seed:
  - `staff@sparki.test` → role `sparki_staff` (Sparki org)
  - `owner@sigenburg.test` → role `site_owner` (Sigenburg org)
  - `tenant@sigenburg.test` → role `tenant` (assigned to Woning 1)
- **2 organisations:** Sparki (own), Stad Sigenburg (customer)
- **1 site:** "Wijk Sint-Jan"
- **10 buildings:** "Woning 1" .. "Woning 10"
- **1 building assignment:** tenant → Woning 1

> The actual demo password lives in `.env` only; never in this file
> or anywhere else committed to git.

---

## 10. Configuration

All secrets live in `.env` (gitignored). See `.env.example` for the
full list of variables. The relevant categories are:

- **Postgres:** `POSTGRES_PASSWORD`
- **InfluxDB:** `INFLUXDB_ADMIN_PASSWORD`, `INFLUXDB_TOKEN`
- **Keycloak:** `KEYCLOAK_ADMIN_PASSWORD`, `KEYCLOAK_CLIENT_SECRET`
- **Node-RED:** `NODERED_ADMIN`, `NODERED_ADMIN_PASSWORD_HASH`
  (bcrypt, must be SINGLE-quoted in `.env` to prevent `$` interpolation)
- **ENTSO-E:** `ENTSOE_API_TOKEN` (placeholder by default,
  mock-fallback in Node-RED until a real token arrives — 1–3 working
  days after mailing `transparency@entsoe.eu`)
- **ENTSO-E:** `ENTSOE_BIDDING_ZONE` (default `10YBE----------2` for BE)
- **App secrets:** `FERNET_KEY` (for at-rest encryption of Sigencloud
  credentials), session secret, etc.

---

## 11. Test Suite (39 tests, 100% passing, ~6s runtime)

Run: `docker compose exec -e KEYCLOAK_URL=http://keycloak:8080 webapp pytest tests/`

| File | Count | Covers |
|------|-------|--------|
| `test_auth.py` | 7 | `/api/me`, `/api/me/roles`, auth failures (401) |
| `test_buildings_list.py` | 6 | Per-role visibility, response shape, sort order |
| `test_buildings_detail.py` | 11 | current + history, 403 patterns, validation |
| `test_prices.py` | 8 | zones, ranges, MWh→kWh conversion, auth |
| `test_audit.py` | 3 | denied → DB, allowed → not DB |
| `test_health.py` | 3 | root, /healthz, /readyz |

All tests are **integration tests** — no mocks. Each token is fetched
from the real Keycloak, building IDs are discovered dynamically via
the staff endpoint, audit_log is queried directly via SQLAlchemy.

---

## 12. Project Plan & Phase Status

| Phase | Period | Status |
|-------|--------|--------|
| Fase 1: Foundation (Docker + DB + Auth) | up to 24 May | ✅ Done |
| Fase 2: Backend (Ingestion + API + Permissions + ENTSO-E) | up to 31 May | ✅ Done |
| **Fase 3: UI (Jinja + HTMX + Chart.js)** | **1–7 June** | 🔜 **Next** |
| Fase 4: Sigencloud live + Caddy + delivery | 8–12 June | ⏳ |

---

## 13. The Current State (always update this section!)

**As of:** 27 May 2026

**Completed this week:**
- [x] Step 2.5 — Permission layer (`buildings_visible_to`)
- [x] Step 2.5C — Audit logging with independent session+commit
- [x] Step 2.7 — ENTSO-E live-ready integration + mock fallback
- [x] Bonus — Automated pytest integration suite (39 tests, all passing)
- [x] Dockerfile: `INSTALL_DEV` build arg for dev tools
- [x] pytest config: cache to `/tmp` (sparki user is non-root)
- [x] Test fixtures: function-scoped client to handle
      pytest-asyncio v0.24's per-test event loop
- [x] Project ahead of schedule — Fase 2 done 4 days early
- [x] **Step 3.1 — Tailwind + Jinja2 base layout** *(done 28 May, 4 days early)*
- [x] New `app/web/` package: `session.py`, `templates_env.py`,
        `routes.py` + `__init__.py`
  - [x] Sparki dark theme (navy `#0A1F44` / red `#E63946`) matching
        sparki.be marketing site — Tailwind via CDN with custom tokens
  - [x] Inter via Google Fonts, HTMX 1.9 + Chart.js 4 pre-loaded
  - [x] Role-aware sidebar (Portfolio + Gebouwen + Prijzen for all;
        Gebruikers for staff/owner; Instellingen for staff only)
  - [x] Signed-cookie session via `itsdangerous` (HMAC, payload =
        user UUID only, 8h TTL, `HttpOnly` + `SameSite=Lax`,
        `Secure` in prod)
  - [x] Dual-auth pattern: `get_session_user_optional/_required` for
        HTML routes; existing `get_current_user` (Bearer) untouched
        for JSON API
  - [x] Dev-only stub login at `/dev/login?as_=staff|owner|tenant`
        (404 in production)
  - [x] `/api` JSON descriptor (previously at `/`); HTML now owns root
  - [x] StaticFiles mount at `/static`
  - [x] Templates + static bind-mounted in dev (hot-reload) and
        baked into image for prod (Dockerfile `COPY` lines)
  - [x] 6 new integration tests in `tests/test_web_layout.py`
        (anonymous render, per-role dev login, role-aware sidebar,
        logout cookie clearing, tampered-cookie resistance)
  - [x] **All 45 tests passing** (39 existing + 6 new)
  - [x] **Bug caught + fixed in initial delivery:** the `/login`
        route had a union return type `HTMLResponse | RedirectResponse`
        which FastAPI rejects unless `response_model=None` is set.
        Lesson logged below (gotcha #9).


**In progress:**
- [ ] Waiting for ENTSO-E API token approval (mail sent — typically
      1–3 working days)
- [ ] Waiting for Sigencloud API token from customer

**Next session:**
- [ ] Step 3.2 — Real Keycloak Authorization Code + PKCE flow
  - [ ] `/login` builds the Keycloak auth URL with `state` + PKCE
        `code_verifier` (stash both in a short-lived signed cookie)
  - [ ] `/auth/callback` exchanges `code` → tokens, calls
        `set_session_cookie(user.id)`, redirects to original target
  - [ ] `/logout` also hits Keycloak's `end_session_endpoint`
  - [ ] Remove `/dev/login` (or keep behind `ENVIRONMENT=development`
        for thesis-demo convenience)
  - [ ] Mobile sidebar drawer (the header hamburger is a placeholder)
- [ ] Step 3.3 — Portfolio page (`GET /` → live list of buildings the
      user can see, rendered from `/api/buildings` data)

---

## 14. Key Architectural Decisions Log

> Why we did what we did. Don't repeat these debates.

1. **Server-rendered HTML over SPAs.** Smaller surface area, no
   build pipeline, easier handoff. Capped at ~1500 lines of code.
2. **HTMX over JavaScript frameworks.** 30s polling for live tiles
   is enough. No state management library needed.
3. **InfluxDB Line Protocol via raw HTTP POST.** Same pattern for
   mock ingestion AND future Sigencloud poller. Universal.
4. **`buildings_visible_to(user)` as the ONE source of truth.**
   Every route calls it. Bugs here would be catastrophic, so
   centralizing and testing exhaustively pays off.
5. **Permission check BEFORE existence check.** Prevents UUID
   enumeration. A tenant requesting any non-visible UUID gets 403
   identical to requesting a real-but-unauthorized building.
6. **Audit log in its own DB transaction.** Survives request
   rollback on `HTTPException`. Implemented via `AsyncSessionLocal()`
   inside `_write_audit_row()`.
7. **Allowed access → stdout logs only. Denied → DB.** Splits
   high-volume operational events from low-volume security events.
   GDPR compliant, storage efficient.
8. **Live-ready ENTSO-E + mock fallback in one flow.** One env var
   to switch. No code change when token arrives. Production pattern.
9. **Tests run inside the webapp container against the live stack.**
   No mocks. Tests prove the SYSTEM works, not just unit logic.
10. **pytest-asyncio function-scoped client fixture.** Session scope
    fails because v0.24 makes a new event loop per test. Documented
    in `conftest.py` so future-me doesn't change it.

---

## 15. Recurring Operational Gotchas

> Things that broke once and could break again.

- **OneDrive on Windows corrupts `.env` files with UTF-8 box-drawing
  chars.** Fix: regenerate with ASCII-only PowerShell.
- **PowerShell `cd` with paths containing `-` and spaces** requires
  full-path quoting: `cd "$env:USERPROFILE\OneDrive - Gridlink\..."`
- **PowerShell shell escaping for Flux queries** mangles double
  quotes. Use here-strings (`@"..."@`) or InfluxDB UI Data Explorer.
- **`uv sync --frozen` ignores optional-dependencies** even with
  build args. Use explicit `uv pip install pytest...` instead.
- **Bcrypt hashes in `.env` need SINGLE quotes** (`'$2b$08$...'`)
  to prevent `$` interpolation.
- **Flux requires DOUBLE quotes for strings** but Python `repr()`
  uses singles. Helper: `_flux_string_array()` in `buildings/service.py`.
- **Docker Desktop on Windows + OneDrive single-file bind mounts**
  fail on EBUSY. Use named volumes + seed copy via
  `cp /seed/flows.json /data/flows.json` after image rebuild.
- **Node-RED `entrypoint.sh` overrides any CMD wrapper.** Cannot
  auto-seed via Dockerfile alone. Bake into `/seed/`, then
  `docker compose exec -u root node-red sh -c "cp ..."` after deploy.
- **Audit log rollback bug:** request session rolls back on
  HTTPException. Audit row needs OWN session+commit. See decision #6.
- **FastAPI rejects route handlers whose return type is a Union of
  Response subclasses.** Symptom: `FastAPIError: Invalid args for
  response field! Hint: check that starlette.responses.HTMLResponse
  | starlette.responses.RedirectResponse is a valid Pydantic field
  type.` Fix: add `response_model=None` to the `@router.get(...)`
  decorator. The error fires at decoration time → uvicorn fails to
  import the app → container crash-loops on startup. Caught during
  Step 3.1 delivery on the `/login` route; static syntax checks
  (`ast.parse`) miss this because the validation only runs when the
  decorator actually executes.
---

## 16. PowerShell Convenience Functions

For testing in PowerShell. Recreate at session start (these don't
persist across PowerShell sessions):

```powershell
function Get-SparkiToken {
    param([string]$Username, [string]$Password)
    $body = @{
        grant_type    = "password"
        client_id     = "webapp"
        client_secret = $env:KEYCLOAK_CLIENT_SECRET  # set this from your .env
        username      = $Username
        password      = $Password
        scope         = "openid"
    }
    return (Invoke-RestMethod -Method POST `
        -Uri "http://localhost:8080/realms/sparki/protocol/openid-connect/token" `
        -Body $body).access_token
}

# Example:
$tokenStaff = Get-SparkiToken "staff@sparki.test" "<from .env>"
$headersStaff = @{ Authorization = "Bearer $tokenStaff" }
```

---

## 17. URLs Quick-Reference

- Webapp API:   http://localhost:8000
- Swagger UI:   http://localhost:8000/docs
- ReDoc:        http://localhost:8000/redoc
- Keycloak:     http://localhost:8080
- Node-RED:     http://localhost:1880
- InfluxDB UI:  http://localhost:8086

---

## 18. Standing Instructions for Claude / future-me

When opening a new chat:
1. Read this file end-to-end before suggesting anything.
2. Check section 13 ("The Current State") for what was done last.
3. Stick to the tech stack in section 3 — don't propose React, build
   pipelines, microservices, or anything that contradicts decisions
   logged in section 14.
4. The webapp should stay <1500 lines. If a feature requires more,
   push back and find a smaller approach.
5. All new endpoints MUST go through `buildings_visible_to()` for
   building-scoped data, OR be explicitly market-data (prices) with
   only auth required.
6. Every change that affects an endpoint needs a corresponding test
   in the existing `tests/` directory. Test count should only grow.
7. Never commit secrets, passwords, tokens, or API keys to git or
   to documentation files. Refer to `.env` / `.env.example` for the
   shape, never for the values.
