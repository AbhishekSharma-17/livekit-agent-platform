"""Deepgram's charge per request — **designed, not built** (V4-17, D-V4-45, R-V4-48; ask #94).

Nothing in this module runs. The opt-in validator
(:func:`lkap_api.costs.vendors.validate_reconcile`) refuses ``"deepgram"`` until
ask #94 is decided, so no workspace can switch on a reconciliation that would
do nothing.

Why it waits: Deepgram returns the USD cost of one request with a regular key,
``GET https://api.deepgram.com/v1/projects/{project_id}/requests/{request_id}``
→ ``response.details.usd`` (plus ``duration``, ``models``), authorised with
``Authorization: Token <api_key>``. The path needs the **project id**, and the
``deepgram-stt``/``deepgram-tts`` credential forms collect only the key. Ask #94
proposes an optional ``project_id`` secret-bag field ("Found in the Deepgram
console under Projects; lets LKAP read the exact charge per request").

The design, the same shape as :mod:`lkap_api.costs.vendors.openrouter`:

1. **Ids.** The worker already reports them: with ``cost_reconcile`` non-empty,
   ``observability.py`` posts ``metrics {kind: "provider_requests", data: {stt: [...],
   tts: [...]}}``. The Deepgram STT plugin carries the stream ``Metadata.request_id``
   (a UUID) into ``STTMetrics.request_id``. That this is the same id the requests
   endpoint accepts is implied by Deepgram's logs guide, not stated
   (``docs/v4/_sources/costs-speech.md``); the first live run confirms it, and a
   miss is an ask, not a patch. TTS request ids from the Aura plugin are to be
   checked the same way before ``tts`` ids are looked up.
2. **Credential.** The slot's ``credential_id`` (else the workspace default, else
   the only ``deepgram-stt``/``deepgram-tts`` row of the workspace), decrypted;
   no ``project_id`` → the job records "not reconcilable" and writes nothing.
3. **Lookup.** One ``GET`` per id through the job's ``net_guard`` client, ≤ 10/s,
   the same transient retry as OpenRouter; a ``404`` or a ``Pending`` record
   (the logs guide's state for "response not yet recorded") is retried once
   after 30 s by re-enqueueing the job.
4. **Write.** ``sum(response.details.usd)`` per slot onto the Deepgram line's
   ``vendor_usd`` with ``vendor_ref="<n> requests"``, added into
   ``sessions.reconciled_usd``; audit ``cost_reconciled`` with ``vendor="deepgram"``.
   Idempotent like the OpenRouter path (a rerun recomputes and overwrites).
"""

from __future__ import annotations
