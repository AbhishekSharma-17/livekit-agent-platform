"""Caller memory: recall at session start, remember after it, forget, purge, retention (V5-40).

Every read and write of the memory backend happens here, keyed only by the
caller's pseudonymous subject id (:mod:`lkap_api.memory.identity`); nothing in
this module logs a memory text, a transcript line or a phone number.

* **Recall** (``POST /internal/v1/memory/recall``, once per session, before the
  greeting): the session's own agent config decides (``memory.enabled``); the
  api resolves the caller, reads up to :data:`MAX_RECALL_MEMORIES` memories
  within :data:`RECALL_TIMEOUT_S` and records ``memory_recalled``. A failure
  never blocks the call: the session starts without memories.
* **Remember** (the ``memory_remember`` job the summary enqueues, never per
  turn): the finished transcript, masked by the deterministic privacy pass
  when ``privacy.storage_tier`` is not ``full``, goes to the backend, which asks
  the agent's language model for short facts (``memory.verbatim`` stores the
  caller's own lines instead, with no model call). Records ``memory_stored``.
* **Forget** one caller, **purge** a workspace, **expire** past
  ``retention_until``: the backend entries go, the ``memory_subjects`` rows go,
  and the memory texts copied into the sessions' ``memory_recalled`` /
  ``memory_stored`` events are blanked, with a ``memory_forgotten`` event on
  each of those sessions.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import Iterable, Sequence
from typing import Any, Final

from lkap_contracts import providers
from lkap_contracts.agent_config import AgentConfig
from lkap_contracts.api_models import (
    MAX_MEMORY_CHARS,
    MAX_RECALL_MEMORIES,
    MEMORY_FORGOTTEN_EVENT,
    MEMORY_RECALLED_EVENT,
    MEMORY_STORED_EVENT,
    MemoryForgetOut,
    MemoryForgetReason,
    MemoryForgottenEvent,
    MemoryPurgeOut,
    MemoryRecalledEvent,
    MemoryRecallIn,
    MemoryRecallOut,
    MemoryRecallStatus,
    MemoryStoredEvent,
    MemoryStoreStatus,
    SessionMemoryOut,
)
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api import net_guard
from lkap_api.costs import config_for_session
from lkap_api.db.guard import CROSS_WORKSPACE_OPTION
from lkap_api.db.models import Credential, MemoryEvent, MemorySubject, SessionEvent, utcnow
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.session import Database
from lkap_api.errors import ApiError, NotFoundError
from lkap_api.logging import get_logger
from lkap_api.memory.identity import (
    SUBJECT_ID_RE,
    caller_identity,
    delete_workspace_keys,
    scope_key,
    subject_id,
    workspace_key,
)
from lkap_api.memory.store import (
    ExtractionModel,
    MemoryItem,
    MemoryMessage,
    MemoryStore,
    MemoryUnavailableError,
    get_memory_store,
)
from lkap_api.privacy.redact import redact_text
from lkap_api.settings import Settings
from lkap_api.vault import Vault

__all__ = [
    "EXTRACTION_PROVIDERS",
    "RECALL_TIMEOUT_S",
    "REMEMBER_TIMEOUT_S",
    "MemoryBackendError",
    "forget_subject",
    "memory_texts",
    "purge_subjects",
    "purge_workspace",
    "recall_for_session",
    "remember_session",
    "resolve_extraction_model",
    "session_memory",
    "sweep_memory_retention",
]

log = get_logger(__name__)

#: How long the session-start recall may take before the session starts without memories.
RECALL_TIMEOUT_S: Final[float] = 3.0
#: How long one post-call write (the extraction model plus the backend) may take.
REMEMBER_TIMEOUT_S: Final[float] = 180.0
#: The most transcript turns one write sends (the latest ones), and each turn's longest text.
_MAX_TURNS: Final[int] = 200
_MAX_TURN_CHARS: Final[int] = 2000

#: Providers whose chat model the api can call after the call, at the registry's fixed base
#: URL (an admin-typed ``base_url`` is never used: the backend's calls must reach a known host).
EXTRACTION_PROVIDERS: Final[dict[str, str]] = {
    "openai-llm": "https://api.openai.com/v1",
    "openrouter-llm": providers.OPENROUTER_BASE_URL,
}
NO_MODEL_REASON: Final[str] = (
    "the agent has no language model the platform can call after the call (OpenAI or OpenRouter "
    "with a key). Turn on 'verbatim' or pick one of those"
)

_FINISHED: Final[frozenset[str]] = frozenset({"ended", "failed"})
_CONTENT_EVENTS: Final[tuple[str, ...]] = (MEMORY_RECALLED_EVENT, MEMORY_STORED_EVENT)


class MemoryBackendError(ApiError):
    """503: the memory backend is not installed or failed; nothing was deleted."""

    status_code = 503
    code = "memory_unavailable"


def parse_config(raw: object) -> AgentConfig | None:
    """An ``AgentConfig`` from a stored JSON document, or ``None`` when it does not validate."""
    try:
        return AgentConfig.model_validate(raw)
    except ValueError:
        return None


def _masked(config: AgentConfig) -> bool:
    return config.privacy.storage_tier != "full"


def memory_texts(items: Iterable[MemoryItem], *, masked: bool) -> list[str]:
    """The texts of ``items`` as recorded and handed out: clipped, masked when due, de-duplicated."""
    texts: list[str] = []
    for item in items:
        text = " ".join(item.text.split())
        if masked:
            text = redact_text(text)
        text = text[:MAX_MEMORY_CHARS]
        if text and text not in texts:
            texts.append(text)
        if len(texts) >= MAX_RECALL_MEMORIES:
            break
    return texts


async def _load_session(db: AsyncSession, session_id: str) -> SessionRow | None:
    return (
        await db.execute(
            select(SessionRow)
            .where(SessionRow.id == session_id)
            # Worker and job paths: the session's own workspace scopes everything after this.
            .execution_options(**{CROSS_WORKSPACE_OPTION: True})
        )
    ).scalar_one_or_none()


def _session_event(db: AsyncSession, session_id: str, kind: str, payload: dict[str, Any]) -> None:
    db.add(SessionEvent(session_id=session_id, ts=utcnow(), type=kind, payload=payload))


def _audit(
    db: AsyncSession,
    *,
    workspace_id: str,
    subject: str,
    kind: str,
    count: int,
    session_id: str | None = None,
    agent_id: str | None = None,
) -> None:
    db.add(
        MemoryEvent(
            workspace_id=workspace_id,
            subject_id=subject,
            session_id=session_id,
            agent_id=agent_id,
            kind=kind,
            count=count,
        )
    )


def _store_or_none(settings: Settings) -> tuple[MemoryStore | None, str | None]:
    try:
        return get_memory_store(settings), None
    except MemoryUnavailableError as exc:
        return None, str(exc)


# ---------------------------------------------------------------------------------- recall


def _recalled(
    db: AsyncSession, session: SessionRow, status: MemoryRecallStatus, texts: list[str] | None = None
) -> None:
    event = MemoryRecalledEvent(status=status, count=len(texts or []), memories=texts or [])
    _session_event(db, session.id, MEMORY_RECALLED_EVENT, event.model_dump(mode="json"))


async def recall_for_session(
    db: AsyncSession, vault: Vault, settings: Settings, payload: MemoryRecallIn
) -> MemoryRecallOut:
    """What the memory knows about the session's caller (the worker's session-start call).

    The backend is read before anything is written, so no write lock is held while
    it answers. Never raises for a backend problem: the session then starts without
    memories (``unavailable`` / ``failed``).

    Raises:
        NotFoundError: The session does not exist.
    """
    session = await _load_session(db, payload.session_id)
    if session is None:
        raise NotFoundError(f"unknown session '{payload.session_id}'")
    config = await config_for_session(db, session)
    if config is None or not config.memory.enabled:
        return MemoryRecallOut(status="disabled")
    identity = caller_identity(session, caller_e164=payload.caller_e164)
    if identity is None:
        _recalled(db, session, "no_identity")
        log.info("memory_recall", session_id=session.id, status="no_identity")
        return MemoryRecallOut(status="no_identity")
    store, unavailable = _store_or_none(settings)
    if store is None:
        _recalled(db, session, "unavailable")
        log.warning("memory_recall_unavailable", session_id=session.id, reason=unavailable)
        return MemoryRecallOut(status="unavailable")
    remember = (
        config.memory.verbatim
        or (await resolve_extraction_model(db, vault, settings, config, workspace_id=session.workspace_id))[0]
        is not None
    )
    status: MemoryRecallStatus = "empty"
    texts: list[str] = []
    key = await workspace_key(db, vault, session.workspace_id, create=False)
    if key is not None:
        try:
            items = await asyncio.wait_for(
                store.recall(
                    subject_id(key, identity),
                    scope_key(config.memory, session.agent_id),
                    query=None,
                    k=MAX_RECALL_MEMORIES,
                ),
                timeout=RECALL_TIMEOUT_S,
            )
        except Exception as exc:  # noqa: BLE001 - a slow or failing backend never blocks the call
            log.warning("memory_recall_failed", session_id=session.id, error_type=type(exc).__name__)
            status = "failed"
        else:
            texts = memory_texts(items, masked=_masked(config))
            status = "recalled" if texts else "empty"
    else:
        # The workspace's first remembered caller: nothing to read yet; the key is made now.
        key = await workspace_key(db, vault, session.workspace_id, create=True)
        if key is None:  # a key row exists but cannot be read with this master key
            _recalled(db, session, "failed")
            log.warning("memory_recall_failed", session_id=session.id, error_type="unreadable_key")
            return MemoryRecallOut(status="failed")
    _recalled(db, session, status, texts)
    _audit(
        db,
        workspace_id=session.workspace_id,
        subject=subject_id(key, identity),
        kind="recalled",
        count=len(texts),
        session_id=session.id,
        agent_id=session.agent_id,
    )
    await db.flush()
    log.info("memory_recall", session_id=session.id, status=status, count=len(texts), remember=remember)
    return MemoryRecallOut(status=status, memories=texts, remember=remember)


# --------------------------------------------------------------------------------- remember


async def resolve_extraction_model(
    db: AsyncSession, vault: Vault, settings: Settings, config: AgentConfig, *, workspace_id: str
) -> tuple[ExtractionModel | None, str | None]:
    """The agent's language model the api can call after the call, or why there is none.

    Tries ``pipeline.llm`` then ``pipeline.workflow_llm``; only OpenAI and OpenRouter
    with a key of the workspace qualify, always at the registry's base URL.
    """
    for ref in (config.pipeline.llm, config.pipeline.workflow_llm):
        if ref is None or ref.provider_id not in EXTRACTION_PROVIDERS or not ref.credential_id:
            continue
        credential = await db.scalar(
            select(Credential)
            .where(Credential.id == ref.credential_id, Credential.workspace_id == workspace_id)
            .execution_options(**{CROSS_WORKSPACE_OPTION: True})
        )
        if credential is None:
            continue
        bag = vault.decrypt(credential.ciphertext)
        api_key = str(bag.get("api_key") or next(iter(bag.values()), ""))
        model = ref.model or providers.get(ref.provider_id).default_model
        base_url = EXTRACTION_PROVIDERS[ref.provider_id]
        blocked = net_guard.check_url(base_url, net_guard.policy_from_settings(settings))
        if not api_key or not model or blocked is not None:
            continue
        return ExtractionModel(
            provider_id=ref.provider_id, model=model, base_url=base_url, api_key=api_key
        ), None
    return None, NO_MODEL_REASON


def transcript_messages(transcript: object, *, masked: bool, callers_only: bool) -> list[MemoryMessage]:
    """The session's transcript as backend messages (the latest turns, masked when due)."""
    messages: list[MemoryMessage] = []
    turns = transcript if isinstance(transcript, list) else []
    for turn in turns[-_MAX_TURNS:]:
        if not isinstance(turn, dict):
            continue
        role = turn.get("role")
        text = turn.get("text")
        if role not in ("user", "assistant") or not isinstance(text, str) or not text.strip():
            continue
        if callers_only and role != "user":
            continue
        content = " ".join(text.split())[:_MAX_TURN_CHARS]
        messages.append(MemoryMessage(role=role, content=redact_text(content) if masked else content))
    return messages


async def _already_stored(db: AsyncSession, session_id: str) -> bool:
    found = await db.scalar(
        select(SessionEvent.id)
        .where(SessionEvent.session_id == session_id, SessionEvent.type == MEMORY_STORED_EVENT)
        .limit(1)
    )
    return found is not None


async def _touch_subject(
    db: AsyncSession, *, workspace_id: str, subject: str, scope: str, agent_id: str, retention_days: int
) -> None:
    now = utcnow()
    row = await db.scalar(
        select(MemorySubject).where(
            MemorySubject.workspace_id == workspace_id,
            MemorySubject.subject_id == subject,
            MemorySubject.scope_key == scope,
        )
    )
    until = now + dt.timedelta(days=retention_days)
    if row is None:
        db.add(
            MemorySubject(
                workspace_id=workspace_id,
                subject_id=subject,
                scope_key=scope,
                agent_id=agent_id,
                created_at=now,
                last_seen_at=now,
                retention_until=until,
            )
        )
    else:
        row.last_seen_at = now
        row.agent_id = agent_id
        row.retention_until = until
    await db.flush()


def _stored(
    db: AsyncSession,
    session_id: str,
    status: MemoryStoreStatus,
    *,
    texts: list[str] | None = None,
    reason: str | None = None,
) -> MemoryStoredEvent:
    event = MemoryStoredEvent(status=status, count=len(texts or []), memories=texts or [], reason=reason)
    _session_event(db, session_id, MEMORY_STORED_EVENT, event.model_dump(mode="json"))
    return event


async def remember_session(
    database: Database, vault: Vault, settings: Settings, session_id: str
) -> MemoryStoredEvent | None:
    """Write what a finished session taught the memory (the ``memory_remember`` job).

    Runs once per session (a recorded ``memory_stored`` event ends it). The subject's
    row is written (and committed) before the backend is called, so the retention
    sweep and a purge always know about what the backend holds.

    Returns:
        The recorded event, or ``None`` when memory is off or the session is gone.
    """
    async with database.session() as db:
        session = await _load_session(db, session_id)
        if session is None or session.status not in _FINISHED:
            return None
        config = await config_for_session(db, session)
        if config is None or not config.memory.enabled or await _already_stored(db, session_id):
            return None
        memory = config.memory
        identity = caller_identity(session)
        if identity is None:
            return _stored(db, session_id, "skipped", reason="no caller identity")
        messages = transcript_messages(
            session.transcript, masked=_masked(config), callers_only=memory.verbatim
        )
        if not messages:
            return _stored(db, session_id, "skipped", reason="nothing was said")
        model: ExtractionModel | None = None
        if not memory.verbatim:
            model, no_model = await resolve_extraction_model(
                db, vault, settings, config, workspace_id=session.workspace_id
            )
            if model is None:
                return _stored(db, session_id, "skipped", reason=no_model)
        store, unavailable = _store_or_none(settings)
        if store is None:
            return _stored(db, session_id, "skipped", reason=unavailable)
        key = await workspace_key(db, vault, session.workspace_id, create=True)
        if key is None:
            return _stored(db, session_id, "failed", reason="the workspace's memory key could not be read")
        subject = subject_id(key, identity)
        scope = scope_key(memory, session.agent_id)
        await _touch_subject(
            db,
            workspace_id=session.workspace_id,
            subject=subject,
            scope=scope,
            agent_id=session.agent_id,
            retention_days=memory.retention_days,
        )
        workspace_id, agent_id, masked = session.workspace_id, session.agent_id, _masked(config)

    status: MemoryStoreStatus
    reason: str | None = None
    texts: list[str] = []
    try:
        items = await asyncio.wait_for(
            store.remember(subject, scope, messages, model=model), timeout=REMEMBER_TIMEOUT_S
        )
    except Exception as exc:  # noqa: BLE001 - recorded on the session; the job does not retry a model call
        status, reason = "failed", f"the memory could not be written ({type(exc).__name__})"
        log.warning("memory_remember_failed", session_id=session_id, error_type=type(exc).__name__)
    else:
        texts = memory_texts(items, masked=masked)
        status = "stored" if texts else "nothing_new"

    async with database.session() as db:
        event = _stored(db, session_id, status, texts=texts, reason=reason)
        _audit(
            db,
            workspace_id=workspace_id,
            subject=subject,
            kind="stored",
            count=len(texts),
            session_id=session_id,
            agent_id=agent_id,
        )
    log.info("memory_remember", session_id=session_id, status=status, count=len(texts))
    return event


# ----------------------------------------------------------------------------- forgetting


async def _linked_sessions(
    db: AsyncSession, workspace_id: str, subjects: Sequence[str], *, agent_id: str | None = None
) -> list[str]:
    if not subjects:
        return []
    stmt = select(MemoryEvent.session_id).where(
        MemoryEvent.workspace_id == workspace_id,
        MemoryEvent.subject_id.in_(list(subjects)),
        MemoryEvent.session_id.is_not(None),
    )
    if agent_id is not None:
        stmt = stmt.where(MemoryEvent.agent_id == agent_id)
    rows = (await db.execute(stmt.distinct())).scalars().all()
    return sorted({row for row in rows if row})


async def _blank_sessions(
    db: AsyncSession, workspace_id: str, session_ids: Sequence[str], reason: MemoryForgetReason
) -> int:
    """Blank the memory texts recorded on ``session_ids`` and note the forget on each."""
    if not session_ids:
        return 0
    owned = set(
        (
            await db.execute(
                select(SessionRow.id).where(
                    SessionRow.workspace_id == workspace_id, SessionRow.id.in_(list(session_ids))
                )
            )
        )
        .scalars()
        .all()
    )
    events = (
        (
            await db.execute(
                select(SessionEvent).where(
                    SessionEvent.session_id.in_(sorted(owned)), SessionEvent.type.in_(_CONTENT_EVENTS)
                )
            )
        )
        .scalars()
        .all()
    )
    for event in events:
        payload = dict(event.payload) if isinstance(event.payload, dict) else {}
        payload.update({"memories": [], "forgotten": True})
        event.payload = payload
    forgotten = MemoryForgottenEvent(reason=reason).model_dump(mode="json")
    for session_id in sorted(owned):
        _session_event(db, session_id, MEMORY_FORGOTTEN_EVENT, forgotten)
    return len(owned)


async def forget_subject(
    db: AsyncSession, settings: Settings, workspace_id: str, subject: str
) -> MemoryForgetOut:
    """Forget one caller of the workspace everywhere (``DELETE /v1/memory/subjects/{id}``).

    Raises:
        NotFoundError: The workspace has never seen this subject id.
        MemoryBackendError: The backend is unavailable or failed (nothing was deleted).
    """
    if not SUBJECT_ID_RE.match(subject):
        raise NotFoundError("unknown memory subject")
    rows = (
        (
            await db.execute(
                select(MemorySubject).where(
                    MemorySubject.workspace_id == workspace_id, MemorySubject.subject_id == subject
                )
            )
        )
        .scalars()
        .all()
    )
    sessions = await _linked_sessions(db, workspace_id, [subject])
    if not rows and not sessions:
        raise NotFoundError("unknown memory subject")
    count = 0
    if rows:
        store, unavailable = _store_or_none(settings)
        if store is None:
            raise MemoryBackendError(f"memory is unavailable ({unavailable}). Nothing was deleted")
        try:
            count = await store.forget(subject)
        except Exception as exc:
            log.warning("memory_forget_failed", error_type=type(exc).__name__)
            raise MemoryBackendError("the memory backend failed. Nothing was deleted") from exc
        await db.execute(
            delete(MemorySubject).where(
                MemorySubject.workspace_id == workspace_id, MemorySubject.subject_id == subject
            )
        )
    updated = await _blank_sessions(db, workspace_id, sessions, "caller")
    _audit(db, workspace_id=workspace_id, subject=subject, kind="forgotten", count=count)
    await db.flush()
    log.info("memory_subject_forgotten", workspace_id=workspace_id, deleted=count, sessions=updated)
    return MemoryForgetOut(subject_id=subject, forgotten=True, sessions_updated=updated)


async def purge_workspace(db: AsyncSession, workspace_id: str) -> tuple[MemoryPurgeOut, list[str]]:
    """Make every memory of the workspace unreachable at once (``POST /v1/memory/purge``).

    Deletes the memory key (no subject id can be computed again), the subject rows
    and the texts copied into sessions. The backend entries are deleted by the
    ``memory_purge`` job the caller enqueues after committing, with the returned ids.

    Returns:
        The response (without ``job_id``) and the subject ids to purge in the backend.
    """
    subjects = sorted(
        set(
            (
                await db.execute(
                    select(MemorySubject.subject_id).where(MemorySubject.workspace_id == workspace_id)
                )
            )
            .scalars()
            .all()
        )
    )
    linked = sorted(
        set(
            (
                await db.execute(
                    select(MemoryEvent.subject_id).where(
                        MemoryEvent.workspace_id == workspace_id, MemoryEvent.kind.in_(("recalled", "stored"))
                    )
                )
            )
            .scalars()
            .all()
        )
    )
    keys = await delete_workspace_keys(db, workspace_id)
    if not subjects and not linked and not keys:
        return MemoryPurgeOut(status="nothing_to_purge", subjects=0), []
    await db.execute(delete(MemorySubject).where(MemorySubject.workspace_id == workspace_id))
    sessions = await _linked_sessions(db, workspace_id, sorted({*subjects, *linked}))
    await _blank_sessions(db, workspace_id, sessions, "workspace")
    for subject in subjects:
        _audit(db, workspace_id=workspace_id, subject=subject, kind="purged", count=0)
    await db.flush()
    log.info("memory_workspace_purged", workspace_id=workspace_id, subjects=len(subjects), keys=keys)
    return MemoryPurgeOut(status="queued", subjects=len(subjects)), subjects


async def purge_subjects(settings: Settings, subjects: Sequence[str]) -> int:
    """Delete every backend entry of ``subjects`` (the ``memory_purge`` job).

    Raises:
        MemoryUnavailableError: The backend is not installed (the job retries).
    """
    if not subjects:
        return 0
    return await get_memory_store(settings).purge(list(subjects))


async def sweep_memory_retention(
    database: Database, settings: Settings, *, now: dt.datetime | None = None
) -> int:
    """Forget every subject row whose ``retention_until`` has passed (the sessions sweep).

    A backend failure leaves that row for the next pass; without the backend
    installed nothing is swept (nothing could have been stored).

    Returns:
        How many subject rows were forgotten.
    """
    ts = now or utcnow()
    async with database.session() as db:
        rows = (
            (
                await db.execute(
                    select(MemorySubject)
                    .where(MemorySubject.retention_until <= ts)
                    .execution_options(**{CROSS_WORKSPACE_OPTION: True})
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return 0
        store, unavailable = _store_or_none(settings)
        if store is None:
            log.warning("memory_retention_skipped", reason=unavailable, due=len(rows))
            return 0
        forgotten = 0
        for row in rows:
            try:
                count = await store.forget(row.subject_id, row.scope_key)
            except Exception as exc:  # noqa: BLE001 - kept for the next pass
                log.warning("memory_retention_forget_failed", error_type=type(exc).__name__)
                continue
            agent = None if row.scope_key == "workspace" else row.scope_key
            sessions = await _linked_sessions(db, row.workspace_id, [row.subject_id], agent_id=agent)
            await _blank_sessions(db, row.workspace_id, sessions, "retention")
            _audit(db, workspace_id=row.workspace_id, subject=row.subject_id, kind="expired", count=count)
            await db.delete(row)
            forgotten += 1
    if forgotten:
        log.info("memory_retention_swept", subjects=forgotten)
    return forgotten


# ------------------------------------------------------------------------------ console


async def session_memory(db: AsyncSession, session: SessionRow) -> SessionMemoryOut:
    """What a session recalled and stored, for the console's Memory tab."""
    config = await config_for_session(db, session)
    enabled = bool(config is not None and config.memory.enabled)
    subject = await db.scalar(
        select(MemoryEvent.subject_id)
        .where(
            MemoryEvent.workspace_id == session.workspace_id,
            MemoryEvent.session_id == session.id,
            MemoryEvent.kind.in_(("recalled", "stored")),
        )
        .order_by(MemoryEvent.created_at.desc())
        .limit(1)
    )
    events = (
        (
            await db.execute(
                select(SessionEvent)
                .where(
                    SessionEvent.session_id == session.id,
                    SessionEvent.type.in_((*_CONTENT_EVENTS, MEMORY_FORGOTTEN_EVENT)),
                )
                .order_by(SessionEvent.id)
            )
        )
        .scalars()
        .all()
    )
    out = SessionMemoryOut(enabled=enabled, subject_id=subject)
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type == MEMORY_RECALLED_EVENT:
            recalled = MemoryRecalledEvent.model_validate(payload)
            out.recall_status, out.recalled = recalled.status, list(recalled.memories)
        elif event.type == MEMORY_STORED_EVENT:
            stored = MemoryStoredEvent.model_validate(payload)
            out.store_status, out.store_reason = stored.status, stored.reason
            out.stored = list(stored.memories)
        else:
            out.forgotten_at = event.ts
    return out
