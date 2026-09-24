#!/usr/bin/env python3
"""Run the registry catalog drift report locally (docs/v4/CUSTOM-MODELS.md D-V4-27).

A thin entry point for ``uv run --project api python -m lkap_api.catalogs.drift "$@"``;
the logic and its tests live in the api package. Examples::

    scripts/catalog_drift.py --only keyless --out /tmp/drift
    OPENAI_API_KEY=... scripts/catalog_drift.py --only openai-llm,openai-tts

Writes ``catalog-drift.md`` and ``catalog-drift.json`` under ``--out`` (default:
the current directory) and always exits 0. Keys are read from the environment
(``SECRET_ENV_BY_HOME`` in the module); none is ever printed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parent.parent / "api"


def main() -> None:
    """Replace this process with the api module's CLI, passing every argument through."""
    argv = ["uv", "run", "--project", str(API_DIR), "python", "-m", "lkap_api.catalogs.drift", *sys.argv[1:]]
    os.execvp(argv[0], argv)


if __name__ == "__main__":
    main()
