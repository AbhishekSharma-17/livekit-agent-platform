"""Who the caller is, as the memory sees them (V5-40, D-V5-17, research-v4 K §6.1).

The memory never sees a phone number or an identity. It sees a **subject id**:
``hex(HMAC-SHA256(workspace memory key, identity))``, computed here and only
here. The key is 32 random bytes per workspace, kept in the vault as a
``credentials`` row with ``provider_id = "memory-key"`` and created the first
time a workspace needs it. A keyed hash (not a bare hash) is the ICO's
pseudonymisation technique: without the key the id cannot be tied back to a
number, and deleting or replacing the key orphans every memory of the
workspace at once (a purge does exactly that).

The identity of a session:

* a phone call (``sip_in`` / ``sip_out``): the other party's E.164 number,
  from ``sessions.caller`` (``from`` inbound, ``to`` outbound), else the
  number the worker passed (``MemoryRecallIn.caller_e164``);
* any other channel: ``sessions.participant_identity`` when a trusted caller
  chose it (an embedding site's backend, the console) — never the random
  ``user-xxxxxxxx`` the platform mints for an anonymous visitor, nor the
  ``<channel>-caller`` placeholder of a worker-created session, which would
  each make a memory nobody could ever recall.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from typing import Any, Final

from lkap_contracts.agent_config import MemoryConfig
from lkap_contracts.telephony import E164_PATTERN
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.db.guard import CROSS_WORKSPACE_OPTION
from lkap_api.db.models import Credential
from lkap_api.db.models import Session as SessionRow
from lkap_api.logging import get_logger
from lkap_api.vault import Vault

__all__ = [
    "GENERATED_IDENTITY_RE",
    "MEMORY_KEY_LABEL",
    "MEMORY_KEY_PROVIDER_ID",
    "PHONE_CHANNELS",
    "SUBJECT_ID_RE",
    "WORKSPACE_SCOPE",
    "caller_identity",
    "delete_workspace_keys",
    "scope_key",
    "subject_id",
    "workspace_key",
]

log = get_logger(__name__)

#: ``credentials.provider_id`` of a workspace's memory key (not a registry provider).
MEMORY_KEY_PROVIDER_ID: Final[str] = "memory-key"
MEMORY_KEY_LABEL: Final[str] = "Caller memory key (deleting it forgets every caller)"
_KEY_FIELD: Final[str] = "key"
_KEY_BYTES: Final[int] = 32

#: A subject id: 64 lowercase hex characters.
SUBJECT_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")

#: What `livekit_tokens.new_participant_identity` mints for an anonymous caller.
GENERATED_IDENTITY_RE: Final[re.Pattern[str]] = re.compile(r"^user-[0-9a-f]{8}$")
#: The placeholder `POST /internal/v1/sessions/start` stores (``f"{channel}-caller"``).
_PLACEHOLDER_IDENTITY_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z_]+-caller$")
_E164_RE: Final[re.Pattern[str]] = re.compile(E164_PATTERN)

#: Channels whose caller is a phone number.
PHONE_CHANNELS: Final[frozenset[str]] = frozenset({"sip_in", "sip_out"})

#: ``memory_subjects.scope_key`` (and the backend's agent id) of a workspace-scoped memory.
WORKSPACE_SCOPE: Final[str] = "workspace"

#: Longest identity hashed (a LiveKit identity is far shorter; this only bounds input).
_MAX_IDENTITY_CHARS: Final[int] = 256


def subject_id(key: bytes, identity: str) -> str:
    """The caller's pseudonymous id: ``hex(HMAC-SHA256(key, identity))``."""
    return hmac.new(key, identity.encode("utf-8"), hashlib.sha256).hexdigest()


def scope_key(memory: MemoryConfig, agent_id: str) -> str:
    """Whose memories a session reads and writes: the agent's own, or the workspace's."""
    return WORKSPACE_SCOPE if memory.scope == "workspace" else agent_id


def _phone(value: object) -> str | None:
    if isinstance(value, str) and _E164_RE.match(value.strip()):
        return value.strip()
    return None


def caller_identity(session: SessionRow, *, caller_e164: str | None = None) -> str | None:
    """The stable identity of the session's caller, or ``None`` when there is none.

    Args:
        session: The session row.
        caller_e164: The number the worker saw on the phone leg, used only for a
            phone session whose row does not have it yet.

    Returns:
        An E.164 number, a trusted participant identity, or ``None``.
    """
    if session.channel in PHONE_CHANNELS:
        caller: dict[str, Any] = session.caller if isinstance(session.caller, dict) else {}
        outbound = caller.get("direction") == "outbound" or session.channel == "sip_out"
        return _phone(caller.get("to" if outbound else "from")) or _phone(caller_e164)
    identity = (session.participant_identity or "").strip()
    if (
        not identity
        or len(identity) > _MAX_IDENTITY_CHARS
        or GENERATED_IDENTITY_RE.match(identity)
        or _PLACEHOLDER_IDENTITY_RE.match(identity)
    ):
        return None
    return identity


def _key_fingerprint(key: bytes) -> str:
    return "…" + hashlib.sha256(key).hexdigest()[-4:]


async def _key_rows(db: AsyncSession, workspace_id: str) -> list[Credential]:
    rows = (
        await db.execute(
            select(Credential)
            .where(Credential.workspace_id == workspace_id, Credential.provider_id == MEMORY_KEY_PROVIDER_ID)
            .order_by(Credential.created_at, Credential.id)
            # Workers' internal routes and jobs run without a request workspace (the row is scoped above).
            .execution_options(**{CROSS_WORKSPACE_OPTION: True})
        )
    ).scalars()
    return list(rows)


def _decode(vault: Vault, row: Credential) -> bytes | None:
    try:
        return bytes.fromhex(vault.decrypt(row.ciphertext)[_KEY_FIELD])
    except (KeyError, ValueError):
        log.warning("memory_key_unreadable", credential_id=row.id)
        return None


async def workspace_key(db: AsyncSession, vault: Vault, workspace_id: str, *, create: bool) -> bytes | None:
    """The workspace's memory key; created (and flushed) on first use when ``create``.

    The oldest row wins, so two sessions creating a key at the same moment agree
    once both have committed; the loser's row is removed on its next read.

    Returns:
        The 32-byte key, or ``None`` when there is none and ``create`` is false.
    """
    rows = await _key_rows(db, workspace_id)
    for extra in rows[1:]:
        await db.delete(extra)
    if rows:
        return _decode(vault, rows[0])
    if not create:
        return None
    key = secrets.token_bytes(_KEY_BYTES)
    row = Credential(
        workspace_id=workspace_id,
        provider_id=MEMORY_KEY_PROVIDER_ID,
        label=MEMORY_KEY_LABEL,
        ciphertext=vault.encrypt({_KEY_FIELD: key.hex()}),
        fingerprint=_key_fingerprint(key),
    )
    db.add(row)
    await db.flush()
    log.info("memory_key_created", workspace_id=workspace_id, credential_id=row.id)
    return key


async def delete_workspace_keys(db: AsyncSession, workspace_id: str) -> int:
    """Delete the workspace's memory key(s): every existing subject id is orphaned for good."""
    rows = await _key_rows(db, workspace_id)
    for row in rows:
        await db.delete(row)
    return len(rows)
