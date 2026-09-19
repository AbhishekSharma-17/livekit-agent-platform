# packs

Use-case packs. `generic` (empty) and `insurance_claim` (reference). Interface: docs/CONTRACTS.md §8; parity: docs/INSURANCE_PACK_MAPPING.md.

## Known parity bugs (insurance_claim)

The insurance pack's rule engine (`packs/src/packs/insurance_claim/policies.py`) was ported from the original demo verbatim, including three bugs that the original also has. They are preserved on purpose for parity and are pinned down by `xfail(strict=True)` tests in `packs/tests/insurance_claim/test_rules.py` so nobody "fixes" one by accident:

- **`test_rules.py:254`** — `_without_negated_safety_mentions` only strips a standalone "no" (optionally followed by "was") before an injury term. It does not recognize the contraction "nobody" or present-tense "is"/"are", so phrases like "nobody is hurt" or "no one is hurt" still trip rule `SAFE-002` and incorrectly escalate a claim where nobody was hurt.
- **`test_rules.py:279`** — the `home_water_damage` `SAFE-001` check runs its unsafe/mold/sewage regex directly against the raw evidence text with no negation stripping at all (unlike `SAFE-002`'s `_has_positive_safety_language`). "no sewage and no mold" still matches `\bsewage\b`/`\bmold\b` and incorrectly triggers `emergency_escalation`.
- **`test_rules.py:345`** — `_document_provided`'s fallthrough checks a document title's first three words against the evidence text when no keyword group matches. For "Other driver and witness information" those words are "other", "driver", "and" — the common word "and" appears in almost any narrative, so the checklist item is wrongly marked as already provided even when no other-driver information was ever given.

Decide after handoff whether parity with the original demo or correctness wins for these three; until then they ship as documented, intentional bugs, not gaps.

## Adding a new pack

1. Create `packs/src/packs/<pack_id>/` with `__init__.py`, `manifest.py`, `pack.py` (and `seeds/*.md` if you want KBs seeded). Copy `packs/generic/` as the skeleton; `packs/insurance_claim/` is the full worked example.
2. `manifest.py` exposes `MANIFEST: PackManifest` — pure Pydantic, **no livekit import** (the api imports it).
3. `pack.py` exposes `PACK: Pack` implementing `packs.base.Pack` (tools, hooks, initial state — see CONTRACTS §8).
4. Copy `packs/tests/insurance_claim/fake_ctx.py` and `test_pack.py`'s pattern for your own tests; golden `UiState` fixtures are the cheapest regression net.
5. Register `packs.<pack_id>` in `LKAP_PACKS` for **both** the api and the worker, then restart both (the api caches manifests; the worker imports packs at start). `curl localhost:8080/v1/health` must list your id.
6. Panel (optional): a `web/src/panels/<ui_panel_id>/index.tsx` plus one entry in `web/src/panels/registry.ts`; without it you get the `generic` panel automatically.
7. Deploy: `scripts/vendor_agent_deps.sh` wheels the whole `packs/` package, so a pack under `packs/src/packs/` ships with the agent image automatically.

See `docs/REVIEW-FINAL.md` §6 Path B for the full walkthrough.
