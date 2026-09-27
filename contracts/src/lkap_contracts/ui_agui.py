"""The AG-UI state adapter: LKAP ``lkap.ui.state`` messages ⇄ AG-UI state events (V5-43).

LKAP keeps its own wire protocol (:mod:`lkap_contracts.ui_protocol`): a seq-ordered
``UiSnapshot`` / ``UiPatch`` stream whose ops are ``set``, ``append``, ``remove`` and
``upsert`` on JSON-pointer-style paths. Nothing here changes it. This module maps it
to and from the AG-UI protocol's state events, so an AG-UI agent (LangGraph, Pydantic
AI, ADK, Mastra, …) can drive LKAP blocks and an AG-UI or json-render host can render
LKAP panels without a bespoke bridge (``docs/research-v4/panels-and-capabilities.md``
§5.2 item 1).

AG-UI, as checked on 2026-09-27:

* ``STATE_SNAPSHOT`` carries ``snapshot`` (the complete state) and ``STATE_DELTA``
  carries ``delta``, a list of RFC 6902 JSON Patch operations (``add``, ``remove``,
  ``replace``, ``move``, ``copy``, ``test``); every event may also carry
  ``timestamp``, ``rawEvent`` and ``metadata``
  (https://docs.ag-ui.com/concepts/events,
  https://docs.ag-ui.com/sdk/python/core/events — ``EventType.STATE_SNAPSHOT =
  "STATE_SNAPSHOT"``, ``EventType.STATE_DELTA = "STATE_DELTA"``, ``delta: List[Any]``);
* a delta is applied atomically, all or none, and a client that fails to apply one
  asks for a fresh snapshot (https://docs.ag-ui.com/concepts/state);
* JSON Patch and JSON Pointer are RFC 6902 (https://www.rfc-editor.org/rfc/rfc6902)
  and RFC 6901 (https://www.rfc-editor.org/rfc/rfc6901): ``~1`` is ``/`` and ``~0``
  is ``~`` inside a path segment; ``add`` on an object member sets it, on an array
  index *inserts* before it, and ``/-`` appends.

The documented mapping (LKAP op → RFC 6902), evaluated against the state *before*
each op, as the web reducer (``web/src/lib/ui-state.ts::applyOp``) and the worker
(``lkap_agent.ui.channel.apply_patch_op``) apply it:

==============================  =====================================================
LKAP ``UiPatchOp``              AG-UI ``STATE_DELTA`` operations
==============================  =====================================================
``set`` on an object member     ``add`` (creates or replaces the member; any missing
                                parent objects are ``add``-ed as ``{}`` first)
``set`` on an array index       ``replace`` (``add`` would insert)
``append``                      ``add`` at ``<path>/-`` (``add <path> [value]`` when
                                the list does not exist yet)
``remove`` without ``key``      ``remove`` (nothing when the path does not exist)
``remove`` with ``key``         ``remove <path>/<i>`` for each item whose ``key`` or
                                ``id`` is ``key``, highest index first
``upsert``                      ``replace <path>/<i>`` for the first item whose
                                ``key``/``id`` matches (``key``, else the value's
                                own ``key``/``id``), else ``add <path>/-``
==============================  =====================================================

``/activity`` keeps at most ``ACTIVITY_RING_SIZE`` rows: when an op grows it past
that, ``remove /activity/0`` follows, as the reducer trims it.

The reverse (:func:`agui_delta_to_patch`) accepts only paths under ``/blocks/<id>/``
by default (the ``state_delta`` action narrows it further to the blocks
``update_block`` may write): ``add``/``replace`` on an object member → ``set``;
``add`` at ``/-`` → ``append``; ``remove`` → ``remove``; ``replace`` at an array index
→ ``set``; ``add`` at an array index (an insert), ``move`` and ``copy`` → ``set`` of the
parent container as it is after the operation; ``test`` → nothing when it holds, and
the whole delta is refused when it does not. Every operation is checked against the
state as the previous ones left it, so the result is all or none.

:func:`apply_ui_ops` and :func:`apply_json_patch` are the two reference appliers the
round-trip tests use (``contracts/tests/test_ui_agui.py``).
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterable, Sequence
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from lkap_contracts.ui_protocol import ACTIVITY_RING_SIZE, UiPatch, UiPatchOp, UiSnapshot, UiState

__all__ = [
    "AGUI_STATE_DELTA",
    "AGUI_STATE_SNAPSHOT",
    "BLOCKS_PREFIX",
    "AguiJsonPatchOp",
    "AguiPatchError",
    "AguiStateDeltaEvent",
    "AguiStateSnapshotEvent",
    "agui_delta_to_patch",
    "apply_json_patch",
    "apply_ui_ops",
    "decode_pointer",
    "encode_pointer",
    "patch_to_agui",
    "patch_to_agui_delta",
    "snapshot_to_agui",
]

#: AG-UI ``EventType`` wire values of the two state events.
AGUI_STATE_SNAPSHOT: Final[str] = "STATE_SNAPSHOT"
AGUI_STATE_DELTA: Final[str] = "STATE_DELTA"
#: The only top-level state field an inbound delta may touch by default.
BLOCKS_PREFIX: Final[tuple[str, ...]] = ("blocks",)

JsonPatchOpName = Literal["add", "remove", "replace", "move", "copy", "test"]


class AguiPatchError(ValueError):
    """A delta that cannot be applied: a bad path, a missing target, a failed ``test``."""


class AguiJsonPatchOp(BaseModel):
    """One RFC 6902 operation, as AG-UI's ``STATE_DELTA.delta`` carries it."""

    op: JsonPatchOpName
    path: str
    value: Any = None
    from_: str | None = Field(default=None, alias="from")

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    def wire(self) -> dict[str, Any]:
        """The operation as plain JSON: ``value`` and ``from`` only where RFC 6902 has them."""
        out: dict[str, Any] = {"op": self.op, "path": self.path}
        if self.op in ("add", "replace", "test"):
            out["value"] = self.value
        if self.op in ("move", "copy"):
            out["from"] = self.from_
        return out


class AguiStateSnapshotEvent(BaseModel):
    """AG-UI ``STATE_SNAPSHOT``: the complete state."""

    type: Literal["STATE_SNAPSHOT"] = "STATE_SNAPSHOT"
    snapshot: dict[str, Any]
    timestamp: int | None = None


class AguiStateDeltaEvent(BaseModel):
    """AG-UI ``STATE_DELTA``: RFC 6902 operations on the state."""

    type: Literal["STATE_DELTA"] = "STATE_DELTA"
    delta: list[AguiJsonPatchOp]
    timestamp: int | None = None


# ----------------------------------------------------------------------- JSON Pointer


def encode_pointer(segments: Iterable[str]) -> str:
    """An RFC 6901 pointer for ``segments`` (``~`` → ``~0``, ``/`` → ``~1``)."""
    return "".join("/" + s.replace("~", "~0").replace("/", "~1") for s in segments)


def decode_pointer(path: str) -> list[str]:
    """The segments of an RFC 6901 pointer; ``""`` is the whole document.

    Raises:
        AguiPatchError: A pointer that does not start with ``/`` or has a bad ``~`` escape.
    """
    if path == "":
        return []
    if not path.startswith("/"):
        raise AguiPatchError(f"{path!r} is not a JSON Pointer (it must start with '/')")
    out: list[str] = []
    for raw in path[1:].split("/"):
        if "~" in raw.replace("~0", "").replace("~1", ""):
            raise AguiPatchError(f"{path!r} has an invalid '~' escape")
        out.append(raw.replace("~1", "/").replace("~0", "~"))
    return out


def _lkap_path(segments: Sequence[str]) -> str:
    """An LKAP path for ``segments``; LKAP paths have no escapes, so no segment may hold ``/``."""
    for segment in segments:
        if "/" in segment or segment == "":
            raise AguiPatchError(f"the path segment {segment!r} cannot be written to the panel")
    return "/" + "/".join(segments)


def _segments(path: str) -> list[str]:
    return [s for s in path.split("/") if s]


# ------------------------------------------------------------------ LKAP op semantics


def _item_matches(item: Any, key: str) -> bool:
    return isinstance(item, dict) and (item.get("key") == key or item.get("id") == key)


def _child(container: Any, segment: str, *, create: bool) -> Any:
    """Descend one LKAP segment (creating a dict in place of a missing or scalar child when asked)."""
    if isinstance(container, list):
        if not segment.isdigit() or int(segment) >= len(container):
            return None
        index = int(segment)
        if not isinstance(container[index], dict | list):
            if not create:
                return None
            container[index] = {}
        return container[index]
    if not isinstance(container, dict):
        return None
    child = container.get(segment)
    if not isinstance(child, dict | list):
        if not create:
            return None
        child = container[segment] = {}
    return child


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True)
    return json.loads(json.dumps(value, default=str))


def _apply_list_op(items: list[Any], value: Any, op: UiPatchOp) -> None:
    if op.op == "append":
        items.append(value)
    elif op.op == "upsert":
        candidate = op.key
        if candidate is None and isinstance(value, dict):
            candidate = value.get("key") or value.get("id")
        if candidate is not None:
            for index, existing in enumerate(items):
                if _item_matches(existing, candidate):
                    items[index] = value
                    return
        items.append(value)
    elif op.op == "remove" and op.key is not None:
        items[:] = [i for i in items if not _item_matches(i, op.key)]


def _apply_ui_op(doc: dict[str, Any], op: UiPatchOp) -> None:
    segments = _segments(op.path)
    if not segments:
        raise AguiPatchError("an op needs a path")
    container: Any = doc
    for segment in segments[:-1]:
        container = _child(container, segment, create=True)
        if container is None:
            return
    leaf = segments[-1]
    value = _jsonable(op.value)
    if isinstance(container, list):
        if not leaf.isdigit() or int(leaf) >= len(container):
            return
        index = int(leaf)
        if op.op == "remove" and op.key is None:
            del container[index]
        elif op.op == "set":
            container[index] = value
        else:
            current = container[index]
            items = current if isinstance(current, list) else []
            _apply_list_op(items, value, op)
            container[index] = items
    elif op.op == "set":
        container[leaf] = value
    elif op.op == "remove" and op.key is None:
        container.pop(leaf, None)
    else:
        current = container.get(leaf)
        items = current if isinstance(current, list) else []
        _apply_list_op(items, value, op)
        container[leaf] = items
    if segments == ["activity"] and isinstance(doc.get("activity"), list):
        del doc["activity"][:-ACTIVITY_RING_SIZE]


def apply_ui_ops(state: UiState | dict[str, Any], ops: Iterable[UiPatchOp]) -> dict[str, Any]:
    """Apply LKAP ops to a copy of ``state`` as plain JSON (the reducer's semantics) and return it."""
    doc = _jsonable(state) if isinstance(state, BaseModel) else copy.deepcopy(state)
    for op in ops:
        _apply_ui_op(doc, op)
    return doc


# --------------------------------------------------------------------- RFC 6902 apply


def _resolve(doc: Any, segments: Sequence[str]) -> tuple[bool, Any]:
    node = doc
    for segment in segments:
        if isinstance(node, dict) and segment in node:
            node = node[segment]
        elif isinstance(node, list) and segment.isdigit() and int(segment) < len(node):
            if len(segment) > 1 and segment.startswith("0"):
                return False, None
            node = node[int(segment)]
        else:
            return False, None
    return True, node


def _parent(doc: Any, segments: Sequence[str], path: str) -> Any:
    found, parent = _resolve(doc, segments[:-1])
    if not found or not isinstance(parent, dict | list):
        raise AguiPatchError(f"the parent of {path!r} does not exist")
    return parent


def _index(parent: list[Any], segment: str, path: str, *, insert: bool) -> int:
    if segment == "-" and insert:
        return len(parent)
    if not segment.isdigit() or (len(segment) > 1 and segment.startswith("0")):
        raise AguiPatchError(f"{path!r} does not name an array index")
    index = int(segment)
    if index > len(parent) or (not insert and index == len(parent)):
        raise AguiPatchError(f"{path!r} is past the end of the array")
    return index


def _add(doc: Any, segments: list[str], value: Any, path: str) -> Any:
    if not segments:
        return copy.deepcopy(value)
    parent = _parent(doc, segments, path)
    if isinstance(parent, list):
        parent.insert(_index(parent, segments[-1], path, insert=True), copy.deepcopy(value))
    else:
        parent[segments[-1]] = copy.deepcopy(value)
    return doc


def _remove(doc: Any, segments: list[str], path: str) -> tuple[Any, Any]:
    if not segments:
        raise AguiPatchError("the whole state cannot be removed")
    parent = _parent(doc, segments, path)
    if isinstance(parent, list):
        return doc, parent.pop(_index(parent, segments[-1], path, insert=False))
    if segments[-1] not in parent:
        raise AguiPatchError(f"{path!r} does not exist")
    return doc, parent.pop(segments[-1])


def _apply_json_op(doc: Any, op: AguiJsonPatchOp) -> Any:
    segments = decode_pointer(op.path)
    if op.op == "add":
        return _add(doc, segments, op.value, op.path)
    if op.op == "remove":
        return _remove(doc, segments, op.path)[0]
    if op.op == "replace":
        found, _ = _resolve(doc, segments)
        if not found:
            raise AguiPatchError(f"{op.path!r} does not exist")
        doc, _ = _remove(doc, segments, op.path) if segments else (doc, None)
        return _add(doc, segments, op.value, op.path)
    if op.op in ("move", "copy"):
        if op.from_ is None:
            raise AguiPatchError(f"{op.op} needs a 'from'")
        source = decode_pointer(op.from_)
        found, value = _resolve(doc, source)
        if not found:
            raise AguiPatchError(f"{op.from_!r} does not exist")
        if op.op == "move":
            if segments[: len(source)] == source and len(segments) > len(source):
                raise AguiPatchError("a value cannot be moved into itself")
            doc, value = _remove(doc, source, op.from_)
        return _add(doc, segments, value, op.path)
    found, value = _resolve(doc, segments)  # test
    if not found or value != op.value:
        raise AguiPatchError(f"test failed at {op.path!r}")
    return doc


def apply_json_patch(doc: Any, delta: Iterable[AguiJsonPatchOp | dict[str, Any]]) -> Any:
    """Apply RFC 6902 operations to a copy of ``doc``, all or none, and return the result.

    Raises:
        AguiPatchError: An operation that cannot be applied (the input is unchanged).
    """
    out = copy.deepcopy(doc)
    for raw in delta:
        out = _apply_json_op(out, _parse_op(raw))
    return out


def _parse_op(raw: AguiJsonPatchOp | dict[str, Any]) -> AguiJsonPatchOp:
    if isinstance(raw, AguiJsonPatchOp):
        return raw
    try:
        return AguiJsonPatchOp.model_validate(raw)
    except ValidationError as exc:
        raise AguiPatchError(f"not a JSON Patch operation: {exc.errors()[0]['msg']}") from exc


# --------------------------------------------------------------------- LKAP → AG-UI


def _to_agui(doc: dict[str, Any], op: UiPatchOp) -> list[AguiJsonPatchOp]:
    """The RFC 6902 operations equal to ``op`` on ``doc`` (which is not modified)."""
    segments = _segments(op.path)
    if not segments:
        raise AguiPatchError("an op needs a path")
    out: list[AguiJsonPatchOp] = []
    # Walk the parents the way LKAP does, adding the ones LKAP would create.
    node: Any = doc
    for depth, segment in enumerate(segments[:-1]):
        if isinstance(node, list):
            if not segment.isdigit() or int(segment) >= len(node):
                return out
            child = node[int(segment)]
            if not isinstance(child, dict | list):
                out.append(
                    AguiJsonPatchOp(op="replace", path=encode_pointer(segments[: depth + 1]), value={})
                )
                child = {}
            node = child
            continue
        child = node.get(segment) if isinstance(node, dict) else None
        if not isinstance(child, dict | list):
            out.append(AguiJsonPatchOp(op="add", path=encode_pointer(segments[: depth + 1]), value={}))
            child = {}
        node = child
    leaf = segments[-1]
    pointer = encode_pointer(segments)
    value = _jsonable(op.value)
    if isinstance(node, list):
        if not leaf.isdigit() or int(leaf) >= len(node):
            return out
        current: Any = node[int(leaf)]
        if op.op == "set":
            return [*out, AguiJsonPatchOp(op="replace", path=pointer, value=value)]
        if op.op == "remove" and op.key is None:
            return [*out, AguiJsonPatchOp(op="remove", path=pointer)]
    else:
        present = isinstance(node, dict) and leaf in node
        current = node.get(leaf) if isinstance(node, dict) else None
        if op.op == "set":
            return [*out, AguiJsonPatchOp(op="add", path=pointer, value=value)]
        if op.op == "remove" and op.key is None:
            return [*out, AguiJsonPatchOp(op="remove", path=pointer)] if present else out
    # A list op (append, upsert, keyed remove) on the leaf.
    if not isinstance(current, list):
        if op.op == "remove":
            return [
                *out,
                AguiJsonPatchOp(op="replace" if isinstance(node, list) else "add", path=pointer, value=[]),
            ]
        return [
            *out,
            AguiJsonPatchOp(op="replace" if isinstance(node, list) else "add", path=pointer, value=[value]),
        ]
    if op.op == "append":
        return [*out, AguiJsonPatchOp(op="add", path=pointer + "/-", value=value)]
    if op.op == "remove":
        matches = [i for i, item in enumerate(current) if _item_matches(item, op.key or "")]
        return [*out, *(AguiJsonPatchOp(op="remove", path=f"{pointer}/{i}") for i in reversed(matches))]
    candidate = op.key
    if candidate is None and isinstance(value, dict):
        candidate = value.get("key") or value.get("id")
    if candidate is not None:
        for index, item in enumerate(current):
            if _item_matches(item, candidate):
                return [*out, AguiJsonPatchOp(op="replace", path=f"{pointer}/{index}", value=value)]
    return [*out, AguiJsonPatchOp(op="add", path=pointer + "/-", value=value)]


def patch_to_agui_delta(
    ops: UiPatch | Sequence[UiPatchOp], state: UiState | dict[str, Any]
) -> list[AguiJsonPatchOp]:
    """The AG-UI ``STATE_DELTA.delta`` equal to LKAP ``ops`` applied to ``state``.

    Args:
        ops: A ``UiPatch`` or its ops.
        state: The state *before* the ops (a keyed ``remove`` or an ``upsert``
            needs it to find the item's index; a ``set`` needs it to tell an
            object member from an array index).

    Returns:
        The RFC 6902 operations; applying them to ``state`` gives what the LKAP
        ops give (``apply_json_patch(state, delta) == apply_ui_ops(state, ops)``).
    """
    doc = _jsonable(state) if isinstance(state, BaseModel) else copy.deepcopy(state)
    items = ops.ops if isinstance(ops, UiPatch) else list(ops)
    delta: list[AguiJsonPatchOp] = []
    for op in items:
        step = _to_agui(doc, op)
        rfc_after = apply_json_patch(doc, step)
        _apply_ui_op(doc, op)
        # LKAP trims `/activity` to the ring; RFC 6902 needs the removes spelled out.
        extra = _length(rfc_after, "activity") - _length(doc, "activity")
        step.extend(AguiJsonPatchOp(op="remove", path="/activity/0") for _ in range(max(extra, 0)))
        delta.extend(step)
    return delta


def _length(doc: Any, key: str) -> int:
    value = doc.get(key) if isinstance(doc, dict) else None
    return len(value) if isinstance(value, list) else 0


def patch_to_agui(patch: UiPatch, state: UiState | dict[str, Any]) -> AguiStateDeltaEvent:
    """A ``UiPatch`` as an AG-UI ``STATE_DELTA`` event (see :func:`patch_to_agui_delta`)."""
    return AguiStateDeltaEvent(delta=patch_to_agui_delta(patch, state))


def snapshot_to_agui(snapshot: UiSnapshot) -> AguiStateSnapshotEvent:
    """A ``UiSnapshot`` as an AG-UI ``STATE_SNAPSHOT`` event (the ``UiState`` as plain JSON)."""
    return AguiStateSnapshotEvent(snapshot=snapshot.state.model_dump(mode="json", by_alias=True))


# --------------------------------------------------------------------- AG-UI → LKAP


def agui_delta_to_patch(
    delta: Iterable[AguiJsonPatchOp | dict[str, Any]],
    state: UiState | dict[str, Any],
    *,
    prefix: Sequence[str] = BLOCKS_PREFIX,
) -> list[UiPatchOp]:
    """LKAP ops equal to an AG-UI ``STATE_DELTA.delta`` applied to ``state`` (all or none).

    Args:
        delta: RFC 6902 operations (models or plain dicts).
        state: The current state; every operation is checked against it as the
            previous operations left it.
        prefix: The top-level path every operation (and every ``from``) must be
            under, with at least one more segment (``/blocks/<id>...``); the
            root and the prefix itself are refused.

    Returns:
        The LKAP ops (``apply_ui_ops(state, ops) == apply_json_patch(state, delta)``).

    Raises:
        AguiPatchError: A path outside ``prefix``, a segment LKAP cannot address,
            a missing target, a failed ``test`` or an operation RFC 6902 refuses.
    """
    doc = _jsonable(state) if isinstance(state, BaseModel) else copy.deepcopy(state)
    ops: list[UiPatchOp] = []
    for raw in delta:
        op = _parse_op(raw)
        segments = decode_pointer(op.path)
        _check_prefix(segments, op.path, prefix)
        if op.from_ is not None and op.op in ("move", "copy"):
            _check_prefix(decode_pointer(op.from_), op.from_, prefix)
        after = _apply_json_op(copy.deepcopy(doc), op)
        ops.extend(_from_agui(doc, op, segments, after))
        doc = after
    return ops


def _check_prefix(segments: list[str], path: str, prefix: Sequence[str]) -> None:
    if len(segments) <= len(prefix) or segments[: len(prefix)] != list(prefix):
        allowed = encode_pointer(prefix) + "/<id>"
        raise AguiPatchError(f"{path!r} is outside {allowed}")


def _from_agui(before: Any, op: AguiJsonPatchOp, segments: list[str], after: Any) -> list[UiPatchOp]:
    if op.op == "test":
        return []
    _, parent = _resolve(before, segments[:-1])
    leaf = segments[-1]
    if op.op == "remove":
        return [UiPatchOp(op="remove", path=_lkap_path(segments))]
    if op.op == "add" and leaf == "-" and isinstance(parent, list):
        return [UiPatchOp(op="append", path=_lkap_path(segments[:-1]), value=op.value)]
    if op.op in ("add", "replace") and (
        isinstance(parent, dict) or (op.op == "replace" and isinstance(parent, list))
    ):
        return [UiPatchOp(op="set", path=_lkap_path(segments), value=op.value)]
    # An insert at an array index, a move or a copy: set every container that changed.
    ops: list[UiPatchOp] = []
    targets = [segments[:-1]]
    if op.op == "move" and op.from_ is not None:
        source = decode_pointer(op.from_)
        targets.insert(0, source[:-1])
    for target in targets:
        present, value = _resolve(after, target)
        if not present:
            continue
        ops.append(UiPatchOp(op="set", path=_lkap_path(target), value=copy.deepcopy(value)))
    return ops
