"""lkap_testing — shared in-memory fakes for LKAP pack and worker tests (REVIEW-FINAL F-18).

One implementation of every ``packs.base`` Protocol, used by both
``agent/tests`` (through the ``fakes.fake_ctx`` shim, which adds a ``FakeRoom``
and a ``structlog`` logger) and ``packs/tests`` (directly). A Protocol change is
made here once. It depends on ``lkap_contracts`` and ``packs.base`` only, never
on ``lkap_agent`` (packs never import the worker, ARCHITECTURE §10).
"""

from lkap_testing.fake_ctx import (
    FakeBackgroundRunner,
    FakeFrameBuffer,
    FakeImageGen,
    FakeKbClient,
    FakeLogger,
    FakePackSessionContext,
    FakeStructuredLLM,
    FakeUiChannel,
    apply_patch_op,
    default_agent_config,
)

__all__ = [
    "FakeBackgroundRunner",
    "FakeFrameBuffer",
    "FakeImageGen",
    "FakeKbClient",
    "FakeLogger",
    "FakePackSessionContext",
    "FakeStructuredLLM",
    "FakeUiChannel",
    "apply_patch_op",
    "default_agent_config",
]
