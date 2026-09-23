"""`qa_scoring` job registration and the `enqueue_for_session` entry point.

Nothing in this wave calls `enqueue_for_session` yet: the natural callers are
V2-03's `PUT /internal/v1/sessions/{id}/summary` (`routers/internal.py`, when
`config.qa.enabled`) and V2-12's `POST /v1/sessions/{id}/qa` re-score endpoint
(`routers/sessions.py`) — neither file is owned by this package. A one-line
ask for both is filed in `docs/v2/_asks.md`.
"""

from __future__ import annotations

from typing import Any

from fastapi import BackgroundTasks

from lkap_api.jobs.context import JobContext
from lkap_api.jobs.kinds import QA_SCORING
from lkap_api.jobs.registry import job
from lkap_api.jobs.service import JobsService
from lkap_api.qa.scorer import score_session


@job(QA_SCORING)
async def handle_qa_scoring(ctx: JobContext, payload: dict[str, Any]) -> None:
    """Job handler: score the session named in `payload["session_id"]`."""
    await score_session(ctx, str(payload["session_id"]))


async def enqueue_for_session(
    jobs: JobsService, session_id: str, *, background_tasks: BackgroundTasks | None = None
) -> str:
    """Schedule QA scoring for `session_id`. Returns the new job's id.

    Args:
        jobs: The `JobsService` to enqueue on.
        session_id: The `sessions.id` to score.
        background_tasks: Pass this from a request handler (the summary
            endpoint) so the judge LLM call runs after the response is sent
            rather than blocking it.
    """
    return await jobs.enqueue(QA_SCORING, {"session_id": session_id}, background_tasks=background_tasks)
