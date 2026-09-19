"""Pack discovery for the worker.

`loader.py` imports the modules named by `LKAP_PACKS` and hands back a `Pack`
for the agent's `pack_id`. Packs themselves live in the separate `packs`
distribution; `agent -> packs -> contracts` is the only allowed direction.
"""
