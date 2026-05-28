"""
OAuth 2.0 Authorization Code + PKCE helpers for Keycloak login.

Why PKCE on a confidential client (we have a client_secret):
  - Defense in depth. PKCE binds the auth-code redemption to the
    original browser session via a verifier the attacker doesn't have.
  - Recommended by OAuth 2.1 (draft) and the FAPI guidelines.
  - It's ~10 lines, so the cost is trivial.

Flow primitives this module exposes:
  - new_state()                  random opaque token for CSRF protection
  - new_pkce_pair()              (code_verifier, code_challenge) pair
  - build_login_url(...)         the URL we redirect the browser to
  - build_logout_url(...)        Keycloak end_session_endpoint URL
  - exchange_code_for_token(...) server-to-server POST to /token endpoint

Two Keycloak URLs in play:
  - keycloak_public_url    used in URLs we send TO THE BROWSER
                           (browser must be able to reach this)
  - keycloak_internal_url  used for server-side HTTP calls FROM webapp
                           (Docker internal network)
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from dataclasses import dataclass
from typing import Final

import httpx

from app.config import settings

logger = logging.getLogger("sparki.web.oauth")


# ─── Constants ───────────────────────────────────────────────────────
# OAuth scopes we request. "openid" is mandatory for OIDC; "profile" +
# "email" give us the display_name/email claims we surface in the UI.
OAUTH_SCOPES: Final[str] = "openid profile email"

# How long the "OAuth flight" state cookie lives. The user must complete
# Keycloak login in this window. 10 minutes is generous but not abusable.
OAUTH_FLIGHT_TTL_SECONDS: Final[int] = 600


# ─── Random tokens ───────────────────────────────────────────────────
def new_state() -> str:
    """Generate a CSRF-resistant ``state`` parameter (32 bytes → 43 chars b64url).

    We compare this byte-for-byte on callback; any mismatch = reject.
    """
    return secrets.token_urlsafe(32)


@dataclass(frozen=True)
class PkcePair:
    """A PKCE verifier+challenge pair.

    The verifier is the secret kept by the webapp (stashed in the
    flight cookie). The challenge — its SHA-256 hash — is sent to
    Keycloak in the initial auth URL.
    """
    verifier: str          # the secret we hold
    challenge: str         # the public hash sent to Keycloak


def new_pkce_pair() -> PkcePair:
    """Generate a PKCE (verifier, challenge) pair per RFC 7636 §4.

    - verifier:  high-entropy random string, 43-128 chars (we use 64)
    - challenge: BASE64URL(SHA256(verifier)), method = S256
    """
    verifier = secrets.token_urlsafe(64)            # 64 bytes → 86 chars
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return PkcePair(verifier=verifier, challenge=challenge)


# ─── URL builders ────────────────────────────────────────────────────
def _realm_public_url() -> str:
    """Realm URL the browser uses (must be browser-reachable)."""
    return (
        f"{settings.keycloak_public_url.rstrip('/')}"
        f"/realms/{settings.keycloak_realm}"
    )


def _realm_internal_url() -> str:
    """Realm URL the webapp uses for server-side HTTP."""
    return (
        f"{settings.keycloak_internal_url.rstrip('/')}"
        f"/realms/{settings.keycloak_realm}"
    )


def build_login_url(
    *,
    state: str,
    code_challenge: str,
    redirect_uri: str,
) -> str:
    """Build the URL we 302-redirect the browser to so it can log in.

    Always uses the PUBLIC Keycloak URL because the browser is the
    one fetching it.
    """
    params = httpx.QueryParams(
        {
            "client_id": settings.keycloak_client_id,
            "response_type": "code",
            "scope": OAUTH_SCOPES,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{_realm_public_url()}/protocol/openid-connect/auth?{params}"


def build_logout_url(*, post_logout_redirect_uri: str) -> str:
    """Build Keycloak's end_session_endpoint URL for full SSO logout.

    Per OIDC spec, Keycloak 19+ requires:
      - id_token_hint (preferred), OR
      - client_id + post_logout_redirect_uri (what we use)

    The post_logout_redirect_uri must match one of the client's
    configured redirectUris in Keycloak. Our realm allows
    http://localhost:8000/* which covers the root.
    """
    params = httpx.QueryParams(
        {
            "client_id": settings.keycloak_client_id,
            "post_logout_redirect_uri": post_logout_redirect_uri,
        }
    )
    return (
        f"{_realm_public_url()}/protocol/openid-connect/logout?{params}"
    )


# ─── Token exchange ──────────────────────────────────────────────────
@dataclass(frozen=True)
class TokenResponse:
    """Subset of the Keycloak /token response we use."""
    access_token: str
    refresh_token: str | None
    id_token: str | None
    expires_in: int                # seconds until access_token expires


class TokenExchangeError(RuntimeError):
    """Raised when Keycloak refuses the auth-code redemption.

    Most common causes:
      - state mismatch (caught earlier, before we get here)
      - verifier mismatch (PKCE)
      - code expired (default 1 min)
      - code already redeemed (Keycloak makes them single-use)
      - redirect_uri doesn't match the one used in the auth request
    """


async def exchange_code_for_token(
    *,
    code: str,
    code_verifier: str,
    redirect_uri: str,
) -> TokenResponse:
    """Server-to-server POST to Keycloak's /token endpoint.

    Uses the INTERNAL Keycloak URL because this call originates from
    the webapp container, not the browser.

    Returns the parsed TokenResponse on success; raises
    TokenExchangeError with Keycloak's error_description on failure.
    """
    url = f"{_realm_internal_url()}/protocol/openid-connect/token"
    data = {
        "grant_type": "authorization_code",
        "client_id": settings.keycloak_client_id,
        "client_secret": settings.keycloak_client_secret.get_secret_value(),
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(url, data=data)

    if resp.status_code != 200:
        # Keycloak puts useful detail in the JSON body.
        try:
            body = resp.json()
            detail = body.get("error_description") or body.get("error") or resp.text
        except Exception:
            detail = resp.text
        logger.warning(
            "Keycloak token exchange failed: %s %s",
            resp.status_code, detail,
        )
        raise TokenExchangeError(f"Keycloak rejected the code: {detail}")

    payload = resp.json()
    return TokenResponse(
        access_token=payload["access_token"],
        refresh_token=payload.get("refresh_token"),
        id_token=payload.get("id_token"),
        expires_in=int(payload.get("expires_in", 0)),
    )
