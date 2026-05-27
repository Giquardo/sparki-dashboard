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

---

## Running the test suite

The webapp container ships with a pytest integration suite (39 tests)
that validates the full stack — auth, permissions, API endpoints,
audit logging — against the live Docker containers. No mocks.

```bash
docker compose exec -e KEYCLOAK_URL=http://keycloak:8080 webapp pytest tests/
```

Expected output: `39 passed in ~6s`.

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
- [ ] **Phase 3 — UI fundering** (1–7 juni)
- [ ] **Phase 4 — Live data + oplevering** (8–12 juni)

---

## Documentation

- `CONTEXT.md`           — living project context (read this first!)
- `docs/architecture.md` — system architecture details
- `docs/deployment.md`   — handover notes for IT deployment team
- `docs/api.md`          — REST API reference
