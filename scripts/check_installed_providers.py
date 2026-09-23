#!/usr/bin/env python3
"""Build-time import gate for the agent worker image (CONTRACTS-V2 §7, D-V2-8).

Run **inside** the Docker build, after the flavor's plugin packages are
installed (`agent/Dockerfile`), as::

    python requirements/check_installed_providers.py --flavor slim --out /app/installed_providers.json

This is a *mirrored copy* of this exact file, kept at
``agent/requirements/check_installed_providers.py`` by
``scripts/gen_plugin_requirements.py`` (run it, or its ``--check`` mode, after
editing this file) — `agent/Dockerfile`'s build context is `agent/`
(deliberately: it mirrors what `lk agent create`/`lk agent deploy` build
from, see the Dockerfile's own header), so this repo-root script is not
otherwise reachable from that build. The two copies must be byte-identical;
CI (`python.yml`) checks that with `gen_plugin_requirements.py --check`.

What it does, and why (D-V2-8): every ``availability="available"`` registry
entry whose ``worker_image`` is carried by this flavor (plus the four ids
CONTRACTS-V2 §7 ships in ``slim`` that ``worker_image`` alone would miss —
see ``_HARDCODED_SLIM_IDS`` and `docs/v2/_asks.md` ask #32 / ruling R-V2-1)
gets its ``python_class`` imported for real. uv/pip resolving a package is
not the acceptance criterion; importing the class is — that's what catches
native-wheel breakage (bithuman, Krisp, Azure Speech, awscrt) at build time
instead of at a customer's first call.

Two registry kinds are *not* attempted here even though some of their
entries are ``worker_image="slim"``: ``embedding`` (built by
``lkap_api.kb.embed`` inside the **api** process — the worker never
constructs one; see ``agent/src/lkap_agent/providers/factory.py``'s own
`"embedding providers are built by lkap_api.kb.embed"` comment) and
``secret_bag`` (``python_class=""``, no class at all — a credential-only
registry stub). Both are recorded under ``"skipped"`` in the output
manifest, not silently dropped, so the manifest is still a complete account
of every id CONTRACTS-V2 §7 assigns to this flavor.

The output manifest is what the worker reads at start and forwards verbatim
(its ``provider_ids`` field) as ``installed_provider_ids`` in
``POST /internal/v1/workers/register`` (CONTRACTS-V2 §5, D-V2-8) — see
`docs/v2/_asks.md` for the hand-off to V2-07 confirming the exact field name
the worker should read.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
from pathlib import Path
from typing import Any

#: Registry kinds the worker process never constructs itself (D-V2-8 note
#: above) — excluded from the import attempt, but listed in the manifest's
#: ``skipped`` array so nothing silently disappears. Kept as a literal
#: fallback (rather than only ever importing ``CONSTRUCTIBLE_KINDS`` below)
#: so this script still runs against a bare `lkap-contracts`-only
#: environment (e.g. the scratch-venv checks used to verify this script
#: without a Docker daemon).
_FALLBACK_AGENT_EXCLUDED_KINDS = frozenset({"embedding", "secret_bag"})


def _agent_excluded_kinds() -> frozenset[str]:
    """The complement of `lkap_agent`'s own `CONSTRUCTIBLE_KINDS`, when importable.

    `agent/src/lkap_agent/providers/factory.py` exports `CONSTRUCTIBLE_KINDS`
    (the registry kinds the worker's `ProviderFactory` builds) as its single
    source of truth; importing it here instead of hand-maintaining a second
    copy means this build gate can never drift from what the worker actually
    constructs. Falls back to `_FALLBACK_AGENT_EXCLUDED_KINDS` when
    `lkap_agent` is not on the path (this script also runs standalone
    against just `lkap-contracts` for offline verification).
    """
    try:
        import typing

        from lkap_contracts.providers import ProviderKind

        from lkap_agent.providers.factory import CONSTRUCTIBLE_KINDS

        all_kinds = set(typing.get_args(ProviderKind))
        return frozenset(all_kinds - set(CONSTRUCTIBLE_KINDS))
    except ImportError:
        return _FALLBACK_AGENT_EXCLUDED_KINDS


AGENT_EXCLUDED_KINDS = _agent_excluded_kinds()

#: CONTRACTS-V2 §7 ships these four in `slim` ("v1 set + silero +
#: turn-detector + inference"), but ruling R-V2-1 (PLAN-V2 §8) keeps their
#: registry `worker_image` at `"full"` so the v1 `status=="mvp"` id set
#: stays byte-identical (docs/v2/_asks.md ask #32). `by_image("slim")` alone
#: would therefore miss them; hardcode the ids here instead of deriving
#: `slim` purely from `worker_image`.
HARDCODED_SLIM_IDS = frozenset(
    {"silero-vad", "inference-vad", "turn-detector-plugin", "inference-turn-detector"}
)


def selected_ids(flavor: str) -> set[str]:
    """Return the registry ids this image flavor is responsible for constructing.

    Args:
        flavor: ``"slim"`` or ``"full"``.

    Returns:
        Provider ids whose kind the worker process can construct (excludes
        ``AGENT_EXCLUDED_KINDS``), plus the hardcoded slim additions when
        ``flavor == "slim"``.
    """
    from lkap_contracts.providers import by_image  # local import: only needed at run time

    ids = {s.id for s in by_image(flavor) if s.kind not in AGENT_EXCLUDED_KINDS}
    if flavor == "slim":
        ids |= HARDCODED_SLIM_IDS
    return ids


def _resolve(python_class: str) -> Any:
    """Import the longest importable module prefix of a dotted path, then ``getattr`` the rest.

    Needed because several registry ``python_class`` values are classmethod
    factories, not plain classes — e.g. ``livekit.plugins.silero.VAD.load``
    or ``livekit.agents.inference.eot.TurnDetector`` — so not every dotted
    segment is itself a module.

    Args:
        python_class: A dotted path like ``"livekit.plugins.silero.VAD.load"``.

    Returns:
        The resolved attribute (class, classmethod, or function).

    Raises:
        ImportError: If no prefix of the dotted path imports.
        AttributeError: If a prefix imports but a later segment does not exist on it.
    """
    parts = python_class.split(".")
    last_error: Exception | None = None
    for i in range(len(parts), 0, -1):
        module_name = ".".join(parts[:i])
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            last_error = exc
            continue
        obj: Any = module
        for attr in parts[i:]:
            obj = getattr(obj, attr)
        return obj
    raise ImportError(f"no importable prefix found for {python_class!r}") from last_error


def build_manifest(flavor: str) -> dict[str, Any]:
    """Import every selected id's ``python_class`` and assemble the manifest.

    Every failure is collected (not raised on the first one) so a build log
    lists everything wrong in one pass, per CONTRACTS-V2 §7 / PLAN-V2 §8's
    "the build is the gate" acceptance wording.

    Args:
        flavor: ``"slim"`` or ``"full"``.

    Returns:
        A JSON-serialisable manifest: ``flavor``, ``sdk_version``,
        ``generated_at``, ``provider_ids`` (ok), ``skipped`` (excluded
        kinds), ``failed`` (import errors — must be empty for the build to pass).
    """
    from lkap_contracts.providers import get

    ok: list[str] = []
    skipped: list[str] = []
    failed: list[dict[str, str]] = []

    from lkap_contracts.providers import by_image

    all_considered = {s.id for s in by_image(flavor)}
    if flavor == "slim":
        all_considered |= HARDCODED_SLIM_IDS

    for pid in sorted(all_considered):
        spec = get(pid)
        if spec.kind in AGENT_EXCLUDED_KINDS or not spec.python_class:
            skipped.append(pid)
            continue
        try:
            _resolve(spec.python_class)
        except Exception as exc:  # noqa: BLE001 - collect every failure, never abort early
            failed.append(
                {"id": pid, "python_class": spec.python_class, "error": f"{type(exc).__name__}: {exc}"}
            )
            continue
        ok.append(pid)

    try:
        import livekit.agents as _livekit_agents

        sdk_version = getattr(_livekit_agents, "__version__", "unknown")
    except ImportError:
        sdk_version = "unknown"

    return {
        "flavor": flavor,
        "sdk_version": sdk_version,
        "generated_at": time.time(),
        "provider_ids": ok,
        "skipped": skipped,
        "failed": failed,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flavor", choices=["slim", "full"], required=True)
    parser.add_argument("--out", required=True, help="Path to write installed_providers.json")
    args = parser.parse_args(argv)

    manifest = build_manifest(args.flavor)
    Path(args.out).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    failed = manifest["failed"]
    if failed:
        print(f"check_installed_providers: {len(failed)} import failure(s):", file=sys.stderr)
        for entry in failed:
            print(f"  {entry['id']} ({entry['python_class']}): {entry['error']}", file=sys.stderr)
        print(
            "check_installed_providers: fix the package/wheel, or ask the registry owner to "
            "flip this entry's availability to 'deferred' with a note (PLAN-V2 §7 risk table).",
            file=sys.stderr,
        )
        return 1

    print(
        f"check_installed_providers: flavor={args.flavor} sdk={manifest['sdk_version']} "
        f"ok={len(manifest['provider_ids'])} skipped={len(manifest['skipped'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
