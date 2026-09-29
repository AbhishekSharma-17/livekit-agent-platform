"""Tool parameters that also take their value as JSON text (V6-30, F-2).

A model sometimes sends a JSON **string** where a tool parameter is an object or a list of
objects: ``"shapes": "[{\\"kind\\": \\"rect\\", …}]"``, or a list whose items are strings of
JSON. Pydantic refuses both ("Input should be a valid list", "… a valid dictionary"), the
model retries, and every retry spends one of the agent's tool steps (in the V6 demos run a
turn could end with no reply). :func:`json_list` and :func:`json_object` are ``Annotated``
metadata for those parameters (and for nested list fields of their models): a string is read
with strict :func:`json.loads`, at most :data:`MAX_JSON_ARGUMENT_CHARS` characters, and the
result is then validated **exactly as before** — same types, bounds and limits; nothing else
is loosened. Text that is not valid JSON of the expected shape is refused with a message that
shows the expected shape. A blank string for a list means "not given" (``None``). Inside decoded
text a ``null`` property means "use the default", as the strict tool schema tells the model and as
livekit-agents does for the arguments it decodes itself (``llm.utils._inject_schema_defaults``).

The metadata does not change the JSON schema the model is shown.
"""

from __future__ import annotations

import json
from typing import Any, Final

from pydantic import BeforeValidator

__all__ = ["MAX_JSON_ARGUMENT_CHARS", "decode_json_list", "decode_json_object", "json_list", "json_object"]

#: Longest JSON text one parameter may carry (a notebook or chart write is a few KB at most).
MAX_JSON_ARGUMENT_CHARS: Final[int] = 32_000


def _without_nulls(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _without_nulls(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_without_nulls(item) for item in value]
    return value


def _loads(text: str, *, shape: str, example: str) -> Any:
    if len(text) > MAX_JSON_ARGUMENT_CHARS:
        raise ValueError(
            f"that text is too long ({len(text)} characters, at most {MAX_JSON_ARGUMENT_CHARS}); "
            f"pass {shape} like {example}"
        )
    try:
        decoded = json.loads(text)
    except (ValueError, RecursionError):
        raise ValueError(f"pass {shape} like {example}; that text is not valid JSON") from None
    return _without_nulls(decoded)


def _object(value: Any, *, example: str) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text.startswith("{"):
        raise ValueError(f"pass an object like {example}, not text")
    decoded = _loads(text, shape="an object", example=example)
    if not isinstance(decoded, dict):
        raise ValueError(f"pass an object like {example}")
    return decoded


def decode_json_list(value: Any, *, example: str, item_example: str) -> Any:
    """``value`` with a JSON-text list, and JSON-text items of a list, decoded.

    Args:
        value: The raw argument.
        example: The list's shape, shown in a refusal.
        item_example: One item's shape, shown in a refusal.

    Returns:
        A list (its string items decoded to objects), ``None`` for a blank string, or
        ``value`` unchanged when it is neither a string nor a list.

    Raises:
        ValueError: The text is too long, is not valid JSON, or is not a list of objects.
    """
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if not text.startswith("["):
            raise ValueError(f"pass a list like {example}, not text")
        value = _loads(text, shape="a list", example=example)
        if not isinstance(value, list):
            raise ValueError(f"pass a list like {example}")
    if isinstance(value, list):
        return [_object(item, example=item_example) for item in value]
    return value


def decode_json_object(value: Any, *, example: str) -> Any:
    """``value`` decoded when it is JSON text of an object (see :func:`decode_json_list`)."""
    return _object(value, example=example)


def json_list(item_example: str) -> BeforeValidator:
    """``Annotated`` metadata: a list of objects that may arrive as JSON text.

    Args:
        item_example: One item, as the model should write it (``{"key": "claim_no", …}``).
    """
    example = f"[{item_example}]"

    def _decode(value: Any) -> Any:
        return decode_json_list(value, example=example, item_example=item_example)

    return BeforeValidator(_decode)


def json_object(example: str) -> BeforeValidator:
    """``Annotated`` metadata: an object that may arrive as JSON text."""

    def _decode(value: Any) -> Any:
        return decode_json_object(value, example=example)

    return BeforeValidator(_decode)
