"""Post-call QA scoring job (CONTRACTS-V2 §1.4/§4.3, ARCHITECTURE-V2 D-V2-13/D-V2-16).

`job.py` registers the `qa_scoring` job handler as an import side effect, so
importing this package (or `enqueue_for_session`) is enough to make the kind
runnable by `JobsService`.
"""

from __future__ import annotations

from lkap_api.qa import job as _job  # noqa: F401 - registers the job handler
from lkap_api.qa.job import enqueue_for_session
from lkap_api.qa.rubric import DEFAULT_RUBRIC_PROMPT, DEFAULT_TAGS
from lkap_api.qa.scorer import score_session

__all__ = ["DEFAULT_RUBRIC_PROMPT", "DEFAULT_TAGS", "enqueue_for_session", "score_session"]
