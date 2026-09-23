# Regenerate: cd agent && uv run python ../web/scripts/gen-block-patch-parity.py > ../web/tests/fixtures/block_patch_parity.json
"""Generate /blocks patch parity vectors from the agent's real apply_patch_op."""
import copy, json, sys
from lkap_contracts.ui_protocol import UiState, UiPatchOp
from lkap_agent.ui.channel import apply_patch_op

START = {
    "t": {"columns": [{"key": "item", "label": "Item", "type": "string"}], "rows": [{"id": "r1", "item": "a"}, {"id": "r2", "item": "b"}], "selected_row": None},
    "g": {"asset_ids": ["a1"], "selected": None},
    "f": {"schema": {}, "values": {}, "status": "idle", "submitted_at": None},
    "k": {"items": []},
    "nested": {"lists": [[1, 2], [3]], "scalar": 5},
}

CASES = [
    ("set whole block", [{"op": "set", "path": "/blocks/d", "value": {"asset_id": None, "url": "https://x/y.pdf", "page": 2, "highlights": []}}]),
    ("set field", [{"op": "set", "path": "/blocks/t/selected_row", "value": "r2"}]),
    ("table_append", [{"op": "set", "path": "/blocks/t/columns", "value": [{"key": "item", "label": "Item", "type": "string"}, {"key": "qty", "label": "Qty", "type": "number"}]}, {"op": "append", "path": "/blocks/t/rows", "value": {"id": "r3", "item": "c", "qty": 2}}]),
    ("gallery append", [{"op": "append", "path": "/blocks/g/asset_ids", "value": "a2"}]),
    ("form submit", [{"op": "set", "path": "/blocks/f/values", "value": {"name": "x"}}, {"op": "set", "path": "/blocks/f/status", "value": "submitted"}, {"op": "set", "path": "/blocks/f/submitted_at", "value": 12.5}]),
    ("citations replace", [{"op": "set", "path": "/blocks/k/items", "value": [{"chunk_id": "c1", "filename": "f", "score": 0.5, "text": "t"}]}]),
    ("upsert by id replaces", [{"op": "upsert", "path": "/blocks/t/rows", "value": {"id": "r1", "item": "A"}}]),
    ("upsert new appends", [{"op": "upsert", "path": "/blocks/t/rows", "value": {"id": "r9", "item": "Z"}}]),
    ("upsert with op.key", [{"op": "upsert", "path": "/blocks/t/rows", "key": "r2", "value": {"id": "r2", "item": "B"}}]),
    ("upsert by key field", [{"op": "upsert", "path": "/blocks/x/list", "value": {"key": "k1", "v": 1}}, {"op": "upsert", "path": "/blocks/x/list", "value": {"key": "k1", "v": 2}}]),
    ("keyed remove", [{"op": "remove", "path": "/blocks/t/rows", "key": "r1"}]),
    ("unkeyed remove leaf", [{"op": "remove", "path": "/blocks/t/selected_row"}]),
    ("unkeyed remove block", [{"op": "remove", "path": "/blocks/g"}]),
    ("numeric index set", [{"op": "set", "path": "/blocks/t/rows/1", "value": {"id": "r2", "item": "bee"}}]),
    ("numeric index nested set", [{"op": "set", "path": "/blocks/t/rows/0/item", "value": "aye"}]),
    ("numeric index unkeyed remove", [{"op": "remove", "path": "/blocks/t/rows/0"}]),
    ("numeric index out of range set is a no-op", [{"op": "set", "path": "/blocks/t/rows/9", "value": {"id": "rx"}}]),
    ("numeric index out of range intermediate is a no-op", [{"op": "set", "path": "/blocks/t/rows/9/item", "value": "x"}]),
    ("non-numeric index into a list is a no-op", [{"op": "set", "path": "/blocks/t/rows/first/item", "value": "x"}]),
    ("append into a list item", [{"op": "append", "path": "/blocks/nested/lists/1", "value": 4}]),
    ("append onto a scalar replaces it with a list", [{"op": "append", "path": "/blocks/nested/scalar", "value": 1}]),
    ("missing intermediates are created", [{"op": "set", "path": "/blocks/new/a/b", "value": 1}]),
    ("scalar intermediate becomes a dict", [{"op": "set", "path": "/blocks/nested/scalar/deep", "value": 1}]),
    ("empty segments are ignored", [{"op": "set", "path": "/blocks//t//selected_row", "value": "r1"}]),
    ("set root blocks", [{"op": "set", "path": "/blocks", "value": {"only": {}}}]),
    ("no ~1 unescaping in block paths", [{"op": "set", "path": "/blocks/new/a~1b", "value": 1}]),
    ("keyed remove on missing list makes an empty list", [{"op": "remove", "path": "/blocks/g/nothing", "key": "x"}]),
]

out = []
for name, ops in CASES:
    state = UiState(blocks=copy.deepcopy(START))
    for op in ops:
        apply_patch_op(state, UiPatchOp.model_validate(op))
    out.append({"name": name, "ops": ops, "expected": state.blocks})
json.dump({"start": START, "cases": out}, sys.stdout, indent=1)
