"""Tool providers: connected apps through Composio (docs/v5/COMPOSIO.md, V5-18).

* :mod:`.adapter` — the vendor-neutral interface and its errors;
* :mod:`.composio` — the Composio REST adapter (over the guarded HTTP client);
* :mod:`.service` — the key test, catalogue, connect/callback/status/disconnect;
* :mod:`.bindings` — which tools may bind a Composio key (``routers/tools.py``);
* :mod:`.router` — ``/v1/tool-providers/composio/*``.
"""
