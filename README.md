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

# 2. Start the infrastructure containers
docker compose up -d

# 3. Verify
docker compose ps
docker compose logs -f
```

Endpoints (development, direct via host):
- PostgreSQL:  `localhost:5432`
- InfluxDB UI: http://localhost:8086
- Keycloak:    http://localhost:8080  (admin / `${KEYCLOAK_ADMIN_PASSWORD}`)
- Webapp:      http://localhost:8000  *(activated in next step)*
- Caddy:       http://localhost       *(activated in last step)*

---

## Project status

- [x] **Phase 1 — Fundering** (19–24 mei)
  - [x] Directory structure
  - [x] `.env.example` template
  - [x] `docker-compose.yml` (postgres + influxdb + keycloak active)
  - [ ] Webapp container + Dockerfile
  - [ ] PostgreSQL schema + Alembic migrations
  - [ ] Keycloak realm config
  - [ ] Seed script
- [ ] Phase 2 — Data-laag (25–31 mei)
- [ ] Phase 3 — UI fundering (1–7 juni)
- [ ] Phase 4 — Live data + oplevering (8–12 juni)

---

## Documentation

- `docs/architecture.md` — system architecture details
- `docs/deployment.md`   — handover notes for IT deployment team
- `docs/api.md`          — REST API reference
