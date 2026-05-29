"""
Sparki webapp — FastAPI entrypoint.

HTML UI routers:
  - buildings_web : /, /buildings, /buildings/{id}, tiles, history.json, prices/{zone}.json, sites/{id}/live.json
  - prices_web    : /prices, /prices/{zone}/current.json
  - users_web     : /users
  - web (routes)  : /login, /auth/callback, /logout, /dev/login
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.auth.keycloak import warm_jwks_cache
from app.auth.routes import router as auth_router
from app.buildings.routes import router as buildings_router
from app.config import settings
from app.core.healthz import collect_health
from app.database import dispose_engine
from app.influx import close_influx_client, get_influx_client
from app.prices.routes import router as prices_router
from app.web.buildings_web import router as web_buildings_router
from app.web.prices_web import router as web_prices_router
from app.web.routes import router as web_router
from app.web.users_web import router as web_users_router

from app import models  # noqa: F401

logging.basicConfig(
    level=logging.DEBUG if not settings.is_production else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("sparki")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info(
        "Sparki webapp v%s starting in %s mode",
        __version__, settings.environment,
    )
    get_influx_client()
    await warm_jwks_cache()
    yield
    logger.info("Sparki webapp shutting down")
    await close_influx_client()
    await dispose_engine()


app = FastAPI(
    title="Sparki Dashboarding API",
    version=__version__,
    description=(
        "Onafhankelijk dashboarding-platform voor Sigenergy-installaties. "
        "Multi-tenant, role-based, ENTSO-E prijsintegratie."
    ),
    lifespan=lifespan,
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
)

_STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
else:
    logger.warning("Static directory not found at %s — /static will 404", _STATIC_DIR)


@app.get("/healthz", tags=["meta"])
async def healthz() -> JSONResponse:
    return JSONResponse(
        content={
            "status": "ok",
            "service": "sparki-webapp",
            "version": __version__,
            "environment": settings.environment,
        }
    )


@app.get("/readyz", tags=["meta"])
async def readyz() -> JSONResponse:
    report = await collect_health()
    http_status = 200 if report["status"] == "ok" else 503
    return JSONResponse(
        content={"service": "sparki-webapp", "version": __version__, **report},
        status_code=http_status,
    )


@app.get("/api", tags=["meta"])
async def api_root() -> dict[str, str]:
    return {
        "message": "Sparki webapp is running.",
        "docs": "/docs",
        "health": "/healthz",
        "readiness": "/readyz",
        "dashboard": "/",
    }


# JSON API routers first.
app.include_router(auth_router)
app.include_router(buildings_router)
app.include_router(prices_router)

# HTML UI routers last (they own browser-facing paths).
app.include_router(web_buildings_router)
app.include_router(web_prices_router)
app.include_router(web_users_router)
app.include_router(web_router)
