"""
Pydantic schemas for the auth layer.

  - TokenPayload: the raw claims we expect inside a Keycloak JWT
  - CurrentUser:  the in-app representation used by routes and services
"""

from __future__ import annotations

import uuid
from typing import Annotated

from email_validator import EmailNotValidError, validate_email
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from app.users.models import UserRole


def _validate_email_permissive(v: str | None) -> str | None:
    """Validate an email but allow reserved TLDs (.test, .example, .localhost, .invalid).

    These are RFC 2606 reserved TLDs intended for testing and documentation.
    pydantic's default EmailStr (via email-validator) rejects them, which
    breaks our demo fixtures like 'tenant@sigenburg.test'.
    """
    if v is None:
        return None
    try:
        result = validate_email(v, check_deliverability=False, test_environment=True)
    except EmailNotValidError as exc:
        raise ValueError(f"Invalid email: {exc}") from exc
    return result.normalized


# A drop-in replacement for EmailStr that tolerates .test / .example / .localhost
PermissiveEmail = Annotated[str, BeforeValidator(_validate_email_permissive)]


class TokenPayload(BaseModel):
    """Subset of JWT claims we care about.

    Keycloak puts dozens of claims in a token — we only validate and
    extract what the app actually needs.
    """

    model_config = ConfigDict(extra="ignore")

    sub: uuid.UUID = Field(..., description="Keycloak user id")
    email: PermissiveEmail | None = Field(None)
    preferred_username: str | None = Field(None)
    name: str | None = Field(None, description="Display name")
    exp: int = Field(..., description="Expiry, unix timestamp")
    iat: int = Field(..., description="Issued-at, unix timestamp")
    iss: str = Field(..., description="Issuer URL")
    azp: str | None = Field(None, description="Authorized party (client)")
    # Keycloak's realm roles live under realm_access.roles
    realm_access: dict[str, list[str]] = Field(default_factory=dict)

    @property
    def roles(self) -> list[str]:
        """Convenience: the list of realm-role names."""
        return self.realm_access.get("roles", [])


class CurrentUser(BaseModel):
    """The authenticated user as the rest of the app sees them.

    Built from (TokenPayload + DB row). Routes depend on this, not on the
    raw JWT — that keeps Keycloak-specific details out of business logic.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str
    role: UserRole
    organization_id: uuid.UUID

    @property
    def is_sparki_staff(self) -> bool:
        return self.role == UserRole.SPARKI_STAFF

    @property
    def is_site_owner(self) -> bool:
        return self.role == UserRole.SITE_OWNER

    @property
    def is_tenant(self) -> bool:
        return self.role == UserRole.TENANT
