"""Text simulations, judges and the pre-publish gate (V5-29, D-V5-29).

``runner`` registers the ``agent_tests_run`` job handler as an import side
effect (``jobs.handlers.load_all_handlers`` imports it); ``router`` serves
``/v1/agents/{id}/tests/*``; ``service`` holds the run model, the publish gate
(``check_publish_gate``) and the per-session tool mocks the resolve delivers
(``tool_mocks_for_session``).
"""

from __future__ import annotations

from lkap_api.agent_tests.service import PublishGateError, check_publish_gate, tool_mocks_for_session

__all__ = ["PublishGateError", "check_publish_gate", "tool_mocks_for_session"]
