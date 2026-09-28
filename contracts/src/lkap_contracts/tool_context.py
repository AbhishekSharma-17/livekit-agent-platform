"""Tool context: placeholders, result bindings, required variables and read-back (V6-07).

D-V6-22 and D-V6-23 of ``docs/v6/PLAN-V6.md``; ``docs/CONTRACTS.md`` §7.

**Placeholders.** Besides a tool call's own ``{{ arg }}`` values and the api's
``{{ secret.NAME }}``, a declarative tool may name

* ``{{ ctx.<name> }}`` — a fact about the session (:data:`CTX_PLACEHOLDERS`), and
* ``{{ var.<name> }}`` — a session variable (a flow variable, an extraction variable, or
  one a binding wrote).

The worker substitutes them **at call time**, in the same single pass as the arguments
(a value is never scanned again, so an argument that contains ``{{ ctx.… }}`` stays text),
escaped for where it lands: percent-encoded in a URL's path and query, JSON-escaped in a
body template, as-is in a pinned argument of an app action or MCP tool (which is sent as a
JSON value). They are **never** allowed in headers (those carry secrets) and never in a
URL's scheme or authority, so a placeholder cannot move the request to another host
(:func:`placeholder_issues`; the definition models refuse them at save and the worker
refuses them again when it builds the tool). A value the session does not have makes the
tool answer "I need … first" without calling out.

**Bindings.** ``bindings: [{path, to}]`` copy parts of a successful result onto the panel
or into a variable without a model turn. ``path`` is a JSON pointer into the tool's
result — the parsed value the model would see, i.e. after ``result_path`` — and ``to``
is one of :data:`BINDING_TARGET_FORMS`. They are bounded (:data:`MAX_BINDINGS`,
:data:`MAX_BINDING_VALUE_CHARS`, :data:`MAX_BINDING_TABLE_ROWS`) and never write a block
outside ``details`` and ``table`` (so never a requestable, link, consent, upload, captions
or handoff block). A bound value is display data: the model reads it later only through
``describe_panel``, fenced.

**requires_vars / confirm_readback.** ``requires_vars`` names variables that must be set
before the tool runs (the refusal lists what is missing, so the model asks); a
``confirm_readback`` list of argument names adds a ``confirmed`` parameter to the tool, and
the tool refuses until the model has read those values back to the caller and calls again
with ``confirmed=true``.
"""

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Final, Literal, get_args

from pydantic import AfterValidator, BaseModel, Field, field_validator

from lkap_contracts.common import VARIABLE_NAME_PATTERN, SessionChannel

__all__ = [
    "BINDING_BLOCK_TYPES",
    "BINDING_TARGET_FORMS",
    "CONFIRMED_PARAMETER",
    "CTX_LABELS",
    "CTX_PLACEHOLDERS",
    "FORBIDDEN_BINDING_BLOCK_TYPES",
    "MAX_BINDINGS",
    "MAX_BINDING_TABLE_ROWS",
    "MAX_BINDING_VALUE_CHARS",
    "MAX_CONFIRM_READBACK",
    "MAX_PINNED_ARGUMENTS",
    "MAX_REQUIRES_VARS",
    "PLACEHOLDER_PATTERN",
    "SENSITIVE_CTX_PLACEHOLDERS",
    "TOOL_CHANNEL_OF",
    "TOOL_CONTEXT_MODELS",
    "BindingTarget",
    "Bindings",
    "ConfirmReadback",
    "PinnedValue",
    "PlaceholderIssue",
    "PlaceholderRef",
    "RequiresVars",
    "ToolBinding",
    "ToolChannel",
    "ToolContextPlaceholder",
    "ToolContextSpec",
    "context_placeholders",
    "parse_binding_target",
    "placeholder_issues",
    "tool_channel",
    "url_authority",
    "variable_label",
]

#: Every ``{{ ctx.<name> }}`` a tool may use, in the order the console lists them.
ToolContextPlaceholder = Literal[
    "session_id",
    "agent_id",
    "caller_phone",
    "caller_identity",
    "language",
    "timezone",
    "channel",
]
CTX_PLACEHOLDERS: Final[tuple[str, ...]] = get_args(ToolContextPlaceholder)

#: What a missing ``ctx`` value is called when the tool asks for it ("I need … first").
CTX_LABELS: Final[dict[str, str]] = {
    "session_id": "the session reference",
    "agent_id": "the agent reference",
    "caller_phone": "the caller's phone number",
    "caller_identity": "the caller's identity",
    "language": "the conversation's language",
    "timezone": "the caller's time zone",
    "channel": "how the caller reached us",
}

#: ``ctx`` values that identify the caller: never written to a log line or a session event.
SENSITIVE_CTX_PLACEHOLDERS: Final[frozenset[str]] = frozenset({"caller_phone", "caller_identity"})

#: ``{{ ctx.channel }}``: the three words a tool sees.
ToolChannel = Literal["web", "phone", "text"]

#: How each session channel reads in ``{{ ctx.channel }}``: a phone leg is ``phone``, a text
#: chat is ``text``, everything else (the web page, the embeddable widget, the console's test
#: call, a session the api started) is ``web``.
TOOL_CHANNEL_OF: Final[dict[str, ToolChannel]] = {
    "web": "web",
    "widget": "web",
    "test": "web",
    "api": "web",
    "text": "text",
    "sip_in": "phone",
    "sip_out": "phone",
}


def tool_channel(channel: SessionChannel | str) -> ToolChannel:
    """``{{ ctx.channel }}`` for a session channel (:data:`TOOL_CHANNEL_OF`; unknown → ``web``)."""
    return TOOL_CHANNEL_OF.get(str(channel), "web")


#: One placeholder of any kind: ``{{ arg }}``, ``{{ ctx.name }}`` or ``{{ var.name }}``.
#: Group 1 is the namespace (``ctx``, ``var`` or ``None`` for an argument), group 2 the name.
#: ``{{ secret.NAME }}`` does not match (the api has substituted it before the worker runs).
PLACEHOLDER_PATTERN: Final[str] = r"{{\s*(?:(ctx|var)\.)?([a-zA-Z_][a-zA-Z0-9_]*)\s*}}"
_PLACEHOLDER_RE: Final = re.compile(PLACEHOLDER_PATTERN)
#: Any ``{{ ctx.… }}`` / ``{{ var.… }}`` spelling, even a malformed one (``{{ ctx.a-b }}``,
#: ``{{ var. }}``), for the "never here" checks.
_ANY_CONTEXT_RE: Final = re.compile(r"{{\s*(?:ctx|var)\s*\.")
_VARIABLE_NAME_RE: Final = re.compile(VARIABLE_NAME_PATTERN)
_TOOL_ARGUMENT_RE: Final = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")

#: The parameter ``confirm_readback`` adds to a tool's schema (and strips before the call).
CONFIRMED_PARAMETER: Final[str] = "confirmed"

#: Caps (D-V6-23 and the card): bindings per tool, characters per bound value, rows per table.
MAX_BINDINGS: Final[int] = 20
MAX_BINDING_VALUE_CHARS: Final[int] = 500
MAX_BINDING_TABLE_ROWS: Final[int] = 100
MAX_REQUIRES_VARS: Final[int] = 20
MAX_CONFIRM_READBACK: Final[int] = 10
MAX_PINNED_ARGUMENTS: Final[int] = 20
_MAX_BINDING_PATH_CHARS: Final[int] = 256

#: The block types a binding may write (``details:`` and ``table:`` targets).
BINDING_BLOCK_TYPES: Final[frozenset[str]] = frozenset({"details", "table"})
#: Blocks a binding never writes (the ``update_block`` rule): a caller's request, a link, a
#: consent, an upload, the captions and the handoff. Listed for the messages; only
#: :data:`BINDING_BLOCK_TYPES` are ever written.
FORBIDDEN_BINDING_BLOCK_TYPES: Final[frozenset[str]] = frozenset(
    {"form", "choices", "slots", "consent", "upload", "link", "captions", "handoff"}
)

#: The spellings of ``ToolBinding.to``.
BINDING_TARGET_FORMS: Final[tuple[str, ...]] = (
    "details:<block_id>.<key>",
    "table:<block_id>",
    "checklist:<item_id>",
    "status",
    "note",
    "var:<name>",
)

_BLOCK_ID = r"[A-Za-z0-9_-]{1,64}"
_TARGET_RES: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("details", re.compile(rf"^details:({_BLOCK_ID})\.([A-Za-z0-9_-]{{1,64}})$")),
    ("table", re.compile(rf"^table:({_BLOCK_ID})$")),
    ("checklist", re.compile(r"^checklist:([A-Za-z0-9_.:-]{1,64})$")),
    ("status", re.compile(r"^status$")),
    ("note", re.compile(r"^note$")),
    ("var", re.compile(r"^var:([a-z][a-z0-9_]{0,63})$")),
)

BindingKind = Literal["details", "table", "checklist", "status", "note", "var"]


@dataclass(frozen=True, slots=True)
class BindingTarget:
    """A parsed ``ToolBinding.to``."""

    kind: BindingKind
    block_id: str | None = None
    """``details`` and ``table`` targets."""
    key: str | None = None
    """``details``: the row key; ``checklist``: the item id; ``var``: the variable name."""


def parse_binding_target(to: str) -> BindingTarget:
    """Parse one binding target.

    Raises:
        ValueError: ``to`` is none of :data:`BINDING_TARGET_FORMS`.
    """
    text = to.strip()
    for kind, pattern in _TARGET_RES:
        match = pattern.match(text)
        if match is None:
            continue
        if kind == "details":
            return BindingTarget("details", block_id=match.group(1), key=match.group(2))
        if kind == "table":
            return BindingTarget("table", block_id=match.group(1))
        if kind in ("checklist", "var"):
            return BindingTarget(kind, key=match.group(1))  # type: ignore[arg-type]
        return BindingTarget(kind)  # type: ignore[arg-type]
    raise ValueError(f"binding target '{to}' must be one of: {', '.join(BINDING_TARGET_FORMS)}")


class ToolBinding(BaseModel):
    """Copy one part of a successful tool result onto the panel or into a variable (D-V6-23).

    Applied by the worker after a successful call (an HTTP 2xx, an app action that
    succeeded, an MCP result that is not an error) and before the model's next turn, with
    no model involved. ``path`` points into the tool's result as the model would see it
    (after ``result_path``); ``""`` is the whole result.
    """

    path: str = Field(default="", max_length=_MAX_BINDING_PATH_CHARS)
    """An RFC 6901 JSON pointer into the result (``/policy/holder``), or ``""`` for all of it."""
    to: str = Field(min_length=1, max_length=140)
    """Where the value goes: ``details:<block_id>.<key>``, ``table:<block_id>`` (a list of
    objects replaces the rows), ``checklist:<item_id>`` (a true value ticks it),
    ``status``, ``note`` or ``var:<name>``."""

    @field_validator("path")
    @classmethod
    def _pointer(cls, value: str) -> str:
        if value and not value.startswith("/"):
            raise ValueError("a binding path is a JSON pointer: empty, or starting with '/'")
        return value

    @field_validator("to")
    @classmethod
    def _target(cls, value: str) -> str:
        parse_binding_target(value)
        return value.strip()

    def target(self) -> BindingTarget:
        """The parsed :attr:`to`."""
        return parse_binding_target(self.to)


def _at_most(limit: int, what: str) -> Callable[[list[Any]], list[Any]]:
    """A list cap enforced by a validator, not ``max_length``: the TypeScript generator would
    turn ``maxItems`` into a union of every tuple length, which no editor can assign to."""

    def _check(value: list[Any]) -> list[Any]:
        if len(value) > limit:
            raise ValueError(f"at most {limit} {what}")
        return value

    return _check


#: ``requires_vars`` (at most :data:`MAX_REQUIRES_VARS` names).
RequiresVars = Annotated[list[str], AfterValidator(_at_most(MAX_REQUIRES_VARS, "required variables"))]
#: ``confirm_readback`` (at most :data:`MAX_CONFIRM_READBACK` argument names).
ConfirmReadback = Annotated[
    list[str], AfterValidator(_at_most(MAX_CONFIRM_READBACK, "arguments to read back"))
]


#: ``bindings`` (at most :data:`MAX_BINDINGS`).
Bindings = Annotated[list[ToolBinding], AfterValidator(_at_most(MAX_BINDINGS, "bindings per tool"))]


#: A pinned argument's value: a template string (``{{ ctx.* }}``/``{{ var.* }}`` allowed) or a
#: plain JSON scalar.
PinnedValue = str | int | float | bool | None


class ToolContextSpec(BaseModel):
    """The tool-context settings of one MCP tool (``McpServerDefinition.tool_context``)."""

    requires_vars: RequiresVars = []
    """Variables that must be set before the tool runs."""
    confirm_readback: ConfirmReadback = []
    """Arguments the model reads back to the caller before the call (adds ``confirmed``)."""
    bindings: Bindings = []
    """Where parts of a successful result go on the panel or in variables."""
    pinned_arguments: dict[str, PinnedValue] = Field(default={}, max_length=MAX_PINNED_ARGUMENTS)
    """Arguments fixed by the admin (hidden from the model; string values may use
    ``{{ ctx.* }}`` and ``{{ var.* }}``)."""


# ---------------------------------------------------------------------- placeholder checks


@dataclass(frozen=True, slots=True)
class PlaceholderRef:
    """One ``{{ ctx.name }}`` or ``{{ var.name }}`` a template names."""

    namespace: Literal["ctx", "var"]
    name: str


def context_placeholders(text: str | None) -> list[PlaceholderRef]:
    """The ``ctx``/``var`` placeholders ``text`` names, in order (arguments are left out)."""
    if not text:
        return []
    refs: list[PlaceholderRef] = []
    for match in _PLACEHOLDER_RE.finditer(text):
        namespace = match.group(1)
        if namespace == "ctx":
            refs.append(PlaceholderRef("ctx", match.group(2)))
        elif namespace == "var":
            refs.append(PlaceholderRef("var", match.group(2)))
    return refs


def variable_label(name: str) -> str:
    """How a variable is named when the tool asks for it (``policy_number`` → ``policy number``)."""
    return name.replace("_", " ").strip() or name


def url_authority(url: str) -> tuple[str, str]:
    """``(scheme, authority)`` of a url template: the text before ``://`` and up to the first ``/?#``.

    A url with no ``://`` has no scheme, and everything before its first ``/``, ``?`` or ``#``
    counts as the authority (a placeholder there could still become the host).
    """
    scheme, sep, rest = url.partition("://")
    authority = rest if sep else scheme
    for stop in "/?#":
        authority = authority.split(stop, 1)[0]
    return (scheme if sep else "", authority)


@dataclass(frozen=True, slots=True)
class PlaceholderIssue:
    """One problem :func:`placeholder_issues` found: the field it is in and a plain sentence."""

    field: str
    message: str


def _names_issues(field: str, text: str | None) -> list[PlaceholderIssue]:
    issues: list[PlaceholderIssue] = []
    if not text:
        return issues
    refs = context_placeholders(text)
    if len(refs) < len(_ANY_CONTEXT_RE.findall(text)):
        issues.append(
            PlaceholderIssue(
                field, "write a value as {{ ctx.<name> }} or {{ var.<name> }} (letters, digits, _)"
            )
        )
    for namespace, name in sorted({(ref.namespace, ref.name) for ref in refs}):
        if namespace == "ctx" and name not in CTX_PLACEHOLDERS:
            issues.append(
                PlaceholderIssue(
                    field,
                    f"'{{{{ ctx.{name} }}}}' is not a session value; "
                    f"use one of: {', '.join(CTX_PLACEHOLDERS)}",
                )
            )
        elif namespace == "var" and _VARIABLE_NAME_RE.match(name) is None:
            issues.append(
                PlaceholderIssue(
                    field, f"'{{{{ var.{name} }}}}': a variable name is lower case letters, digits and _"
                )
            )
    return issues


def _never_here(field: str, text: str | None, where: str) -> list[PlaceholderIssue]:
    if text and _ANY_CONTEXT_RE.search(text):
        return [PlaceholderIssue(field, f"session values and variables may not be used in {where}")]
    return []


def _var_list_issues(field: str, names: Iterable[Any]) -> list[PlaceholderIssue]:
    issues: list[PlaceholderIssue] = []
    for index, name in enumerate(names):
        if not isinstance(name, str) or _VARIABLE_NAME_RE.match(name) is None:
            issues.append(
                PlaceholderIssue(
                    f"{field}[{index}]", f"'{name}' is not a variable name (lower case, digits, _)"
                )
            )
    return issues


def _schema_properties(parameters: Any) -> set[str] | None:
    """The top-level property names of a JSON schema, or ``None`` when it declares none."""
    if not isinstance(parameters, Mapping):
        return None
    properties = parameters.get("properties")
    return set(properties) if isinstance(properties, Mapping) else None


def _readback_issues(
    field: str, names: Iterable[Any], properties: set[str] | None, pinned: Iterable[str] = ()
) -> list[PlaceholderIssue]:
    issues: list[PlaceholderIssue] = []
    pinned_names = set(pinned)
    seen: set[str] = set()
    for index, name in enumerate(names):
        path = f"{field}[{index}]"
        if not isinstance(name, str) or _TOOL_ARGUMENT_RE.match(name) is None:
            issues.append(PlaceholderIssue(path, f"'{name}' is not an argument name"))
            continue
        if name == CONFIRMED_PARAMETER:
            issues.append(PlaceholderIssue(path, f"'{CONFIRMED_PARAMETER}' is added by the read-back itself"))
        elif name in seen:
            issues.append(PlaceholderIssue(path, f"'{name}' is listed twice"))
        elif name in pinned_names:
            issues.append(PlaceholderIssue(path, f"'{name}' is pinned, so the model never says it"))
        elif properties is not None and name not in properties:
            issues.append(PlaceholderIssue(path, f"'{name}' is not one of the tool's arguments"))
        seen.add(name)
    if names and properties is not None and CONFIRMED_PARAMETER in properties:
        issues.append(
            PlaceholderIssue(
                field,
                f"the tool already has an argument named '{CONFIRMED_PARAMETER}'; read-back needs that name",
            )
        )
    return issues


def _bindings_issues(field: str, bindings: Any) -> list[PlaceholderIssue]:
    if not isinstance(bindings, list | tuple):
        return []
    if len(bindings) > MAX_BINDINGS:
        return [PlaceholderIssue(field, f"at most {MAX_BINDINGS} bindings per tool")]
    issues: list[PlaceholderIssue] = []
    for index, binding in enumerate(bindings):
        to = binding.get("to") if isinstance(binding, Mapping) else getattr(binding, "to", None)
        try:
            parse_binding_target(str(to or ""))
        except ValueError as exc:
            issues.append(PlaceholderIssue(f"{field}[{index}].to", str(exc)))
    return issues


def _pinned_issues(field: str, pinned: Any) -> list[PlaceholderIssue]:
    issues: list[PlaceholderIssue] = []
    if not isinstance(pinned, Mapping):
        return issues
    for name, value in pinned.items():
        if not isinstance(name, str) or _TOOL_ARGUMENT_RE.match(name) is None:
            issues.append(PlaceholderIssue(f"{field}.{name}", f"'{name}' is not an argument name"))
        elif isinstance(value, str):
            issues.extend(_names_issues(f"{field}.{name}", value))
    return issues


def _get(definition: Any, name: str, default: Any = None) -> Any:
    if isinstance(definition, Mapping):
        return definition.get(name, default)
    return getattr(definition, name, default)


def _spec_issues(base: str, spec: Any, properties: set[str] | None) -> list[PlaceholderIssue]:
    """The shared checks of ``requires_vars``, ``confirm_readback``, ``bindings``, ``pinned_arguments``."""
    pinned = _get(spec, "pinned_arguments") or {}
    return [
        *_var_list_issues(f"{base}requires_vars", _get(spec, "requires_vars") or []),
        *_readback_issues(
            f"{base}confirm_readback",
            _get(spec, "confirm_readback") or [],
            properties,
            pinned if isinstance(pinned, Mapping) else (),
        ),
        *_bindings_issues(f"{base}bindings", _get(spec, "bindings") or []),
        *_pinned_issues(f"{base}pinned_arguments", pinned),
    ]


def placeholder_issues(definition: Any) -> list[PlaceholderIssue]:
    """Where a tool definition uses session values or variables where it may not (D-V6-22).

    Accepts a definition model or its JSON (``kind`` ``http``, ``provider``, ``mcp`` or
    ``dataset``; any other kind has nothing to check). Refused:

    * a ``{{ ctx.* }}``/``{{ var.* }}`` in a URL's scheme or authority (host, port, login) —
      it could send the request somewhere the host checks never saw — and anywhere in an
      MCP server's url;
    * one in any header (headers carry secrets) or an MCP header-auth header;
    * an unknown ``ctx`` name, a malformed spelling, a variable name that is not one;
    * ``requires_vars`` entries that are not variable names; ``confirm_readback`` entries
      that are not the tool's arguments (when its schema lists them), repeated, pinned, or
      the reserved ``confirmed``;
    * a binding target none of :data:`BINDING_TARGET_FORMS`, or more than :data:`MAX_BINDINGS`.

    A definition that uses none of these features has no issues.

    Returns:
        The issues, ``field`` relative to the definition (``url``, ``headers.X``,
        ``bindings[0].to``, ``tool_context.lookup.requires_vars[1]`` …).
    """
    kind = _get(definition, "kind", "http")
    issues: list[PlaceholderIssue] = []
    headers = _get(definition, "headers") or {}
    if isinstance(headers, Mapping):
        for name, value in headers.items():
            issues.extend(_never_here(f"headers.{name}", str(value), "a header"))
    if kind == "http":
        url = str(_get(definition, "url") or "")
        scheme, authority = url_authority(url)
        if _ANY_CONTEXT_RE.search(scheme) or _ANY_CONTEXT_RE.search(authority):
            issues.append(
                PlaceholderIssue(
                    "url",
                    "session values and variables may not be placed in the url's scheme, host or port; "
                    "use the path or the query",
                )
            )
        issues.extend(_names_issues("url", url))
        issues.extend(_names_issues("body_template", _get(definition, "body_template")))
        issues.extend(_spec_issues("", definition, _schema_properties(_get(definition, "parameters"))))
    elif kind == "provider":
        issues.extend(_spec_issues("", definition, _schema_properties(_get(definition, "parameters"))))
    elif kind == "dataset":
        # V6-16 (ask #35): a lookup's arguments are its key columns; pinned values are rendered.
        keys = _get(definition, "key_columns") or []
        columns = {str(key) for key in keys} if isinstance(keys, list | tuple) else None
        issues.extend(_spec_issues("", definition, columns))
    elif kind == "mcp":
        issues.extend(_never_here("url", str(_get(definition, "url") or ""), "an MCP server's url"))
        auth = _get(definition, "auth")
        auth_headers = _get(auth, "headers") if auth is not None else None
        if isinstance(auth_headers, Mapping):
            for name, value in auth_headers.items():
                issues.extend(_never_here(f"auth.headers.{name}", str(value), "a header"))
        per_tool = _get(definition, "tool_context") or {}
        cached = {
            str(_get(tool, "name")): _schema_properties(_get(tool, "input_schema"))
            for tool in (_get(definition, "cached_tools") or [])
        }
        if isinstance(per_tool, Mapping):
            for tool_name, spec in per_tool.items():
                issues.extend(_spec_issues(f"tool_context.{tool_name}.", spec, cached.get(str(tool_name))))
    return issues


#: The tool-context models, registered in ``export.py`` with one line (``**TOOL_CONTEXT_MODELS``).
TOOL_CONTEXT_MODELS: dict[str, type[BaseModel]] = {
    "ToolBinding": ToolBinding,
    "ToolContextSpec": ToolContextSpec,
}
