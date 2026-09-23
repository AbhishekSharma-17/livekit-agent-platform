"""Fixed ids and column defaults shared by the models, the migrations and bootstrap.

These values are deliberately literal constants rather than settings: the
Alembic revisions of CONTRACTS-V2 §2 must be able to insert the default
workspace without importing :mod:`lkap_api.settings` (migrations run with no
LiveKit credentials and must never read ``.env``).
"""

from __future__ import annotations

#: Primary key of the workspace every v1 row is migrated into (CONTRACTS-V2 §2).
DEFAULT_WORKSPACE_ID = "00000000000000000000000000000001"

#: Slug of that workspace; ``bootstrap`` looks the row up by slug, not by id.
DEFAULT_WORKSPACE_SLUG = "default"

#: Display name of the bootstrapped workspace.
DEFAULT_WORKSPACE_NAME = "Default"

#: Slug of the connection the v1 environment variables are migrated into.
DEFAULT_CONNECTION_SLUG = "default"

#: Display name of that connection.
DEFAULT_CONNECTION_NAME = "Default"

#: Email of the bootstrapped owner when ``LKAP_BOOTSTRAP_OWNER_EMAIL`` is unset.
DEFAULT_OWNER_EMAIL = "owner@local"

#: Agent name a connection dispatches to when nothing else is configured.
DEFAULT_AGENT_NAME = "lkap-agent"
