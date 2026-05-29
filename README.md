# Sparki — Onafhankelijk Dashboarding-platform

Een multi-tenant dashboard voor Sigenergy-installaties, onafhankelijk van het
mySigen-platform. Bouwt een eigen autorisatie- en presentatielaag bovenop de
Sigencloud API en combineert met ENTSO-E day-ahead prijzen.

**Stage Luik B — Sparki — Giquardo Vandaele**
**Deadline:** 12 juni 2026

---

## Architectuur (6 containers)

| Service   | Tech                           | Rol                                          |
| --------- | ------------------------------ | -------------------------------------------- |
| postgres  | PostgreSQL 16                  | Relationele opslag, audit log                |
| influxdb  | InfluxDB 2.7                   | Tijdreeksopslag energie & prijzen            |
| keycloak  | Keycloak 25                    | Auth, gebruikersbeheer, JWT-uitgifte         |
| node-red  | Node-RED (headless)            | Ingestie Sigencloud + ENTSO-E → InfluxDB     |
| webapp    | FastAPI + Jinja2 + HTMX        | API + UI                                     |
| caddy     | Caddy 2                        | Reverse proxy, HTTPS                         |

---

## Quick start (development)

```bash
# 1. Copy and edit environment variables
cp .env.example .env
# Edit .env — generate FERNET_KEY, set passwords, etc.

# 2. Start all containers
docker compose up -d

# 3. Run database migrations
docker compose exec webapp alembic upgrade head

# 4. Seed demo data (3 users, 2 orgs, 1 site, 10 buildings)
docker compose exec webapp python /app/scripts/seed.py

# 5. Verify
docker compose ps
docker compose logs -f webapp
```

Endpoints (development, direct via host):

| Service       | URL                          | Notes                                  |
| ------------- | ---------------------------- | -------------------------------------- |
| Dashboard     | http://localhost:8000        | The web UI (login required)            |
| Swagger UI    | http://localhost:8000/docs   | Interactive API explorer               |
| ReDoc         | http://localhost:8000/redoc  | Alternative API docs                   |
| Keycloak      | http://localhost:8080        | admin / `${KEYCLOAK_ADMIN_PASSWORD}`   |
| Node-RED      | http://localhost:1880        | admin / `${NODERED_ADMIN_PASSWORD}`    |
| InfluxDB UI   | http://localhost:8086        | Data Explorer for Flux queries         |
| PostgreSQL    | `localhost:5432`             | Direct connection for migrations/debug |
| Caddy         | http://localhost             | *(activated in last step)*             |

---

## Demo accounts (after seeding)

All three accounts use the same password defined in `.env`:

| Email                     | Role           | Sees                              |
| ------------------------- | -------------- | --------------------------------- |
| `staff@sparki.test`       | `sparki_staff` | All 10 buildings, all orgs        |
| `owner@sigenburg.test`    | `site_owner`   | All 10 buildings in Sigenburg org |
| `tenant@sigenburg.test`   | `tenant`       | Only Woning 1                     |

---

## Inloggen (web UI)

The dashboard uses Keycloak's OAuth 2.0 Authorization Code + PKCE flow.

1. Open the dashboard at **http://localhost:8000/**
2. Click **Inloggen** → you're redirected to the Keycloak login page
3. Sign in with one of the demo accounts (password from `.env`)
4. Keycloak redirects back to `/auth/callback`, a session cookie is set,
   and you land on the **Portfolio** page
5. **Uitloggen** clears the session and logs you out at Keycloak too

The browser never holds a JWT — only an `HttpOnly`, signed session
cookie containing the user's ID. Permissions are re-derived server-side
on every request.

After login you land on **Portfolio** — a per-site executive overview.
Drill into **Gebouwen** for the per-building grid (grouped by site,
collapsible), click any building for **live tiles + 24h energy chart
with ENTSO-E price overlay + battery SoC chart**. **Prijzen** shows
day-ahead market prices, and **Gebruikers** is the people/hierarchy
view (visible to managers — staff and site-owners — but not tenants).

**Important — use a consistent hostname.** Keycloak matches redirect URIs
by exact string, so `localhost` and `127.0.0.1` are treated as different.
The seeded realm allows both `http://localhost:8000/*` and
`http://127.0.0.1:8000/*`. If you add another hostname or port, register
it in the Keycloak admin UI under Clients → webapp → Valid redirect URIs,
Web origins, and Valid post logout redirect URIs.

> **Dev shortcut:** in development only, `GET /dev/login?as_=staff`
> (`owner` / `tenant`) logs you straight in as a seeded user without the
> Keycloak round-trip — handy for quick role-switching and demos. This
> route returns 404 when `ENVIRONMENT=production`.

---

## REST API + Web UI

The webapp serves two parallel surfaces:

- A **JSON API** at `/api/*` authenticated with Bearer JWTs (for
  programmatic clients and integrations).
- A **server-rendered HTML UI** at `/` and other browser-facing paths,
  authenticated via signed session cookie. The browser never holds a
  JWT — only an `HttpOnly` cookie carrying the user's UUID. Chart and
  HTMX data is served via parallel cookie-authenticated routes that
  reuse the same service functions as the JSON API.

### JSON API (Bearer JWT)

| Method | Path                            | Purpose                                |
| ------ | ------------------------------- | -------------------------------------- |
| GET    | `/healthz`                      | Liveness probe                         |
| GET    | `/readyz`                       | Readiness (Postgres + InfluxDB ping)   |
| GET    | `/api`                          | JSON descriptor (was `/` before 3.3)   |
| GET    | `/api/me`                       | Current user identity + role booleans  |
| GET    | `/api/me/roles`                 | Available roles                        |
| GET    | `/api/buildings`                | Buildings visible to current user      |
| GET    | `/api/buildings/{id}/current`   | Latest measurements (10 fields)        |
| GET    | `/api/buildings/{id}/history`   | Time-series (default 24h @ 60s)        |
| GET    | `/api/prices/{zone}`            | Day-ahead price series (BE/NL/DE-LU)   |
| GET    | `/api/prices/{zone}/current`    | Current hourly price                   |

### Web UI (session cookie)

| Method   | Path                  | Page                                    |
| -------- | --------------------- | --------------------------------------- |
| GET      | `/`                   | Portfolio — per-site executive overview |
| GET      | `/buildings`          | Gebouwen — card grid grouped by site    |
| GET      | `/buildings/{id}`     | Building detail (live tiles + 2 charts) |
| GET      | `/prices`             | Prijzen — current price + 48u chart     |
| GET      | `/users`              | Gebruikers — orgs/sites/people tree     |
| GET      | `/login`              | 303 → Keycloak                          |
| GET      | `/auth/callback`      | OAuth code → session cookie             |
| GET/POST | `/logout`             | Full SSO logout                         |

See `/docs` for interactive API documentation. Full route inventory
(including the cookie-auth chart-data routes consumed by HTMX /
Chart.js) lives in CONTEXT.md §8.

---

## Running the test suite

The webapp container ships with a pytest integration suite (78+ tests)
that validates the full stack — auth, permissions, API endpoints,
audit logging, the HTML UI layer (portfolio / gebouwen / building
detail / prijzen / gebruikers), the Keycloak OAuth login flow, and
the cookie-auth chart-data routes — against the live Docker
containers. No mocks.

```bash
docker compose exec -e KEYCLOAK_URL=http://keycloak:8080 webapp pytest tests/
```

Expected output: `78 passed in ~9s`.

Run a single test file or test:

```bash
docker compose exec -e KEYCLOAK_URL=http://keycloak:8080 webapp \
    pytest tests/test_buildings_list.py -v

docker compose exec -e KEYCLOAK_URL=http://keycloak:8080 webapp \
    pytest tests/test_audit.py::test_denied_request_writes_audit_row -v
```

---

## Project status

- [x] **Phase 1 — Fundering** (19–24 mei)
  - [x] Directory structure
  - [x] `.env.example` template
  - [x] `docker-compose.yml` — all 5 service containers active
  - [x] Webapp container + Dockerfile (multi-stage, uv-based)
  - [x] PostgreSQL schema + Alembic migrations
  - [x] Keycloak realm config (3 roles, webapp client)
  - [x] Seed script (3 demo users, 2 orgs, 10 buildings)
- [x] **Phase 2 — Data-laag** (25–31 mei)
  - [x] Node-RED mock energy ingestion (10 buildings × 10 fields, 60s)
  - [x] InfluxDB Flux query helpers + Buildings REST API
  - [x] Permission layer (`buildings_visible_to`) + 403 audit logging
  - [x] ENTSO-E day-ahead prices (live-ready with mock fallback)
  - [x] Integration test suite (39 tests, all passing)
- [x] **Phase 3 — UI fundering** (1–7 juni) — *done ~10 days early*
  - [x] Step 3.1 — Jinja2 + Tailwind base layout, role-aware sidebar,
        signed-cookie session
  - [x] Step 3.2 — Keycloak Authorization Code + PKCE login flow,
        full SSO logout
  - [x] Step 3.3 — Portfolio with HTMX-lazy live tiles (30s refresh)
  - [x] Step 3.4 — Building detail page: full live-metric set + two
        Chart.js charts (energy flow & battery SoC, both with ENTSO-E
        price overlay on a dual axis), cookie-auth data routes,
        UTC→Europe/Brussels timezone rendering
  - [x] Step 3.5 — Prijzen page: current price tiles + 48u day-ahead
        chart, market-wide so accessible to every role
  - [x] Step 3.6 — Portfolio (per-site summary) / Gebouwen (card
        grid grouped by site, collapsible sections) restructure
  - [x] Step 3.7 — Gebruikers page: staff sees all orgs, owner sees
        their own; tenants grouped under their assigned building's site
- [ ] **Phase 4 — Live data + oplevering** (8–12 juni)
  - [ ] Swap Node-RED mock ingestion → live Sigencloud (waiting on token)
  - [ ] Activate Caddy reverse proxy + Let's Encrypt HTTPS
  - [ ] Light admin tools for `sparki_staff`
  - [ ] Backup scripts (Postgres dump + InfluxDB snapshot)
  - [ ] Deployment-team handover docs

---

## Documentation

- `CONTEXT.md` — living project context, read this first.
