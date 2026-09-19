"""Insurance claim first-notice-of-loss pack.

Ported behaviour-for-behaviour from the standalone Gemini Live demo
(``policies.py``, ``schemas.py``, ``policy_directory.py``, ``agent.py``,
``examples.py`` at commit ``c4472b0``) per ``docs/INSURANCE_PACK_MAPPING.md``.

This module (W1-PACK-INSURANCE-CORE) owns the deterministic core: schemas,
rules, the mock policy directory, prompts, the two-LLM-call claim workflow and
the UI state builder. ``manifest.py``, ``pack.py`` and ``tools.py`` (the LiveKit
wiring) are added by W2-PACK-INSURANCE-TOOLS.
"""

from __future__ import annotations
