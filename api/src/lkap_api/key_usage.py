"""When a stored vendor key was last used (V6-32, ``credentials.last_used_at``).

Every place the api decrypts a key to *use* it (a session's resolve, a tool call or its dry run,
a catalog read, a knowledge-base embed, a model test, the QA judge) calls :func:`mark_used`
with the ids it decrypted. One ``UPDATE`` per call, throttled to once a minute per key so a busy
key never turns every request into a write; ``updated_at`` (the "edited" time) is left alone.
Testing a key from the key list is not a use: that is ``last_test_at``.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable

from sqlalchemy import or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.db.models import Credential, utcnow

#: A key used again within this window keeps its earlier stamp (the list shows minutes at best).
THROTTLE_S = 60


async def mark_used(
    db: AsyncSession, credential_ids: Iterable[str | None], *, now: dt.datetime | None = None
) -> None:
    """Stamp ``last_used_at`` on the given keys (unknown or empty ids are ignored).

    Args:
        db: The request's session; the stamp commits with it.
        credential_ids: The keys just decrypted for use.
        now: The time to record (tests pass a fixed clock).
    """
    ids = sorted({credential_id for credential_id in credential_ids if credential_id})
    if not ids:
        return
    now = now or utcnow()
    cutoff = now - dt.timedelta(seconds=THROTTLE_S)
    statement = (
        update(Credential)
        .where(
            Credential.id.in_(ids),
            or_(Credential.last_used_at.is_(None), Credential.last_used_at < cutoff),
        )
        .values(last_used_at=now, updated_at=Credential.updated_at)
        .execution_options(synchronize_session=False)
    )
    await db.execute(statement)


__all__ = ["THROTTLE_S", "mark_used"]
