"""
Keycloak JWT validation.

This module verifies that an incoming JWT was issued by our Keycloak
realm, not yet expired, and signed by a known public key.

Flow on each request:
  1. Read the Bearer token from the Authorization header
  2. Decode the JWT header to find the `kid` (key id)
  3. Look up the matching public key in the cached JWKS
  4. Verify signature + standard claims (exp, iss)
  5. Return the parsed claims as a TokenPayload

JWKS caching: Keycloak rotates signing keys; on a cache miss for a new
`kid` we refetch the JWKS once. We don't refetch on every request.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from jose import jwt
from jose.exceptions import JWTError

from app.auth.schemas import TokenPayload
from app.config import settings

logger = logging.getLogger("sparki.auth")


# ─── JWKS cache ──────────────────────────────────────────────────────
# Cache shape: {kid: jwk_dict, ...}
_jwks_cache: dict[str, dict[str, Any]] = {}
_jwks_last_fetched: float = 0.0
# Minimum seconds between refetches — protects against attack-induced refetches
_JWKS_MIN_REFETCH_INTERVAL = 30.0


def _realm_url(internal: bool = True) -> str:
    """Return the base realm URL.

    Use the internal URL when this process is talking to Keycloak.
    Use the public URL when validating the `iss` claim (Keycloak sets
    iss to whatever the user-facing URL is).
    """
    base = settings.keycloak_internal_url if internal else settings.keycloak_public_url
    return f"{base}/realms/{settings.keycloak_realm}"


def _jwks_url() -> str:
    return f"{_realm_url(internal=True)}/protocol/openid-connect/certs"


async def _fetch_jwks() -> None:
    """Pull the JWKS from Keycloak and refresh the cache."""
    global _jwks_last_fetched
    url = _jwks_url()
    logger.info("Fetching JWKS from %s", url)
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        payload = resp.json()
    keys = payload.get("keys", [])
    _jwks_cache.clear()
    for key in keys:
        kid = key.get("kid")
        if kid:
            _jwks_cache[kid] = key
    _jwks_last_fetched = time.monotonic()
    logger.info("JWKS cache refreshed; %d keys loaded", len(_jwks_cache))


async def _get_key_for_kid(kid: str) -> dict[str, Any]:
    """Return the JWK matching `kid`, refetching once if needed."""
    if kid not in _jwks_cache:
        # Only refetch if we haven't very recently (anti-DoS)
        if time.monotonic() - _jwks_last_fetched > _JWKS_MIN_REFETCH_INTERVAL:
            await _fetch_jwks()
    if kid not in _jwks_cache:
        raise JWTError(f"Unknown JWT signing key id: {kid}")
    return _jwks_cache[kid]


# ─── Public API ──────────────────────────────────────────────────────
async def decode_token(token: str) -> TokenPayload:
    """Verify the JWT and return its parsed claims.

    Raises JWTError if:
      - the token is malformed
      - the signature is invalid
      - the token has expired
      - the issuer doesn't match our realm
    """
    # 1. Inspect header to find `kid`
    try:
        unverified_header = jwt.get_unverified_header(token)
    except JWTError as e:
        raise JWTError(f"Malformed JWT header: {e}") from e

    kid = unverified_header.get("kid")
    if not kid:
        raise JWTError("JWT header missing `kid` claim")

    # 2. Find the matching public key
    key = await _get_key_for_kid(kid)

    # 3. Verify signature + standard claims
    # `iss` is set by Keycloak based on whatever URL the user logged in via.
    # Accept BOTH internal and public realm URLs to avoid dev/prod mismatch.
    expected_issuers = {
        _realm_url(internal=True),
        _realm_url(internal=False),
    }

    try:
        claims = jwt.decode(
            token,
            key=key,
            algorithms=[key.get("alg", "RS256")],
            options={
                "verify_aud": False,   # we don't enforce audience for now
                "verify_at_hash": False,
            },
        )
    except JWTError as e:
        raise JWTError(f"JWT verification failed: {e}") from e

    iss = claims.get("iss")
    if iss not in expected_issuers:
        raise JWTError(f"Unexpected issuer: {iss!r}")

    return TokenPayload.model_validate(claims)


async def warm_jwks_cache() -> None:
    """Eager-load the JWKS at startup. Safe to call from lifespan."""
    try:
        await _fetch_jwks()
    except Exception as e:  # noqa: BLE001 — startup must tolerate KC not ready yet
        logger.warning(
            "Could not warm JWKS cache at startup (will retry lazily): %s", e,
        )
