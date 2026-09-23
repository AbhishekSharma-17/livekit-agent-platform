"""Request/response models of the auth, team and API-key routes.

CONTRACTS-V2 §3.4 names these endpoints but ``lkap_contracts.api_models`` only
defines ``Me``, ``UserOut`` and ``WorkspaceMembership`` for them. The rest live
here until they are moved into the contracts package (asked in
``docs/v2/_asks.md``); their field names are what V2-14 builds against.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from lkap_api.auth.roles import Role, Scope

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+$")


def normalise_email(value: str) -> str:
    """Lower-case and trim an email; reject anything without a single ``@``."""
    email = value.strip().lower()
    if not _EMAIL.match(email) or len(email) > 320:
        raise ValueError("not a valid email address")
    return email


class LoginIn(BaseModel):
    """``POST /v1/auth/login``."""

    email: str
    password: str = Field(min_length=1, max_length=1024)

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        return normalise_email(value)


class PasswordChangeIn(BaseModel):
    """``POST /v1/auth/password``."""

    current: str = Field(min_length=1, max_length=1024)
    new: str = Field(min_length=1, max_length=1024)


class AcceptInviteIn(BaseModel):
    """``POST /v1/auth/accept-invite``.

    ``password`` sets the password of a new account, or proves ownership of an
    existing one (which is then added to the workspace).
    """

    token: str = Field(min_length=1, max_length=4096)
    password: str = Field(min_length=1, max_length=1024)
    name: str | None = Field(default=None, max_length=200)


class InviteCreate(BaseModel):
    """``POST /v1/workspaces/{id}/invites``."""

    email: str
    role: Role = "viewer"
    name: str = Field(default="", max_length=200)

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        return normalise_email(value)


class InviteOut(BaseModel):
    """A one-time invite link (shown once to the inviting admin)."""

    email: str
    role: Role
    workspace_id: str
    token: str
    url: str
    expires_at: datetime


class WorkspaceOut(BaseModel):
    """A workspace and the caller's role in it."""

    id: str
    slug: str
    name: str
    settings: dict[str, Any] = {}
    role: Role
    created_at: datetime
    updated_at: datetime


class WorkspaceUpdate(BaseModel):
    """``PUT /v1/workspaces/{id}`` — omitted fields are unchanged; ``settings`` merges."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    settings: dict[str, Any] | None = None


class MemberOut(BaseModel):
    """One member of a workspace."""

    user_id: str
    email: str
    name: str
    role: Role
    created_at: datetime
    disabled: bool = False
    pending: bool = Field(default=False, description="Invited but has never set a password")


class MemberCreate(BaseModel):
    """``POST /v1/workspaces/{id}/members`` — add an existing user directly."""

    email: str
    role: Role = "viewer"

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        return normalise_email(value)


class MemberUpdate(BaseModel):
    """``PUT /v1/workspaces/{id}/members/{user_id}``."""

    role: Role


class ApiKeyCreate(BaseModel):
    """``POST /v1/api-keys``."""

    name: str = Field(min_length=1, max_length=200)
    scopes: list[Scope] = Field(min_length=1)
    expires_at: datetime | None = None


class ApiKeyOut(BaseModel):
    """An API key without its secret."""

    id: str
    workspace_id: str
    name: str
    prefix: str
    scopes: list[str]
    created_by: str | None = None
    created_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
    expires_at: datetime | None = None


class ApiKeyCreated(ApiKeyOut):
    """The response of ``POST /v1/api-keys``: the only time ``key`` is ever returned."""

    key: str


class AuditOut(BaseModel):
    """One ``audit_log`` row."""

    id: int
    workspace_id: str | None
    actor_type: Literal["user", "api_key", "system"]
    actor_id: str | None
    action: str
    target_type: str
    target_id: str | None
    payload: dict[str, Any] = {}
    ts: datetime
