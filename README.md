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
| Webapp API    | http://localhost:8000        | FastAPI                                |
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

# Inloggen (web UI)

The dashboard uses Keycloak's OAuth 2.0 Authorization Code + PKCE flow.

1. Open the dashboard at **http://localhost:8000/**
2. Click **Inloggen** → you're redirected to the Keycloak login page
3. Sign in with one of the demo accounts (password from `.env`)
4. Keycloak redirects back to `/auth/callback`, a session cookie is set,
   and you land on the portfolio dashboard
5. **Uitloggen** clears the session and logs you out at Keycloak too

The browser never holds a JWT — only an `HttpOnly`, signed session
cookie containing the user's ID. Permissions are re-derived server-side
on every request.

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

## REST API

All endpoints require a Bearer JWT from Keycloak (except `/healthz`,
`/readyz`, `/`).

| Method | Path                                  | Purpose                                 |
| ------ | ------------------------------------- | --------------------------------------- |
| GET    | `/healthz`                            | Liveness probe                          |
| GET    | `/readyz`                             | Readiness (Postgres + InfluxDB ping)    |
| GET    | `/api/me`                             | Current user identity + role booleans   |
| GET    | `/api/me/roles`                       | Available roles                         |
| GET    | `/api/buildings`                      | Buildings visible to current user       |
| GET    | `/api/buildings/{id}/current`         | Latest measurements (10 fields)         |
| GET    | `/api/buildings/{id}/history`         | Time-series (default 24h @ 60s)         |
| GET    | `/api/prices/{zone}`                  | Day-ahead price series (BE/NL/DE-LU)    |
| GET    | `/api/prices/{zone}/current`          | Current hourly price                    |

See `/docs` for full interactive documentation.

  The table above lists the JSON API (Bearer-authenticated). The web UI
  adds server-rendered HTML routes (`/`, `/login`, `/auth/callback`,
  `/logout`) that authenticate via session cookie instead. The former
  root descriptor previously at `/` now lives at `GET /api`. See
  CONTEXT.md §8 for the full HTML route list.

---

## Running the test suite

  The webapp container ships with a pytest integration suite (54 tests)
  that validates the full stack — auth, permissions, API endpoints,
  audit logging, the HTML UI layer, and the Keycloak OAuth login flow —
  against the live Docker containers. No mocks.

```bash
docker compose exec -e KEYCLOAK_URL=http://keycloak:8080 webapp pytest tests/
```

  Expected output: `54 passed in ~7s`.

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
- [~] **Phase 3 — UI fundering** (1–7 juni) — *in progress, ahead of schedule*
    - [x] Step 3.1 — Jinja2 + Tailwind base layout, role-aware sidebar,
          signed-cookie session
    - [x] Step 3.2 — Keycloak Authorization Code + PKCE login flow,
          full SSO logout
    - [ ] Step 3.3 — Portfolio page (live building list)
    - [ ] Step 3.4 — Building detail (live tiles + Chart.js history)- [ ] **Phase 4 — Live data + oplevering** (8–12 juni)

---

## Documentation

- `CONTEXT.md`           — living project context (read this first!)
- `docs/architecture.md` — system architecture details
- `docs/deployment.md`   — handover notes for IT deployment team
- `docs/api.md`          — REST API reference
