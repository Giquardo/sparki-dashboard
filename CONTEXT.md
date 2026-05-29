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
**Status:** Phases 1–3 complete, full dashboard live, ~10 days ahead
of schedule. Phase 4 (Sigencloud live + Caddy + delivery) is next.

---

## 2. Architecture (6 Containers, Docker Compose)

| Container | Tech | Purpose |
|-----------|------|---------|
| `postgres` | PostgreSQL 16 | Organisations, sites, buildings, users, audit_log |
| `influxdb` | InfluxDB 2.7 | Time-series: energy metrics + ENTSO-E prices |
| `keycloak` | Keycloak 25 | OAuth2/OIDC, JWT, 3 roles |
| `node-red` | Node-RED 4 (custom image) | Data ingestion (2 flows: mock energy + ENTSO-E prices) |
| `webapp` | FastAPI + uv + asyncpg | REST API, HTML UI, JWT validation, permissions, audit |
| `caddy` | Caddy 2 | Reverse proxy (not yet active — Phase 4) |

---

## 3. Tech Stack Decisions (DO NOT REWRITE)

- **Backend:** FastAPI (Python 3.12, async), SQLAlchemy 2.0 async,
  Alembic migrations, Pydantic v2 schemas.
- **Frontend:** Server-rendered Jinja2 + Tailwind CSS via CDN + HTMX
  (30s polling) + Chart.js. NO build pipeline. NO React/Vue/Svelte.
  Goal: <1500 lines of webapp code (currently met).
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

### Flow B: "ENTSO-E Prices (BE)" — LIVE since 28 May
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
  `docker compose up -d --force-recreate node-red`

---

## 8. REST API + HTML routes

### JSON API (Bearer JWT)

All under base path. JWT required unless noted.

| Method | Path | Purpose | Public? |
|--------|------|---------|---------|
| GET | `/healthz` | Liveness probe | ✓ |
| GET | `/readyz` | Readiness (Postgres + InfluxDB ping) | ✓ |
| GET | `/api` | JSON descriptor (was `/` before Phase 3) | ✓ |
| GET | `/api/me` | Current user identity + role booleans | — |
| GET | `/api/me/roles` | List of available roles | — |
| GET | `/api/buildings` | Buildings visible to current user | — |
| GET | `/api/buildings/{id}/current` | Latest 10 measurements | — |
| GET | `/api/buildings/{id}/history` | Time-series, default 24h @ 60s | — |
| GET | `/api/prices/{zone}` | Day-ahead price series (BE/NL/DE-LU) | — |
| GET | `/api/prices/{zone}/current` | Current hourly price | — |

History query guards: `interval_seconds` 10–3600, max range 30 days,
`end` must be after `start`.

### HTML routes (session cookie)

| Method   | Path                  | Page                                    | Roles |
|----------|-----------------------|-----------------------------------------|-------|
| GET      | `/`                   | Portfolio — per-site executive overview | logged-in users |
| GET      | `/buildings`          | Gebouwen — card grid grouped by site    | logged-in users |
| GET      | `/buildings/{id}`     | Building detail (live tiles + 2 charts) | visible to user |
| GET      | `/prices`             | Prijzen — current price + 48u chart     | any logged-in user |
| GET      | `/users`              | Gebruikers — orgs/sites/people tree     | staff + owner |
| GET      | `/login`              | 303 → Keycloak                          | ✓ |
| GET      | `/auth/callback`      | OAuth code → session cookie             | ✓ |
| GET/POST | `/logout`             | Full SSO logout                         | ✓ |
| GET      | `/dev/login`          | Dev-only stub (404 in prod)             | ✓ (dev) |
| —        | `/static/*`           | CSS, images                             | ✓ |

HTML routes authenticate via the signed `sparki_session` cookie, NOT
the Bearer JWT. The JSON `/api/*` routes are unchanged and still
Bearer-only.

### Cookie-auth data routes (consumed by HTMX / Chart.js)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/buildings/{id}/tile` | Compact 3-metric card tile (HTMX fragment) |
| GET | `/buildings/{id}/tile/full` | Full live-metric set for detail page (HTMX) |
| GET | `/buildings/{id}/history.json` | History JSON for Chart.js |
| GET | `/prices/{zone}.json` | Price series JSON for Chart.js |
| GET | `/prices/{zone}/current.json` | Current price for headline tile |
| GET | `/sites/{id}/live.json` | Aggregate live PV for a site |

All building-scoped data routes pass through `buildings_visible_to()`.
Prices are market data — auth required but no building scope.

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
- **ENTSO-E:** `ENTSOE_API_TOKEN` — **live since 28 May**;
  mock-fallback path still intact if the token is unset or placeholder
- **ENTSO-E:** `ENTSOE_BIDDING_ZONE` (default `10YBE----------2` for BE)
- **App secrets:** `WEBAPP_SECRET_KEY` (session cookie HMAC, ≥32 chars),
  `FERNET_KEY` (for at-rest encryption of Sigencloud credentials)

---

## 11. Test Suite (78+ tests, 100% passing, ~9s runtime)

Run: `docker compose exec -e KEYCLOAK_URL=http://keycloak:8080 webapp pytest tests/`

| File | Count | Covers |
|------|-------|--------|
| `test_auth.py` | 7 | `/api/me`, `/api/me/roles`, auth failures (401) |
| `test_buildings_list.py` | 6 | Per-role visibility, response shape, sort order |
| `test_buildings_detail.py` | 10 | Detail page, full tile, history.json, prices.json |
| `test_prices.py` | 8 | zones, ranges, MWh→kWh conversion, auth |
| `test_audit.py` | 3 | denied → DB, allowed → not DB |
| `test_health.py` | 3 | root, /healthz, /readyz |
| `test_web_layout.py` | 6 | layout shell, dev login, role-aware sidebar |
| `test_keycloak_login.py` | 9 | real OAuth flow, callback edges, SSO logout |
| `test_portfolio.py` | 8 | Gebouwen card grid, grouped-by-site, tile permissions |
| `test_portfolio_restructure.py` | 9 | Per-site summary, site live aggregate |
| `test_prices_page.py` | 6 | Prijzen page, current.json, zone validation |
| `test_users_page.py` | 6 | Staff/owner/tenant scoping, hierarchy structure |

All tests are **integration tests** — no mocks. Each token is fetched
from the real Keycloak, building IDs are discovered dynamically via
the staff endpoint, audit_log is queried directly via SQLAlchemy.

---

## 12. Project Plan & Phase Status

| Phase | Period | Status |
|-------|--------|--------|
| Fase 1: Foundation (Docker + DB + Auth) | up to 24 May | ✅ Done |
| Fase 2: Backend (Ingestion + API + Permissions + ENTSO-E) | up to 31 May | ✅ Done |
| Fase 3: UI (Jinja + HTMX + Chart.js) | 1–7 June | ✅ **Done, ~10 days early** |
| **Fase 4: Sigencloud live + Caddy + delivery** | **8–12 June** | 🔜 **Next** |

---

## 13. The Current State (always update this section!)

**As of:** 28 May 2026 — Phase 3 complete, every sidebar link works.

**Completed (Phases 1–3):**
- [x] Phase 1 — Foundation: Docker stack, schemas, Keycloak realm, seed
- [x] Phase 2 — Backend: ingestion, REST API, permissions, audit, ENTSO-E
- [x] Step 3.1 — Tailwind/Jinja layout, role-aware sidebar, cookie session
- [x] Step 3.2 — Keycloak Authorization Code + PKCE login, full SSO logout
- [x] Step 3.3 — Portfolio page with HTMX-lazy live tiles (30s refresh)
- [x] **Step 3.4 — Building detail page**
  - Full live-metric set (10 fields) grouped into Productie / Verbruik /
    Batterij / Net section cards
  - Energiestroom chart: PV + verbruik + net (kW) with **ENTSO-E price
    overlay** on a right axis (real day-ahead prices since 28 May)
  - Batterijlading chart: SoC% with **price overlay** on a right axis
    (same dual-axis pattern, shared `buildPriceDataset()` helper
    between both charts)
  - Cookie-auth web data routes (`/buildings/{id}/history.json`,
    `/prices/{zone}.json`) so the browser never holds a JWT
  - Timezone fix: `nl_time` Jinja filter converts UTC → Europe/Brussels
    at render time (handles DST automatically)
- [x] **Step 3.5 — Prijzen page** (`/prices`)
  - Headline tiles: current price in €/MWh + €/kWh + valid hour
  - 48u day-ahead chart with ENTSO-E attribution
  - New `/prices/{zone}/current.json` cookie-auth route
- [x] **Step 3.6 — Portfolio/Gebouwen restructure**
  - `/` is now the executive overview: per-site summary cards with
    building count + total kWp + total kWh + lazy-loaded live PV
  - `/buildings` (Gebouwen) is the card grid, grouped by site under
    collapsible native `<details>` sections, each with a mini-summary
    header (count + kWp + kWh)
  - Shared `_grouped_by_site()` helper so Portfolio and Gebouwen
    derive their layout from the same data
  - New `/sites/{id}/live.json` aggregate route (sums visible
    buildings' PV; 403 if no visible buildings in that site)
- [x] **Step 3.7 — Gebruikers page** (`/users`)
  - Staff: all orgs → owners + tenants grouped by site
  - Owner: their org only, tenants grouped by site with building chips,
    sees fellow site_owners too
  - Tenant: 403 + audit row (page is for managers, not residents)
  - "Bewoners zonder gebouw" warning section surfaces data-integrity
    issues (a tenant exists but has no assignment yet)
- [x] **Test suite: 78+ tests passing** across 12 files. No mocks —
  every test runs against the live stack.
- [x] Pre-delivery validation upgrade kept paying off: every new
  router is import-tested against a real `FastAPI()` mount before
  shipping; templates rendered with edge cases (empty, single-tenant,
  charging vs discharging, import vs export, no-data).

**In progress:**
- [ ] Waiting for Sigencloud API token from customer (ENTSO-E live;
      Sigencloud still mock until token arrives)

**Next session — Phase 4:**
- [ ] Swap Node-RED mock ingestion → live Sigencloud poll once the
      customer token arrives; verify field mapping against mySigen
- [ ] Activate Caddy as reverse proxy + Let's Encrypt HTTPS
- [ ] Light admin tools for `sparki_staff` (create org / site / user)
- [ ] Backup scripts (Postgres dump + InfluxDB snapshot)
- [ ] Deployment-team handover docs

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
11. **Server-side session cookie for the UI, not localStorage JWTs.**
    The browser never holds a JWT. After Keycloak login we store only
    the user's UUID in an HMAC-signed `HttpOnly` cookie. Permissions are
    re-derived from our own DB each request. XSS can't exfiltrate a token
    that isn't there.
12. **Authorization Code + PKCE even though webapp is a confidential
    client.** Defense in depth, OAuth 2.1-aligned, ~10 extra lines. The
    PKCE verifier lives in a short-lived signed "flight" cookie with a
    DIFFERENT signing salt than the session cookie, so a leaked flight
    cookie can never be replayed as a session.
13. **Token exchange uses the INTERNAL Keycloak URL; browser-facing
    URLs use the PUBLIC one.** `keycloak_internal_url` (Docker network)
    for server-to-server `/token`; `keycloak_public_url` (localhost:8080)
    for the auth + logout URLs the browser must reach. Mirrors the
    existing split in `app/auth/keycloak.py`.
14. **JWT validation stays in ONE place.** The callback decodes the
    Keycloak-issued access token via the existing `decode_token()`
    rather than re-implementing verification. Keycloak-specific logic
    lives only in `app/auth/keycloak.py` + `app/web/oauth.py`.
15. **Full SSO logout.** `/logout` clears our cookie and bounces through
    Keycloak's `end_session_endpoint` so the IdP session ends too —
    otherwise a "logged out" user could silently re-auth on next /login.
16. **Cookie-auth data routes instead of letting JS call the JSON API.**
    The browser only has a session cookie, not a Bearer token, so Chart.js
    can't hit `/api/*` directly. Adding parallel cookie-auth `.json`
    routes on the web side that reuse the SAME service functions
    (`get_history`, `get_price_series`) keeps the JSON API pure
    (Bearer-only, 39 tests still valid) and the browser tokenless.
17. **`<details>` / `<summary>` for collapsible sections, not JS.** The
    Gebouwen page (sections per site) and Gebruikers page (sections per
    org) both use native HTML — zero JavaScript, keyboard-accessible,
    screen-reader friendly. Tailwind's `group-open:` variant rotates the
    chevron via CSS only. State doesn't persist across page loads, which
    is fine at thesis scale.
18. **Aggregate routes never expose unauthorized buildings.** The
    `/sites/{id}/live.json` endpoint sums only the buildings the caller
    can already see, so a user can never learn about a building's
    existence via the aggregate. If the site has no visible buildings
    → 403 + audit row, identical to the direct-access response.
19. **`nl_time` filter converts UTC at render time.** InfluxDB stores
    UTC (correct); the UI shows Europe/Brussels (what the user wants).
    The `templates_env.py` filter uses `zoneinfo`, so DST is handled
    automatically. Naive datetimes are assumed UTC, then converted.
20. **HTML routes use cookie session; JSON API uses Bearer. Same DB user.**
    A single User row backs both auth paths. The HTML side's
    `get_session_user_optional`/`_required` and the API side's
    `get_current_user` both produce a `CurrentUser` Pydantic model the
    rest of the code consumes identically. Two front doors, one room.
21. **Inline scope-by-org_id for the Users page, NOT a `users_visible_to()`
    helper.** The rules are a 3-line WHERE-by-role mapping called from
    exactly one route. A `core/permissions.py`-style abstraction would be
    premature; if a second route ever needs the same logic, extract then.
    (Contrast with `buildings_visible_to()`, which is called from many
    places and centralizing pays off.)

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
  Response subclasses** unless `response_model=None` is on the
  decorator. Fires at decoration/import time → uvicorn fails to load
  app → container crash-loops. `ast.parse` misses it; only a real
  import + FastAPI mount catches it. Now part of pre-delivery validation.
- **`--import-realm` only imports if the realm does NOT already exist.**
  Keycloak persists realms in Postgres (`KC_DB: postgres`). Once `sparki`
  exists, edits to `keycloak/realm-export/sparki-realm.json` are ignored
  on subsequent boots. To make the JSON authoritative again you must drop
  the realm first: stop keycloak → `DROP SCHEMA keycloak CASCADE; CREATE
  SCHEMA keycloak;` in Postgres → restart keycloak → re-run `seed.py`.
  For one-off fixes, edit the client in the Keycloak admin UI instead.
- **Keycloak redirect_uri matching is literal string comparison.**
  `localhost` and `127.0.0.1` are the same host but DIFFERENT strings,
  so a callback built with one won't match a redirect URI registered
  with the other. `request.url_for()` builds the callback from whatever
  host the browser used. Fix: register BOTH hostname variants in the
  webapp client (Valid redirect URIs, Web origins, post-logout URIs), or
  always access the app via the same hostname. Symptom seen: Keycloak
  "Ongeldige parameter: redirect_uri".
- **Tailwind CDN ignores opacity modifiers on custom colors.** Classes
  like `bg-sparki-navy/40` silently drop their background entirely
  under the CDN build (no PostCSS, no JIT). Use solid colors only on
  custom tokens — `bg-sparki-navy` + a separate border for elevation.
  Standard Tailwind colors (`bg-slate-900/40`) still work normally.
- **`docker compose restart` does NOT re-read `.env`.** Env vars are
  injected at container creation time. When you change `.env` (e.g.
  `ENTSOE_API_TOKEN`), `restart` reuses the old values. Use
  `docker compose up -d --force-recreate <service>` instead. Verify
  with `docker compose exec <service> printenv VAR_NAME`.
- **Charts cross-day price alignment needs full date+hour as the key.**
  Keying by `getUTCHours()` alone collides hour-14-today with hour-14-
  yesterday over a 24h window, producing a flat-then-jump shape.
  Compose the key as `year-month-day-hour` (UTC) for stable mapping.

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

- Dashboard:    http://localhost:8000
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
