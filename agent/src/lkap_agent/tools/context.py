"""The session context a declarative tool sees at call time (V6-07, D-V6-22).

One :class:`ToolCallContext` per session, built next to the ``SessionContext`` in
``main.py`` and handed to the HTTP, app-action and MCP builders. It reads every value
**lazily**, at the moment a tool is called: the caller's participant may join after the
tools are built, the caller's time zone is resolved on the first turn, a flow's variables
change from node to node, and a language switch happens mid-call. Nothing is cached, so
nothing needs refreshing.

Rendering (:func:`render_template`) is one pass over the template with
:data:`lkap_contracts.tool_context.PLACEHOLDER_PATTERN`: a value is escaped for where it
lands and never scanned again, so an argument whose text is ``{{ ctx.caller_phone }}``
stays that text. A ``ctx``/``var`` placeholder without a value is collected and the tool
refuses with "I need … first" (:func:`missing_message`) before any request is sent.

Privacy: ``caller_phone`` and ``caller_identity`` are never logged (only placeholder
*names* are), and nothing here writes a session event.

The variable store: a flow session's variables live on ``FlowState.variables``
(``userdata["flow"]``); any other session keeps them in ``userdata[VARIABLES_USERDATA_KEY]``,
seeded from the session's seed variables (an outbound call's). Names a binding wrote are
listed in ``userdata[BOUND_VARIABLES_USERDATA_KEY]`` (their values came from a third party).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping, MutableMapping
from typing import Any, Final

from livekit.agents import ToolError
from lkap_contracts.tool_context import (
    CONFIRMED_PARAMETER,
    CTX_LABELS,
    PLACEHOLDER_PATTERN,
    PlaceholderRef,
    context_placeholders,
    tool_channel,
    variable_label,
)

from lkap_agent.logging import get_logger

__all__ = [
    "BOUND_VARIABLES_USERDATA_KEY",
    "SIP_PHONE_ATTRIBUTE",
    "TOOL_CONTEXT_USERDATA_KEY",
    "VARIABLES_USERDATA_KEY",
    "ToolCallContext",
    "check_readback",
    "check_requires",
    "format_value",
    "hide_pinned",
    "missing_message",
    "missing_refs",
    "pin_arguments",
    "readback_parameters",
    "render_template",
    "session_variables",
    "uses_tool_context",
]

_log = get_logger(__name__)

#: ``SessionContext.userdata`` key of a non-flow session's variables (``{name: value}``).
VARIABLES_USERDATA_KEY: Final[str] = "lkap.variables"
#: ``SessionContext.userdata`` key of the set of variable names a result binding wrote.
BOUND_VARIABLES_USERDATA_KEY: Final[str] = "lkap.bound_variables"
#: ``SessionContext.userdata`` key of the session's :class:`ToolCallContext`.
TOOL_CONTEXT_USERDATA_KEY: Final[str] = "lkap.tool_context"
#: The participant attribute LiveKit SIP sets to the caller's number (E.164).
SIP_PHONE_ATTRIBUTE: Final[str] = "sip.phoneNumber"
_SIP_CHANNELS: Final[frozenset[str]] = frozenset({"sip_in", "sip_out"})

_PLACEHOLDER_RE: Final = re.compile(PLACEHOLDER_PATTERN)


def format_value(value: Any) -> str | None:
    """A context or variable value as template text; ``None`` when it counts as not set.

    ``None`` and blank text are not set; ``True``/``False`` read ``true``/``false``; a list
    or object is compact JSON; anything else is ``str(value)``.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value if value.strip() else None
    if isinstance(value, list | dict):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def session_variables(session: Any) -> MutableMapping[str, Any]:
    """The session's variable store: the flow's ``FlowState.variables`` or the plain store.

    Creates the plain store on first use when the session is not a flow.
    """
    userdata = getattr(session, "userdata", None)
    if not isinstance(userdata, dict):
        return {}
    flow = userdata.get("flow")
    variables = getattr(flow, "variables", None)
    if isinstance(variables, dict):
        return variables
    store = userdata.get(VARIABLES_USERDATA_KEY)
    if not isinstance(store, dict):
        store = {}
        userdata[VARIABLES_USERDATA_KEY] = store
    return store


class ToolCallContext:
    """What ``{{ ctx.* }}`` and ``{{ var.* }}`` read, and where bindings write, for one session."""

    def __init__(self, session: Any, *, participant_identity: str | None = None) -> None:
        """Wrap the session.

        Args:
            session: The worker's ``SessionContext`` (``session_id``, ``agent_id``,
                ``channel``, ``config``, ``room``, ``ui``, ``userdata``).
            participant_identity: The identity the job was dispatched for, used for
                ``ctx.caller_identity`` until the caller's participant is in the room.
        """
        self.session = session
        self._participant_identity = participant_identity or None

    @classmethod
    def for_session(
        cls, session: Any, *, participant_identity: str | None = None, seed: Mapping[str, Any] | None = None
    ) -> ToolCallContext:
        """Build the session's context, seed the plain variable store and remember it in ``userdata``."""
        context = cls(session, participant_identity=participant_identity)
        userdata = getattr(session, "userdata", None)
        if isinstance(userdata, dict):
            store = userdata.setdefault(VARIABLES_USERDATA_KEY, {})
            if isinstance(store, dict):
                for name, value in (seed or {}).items():
                    if value is not None:
                        store.setdefault(name, value)
            userdata[TOOL_CONTEXT_USERDATA_KEY] = context
        return context

    # ------------------------------------------------------------------ session facts

    @property
    def ui(self) -> Any:
        """The session's UI channel."""
        return getattr(self.session, "ui", None)

    @property
    def channel(self) -> str:
        """The session channel (``web``, ``sip_in`` …)."""
        return str(getattr(self.session, "channel", "web") or "web")

    def _caller(self) -> Any:
        try:
            from lkap_agent.locale import caller_participant  # noqa: PLC0415 - light, first call only

            return caller_participant(getattr(self.session, "room", None))
        except Exception:
            _log.debug("tool_context.caller_participant_failed", exc_info=True)
            return None

    def _timezone(self) -> str | None:
        from lkap_agent.locale import fallback_locale, session_locale  # noqa: PLC0415

        locale = session_locale(self.session)
        if locale is not None:
            return locale.caller_timezone
        config = getattr(self.session, "config", None)
        if config is None:
            return None
        try:
            return fallback_locale(config).caller_timezone
        except Exception:
            _log.debug("tool_context.timezone_fallback_failed", exc_info=True)
            return None

    def _language(self) -> str | None:
        from lkap_agent.languages import session_languages  # noqa: PLC0415

        state = session_languages(self.session)
        if state is not None and state.current:
            return str(state.current)
        voice = getattr(getattr(self.session, "config", None), "voice", None)
        language = getattr(voice, "language", None)
        return str(language) if language else None

    def ctx_value(self, name: str) -> str | None:
        """The value of ``{{ ctx.<name> }}`` now, or ``None`` when the session has none."""
        match name:
            case "session_id":
                return format_value(getattr(self.session, "session_id", None))
            case "agent_id":
                return format_value(getattr(self.session, "agent_id", None))
            case "channel":
                return tool_channel(self.channel)
            case "timezone":
                return format_value(self._timezone())
            case "language":
                return format_value(self._language())
            case "caller_identity":
                caller = self._caller()
                identity = getattr(caller, "identity", None) if caller is not None else None
                return format_value(identity or self._participant_identity)
            case "caller_phone":
                if self.channel not in _SIP_CHANNELS:
                    return None
                caller = self._caller()
                attributes = getattr(caller, "attributes", None) or {}
                return format_value(attributes.get(SIP_PHONE_ATTRIBUTE)) if caller is not None else None
            case _:
                return None

    # ------------------------------------------------------------------ variables

    def variables(self) -> MutableMapping[str, Any]:
        """The session's variable store (see :func:`session_variables`)."""
        return session_variables(self.session)

    def var_value(self, name: str) -> str | None:
        """The value of ``{{ var.<name> }}`` now, or ``None`` when not set."""
        return format_value(self.variables().get(name))

    def set_variable(self, name: str, value: Any, *, bound: bool = False) -> None:
        """Write a variable; ``bound`` marks it as coming from a tool result (a third party)."""
        self.variables()[name] = value
        if bound:
            userdata = getattr(self.session, "userdata", None)
            if isinstance(userdata, dict):
                names = userdata.setdefault(BOUND_VARIABLES_USERDATA_KEY, set())
                if isinstance(names, set):
                    names.add(name)

    def value(self, ref: PlaceholderRef) -> str | None:
        """The current value of one ``ctx``/``var`` placeholder."""
        return self.ctx_value(ref.name) if ref.namespace == "ctx" else self.var_value(ref.name)

    def label(self, ref: PlaceholderRef) -> str:
        """How the tool names a missing value when it asks for it."""
        if ref.namespace == "ctx":
            return CTX_LABELS.get(ref.name, ref.name)
        return variable_label(ref.name)


def uses_tool_context(definition: Any) -> bool:
    """Whether a tool definition uses any V6-07 feature (placeholders, bindings, read-back …).

    ``main.py`` hands the builders a :class:`ToolCallContext` only when one does, so an agent
    without them is built exactly as before V6-07.
    """
    kind = getattr(definition, "kind", None)
    if kind == "mcp":
        return bool(getattr(definition, "tool_context", None))
    if kind not in ("http", "provider"):
        return False
    if any(getattr(definition, name, None) for name in ("bindings", "requires_vars", "confirm_readback")):
        return True
    if kind == "provider":
        return bool(getattr(definition, "pinned_arguments", None))
    return bool(
        context_placeholders(getattr(definition, "url", None))
        or context_placeholders(getattr(definition, "body_template", None))
    )


# ---------------------------------------------------------------------- rendering


def render_template(
    template: str,
    arguments: Mapping[str, Any],
    context: ToolCallContext | None,
    *,
    escape: Callable[[str], str],
    missing: list[PlaceholderRef],
) -> str:
    """Substitute ``{{ arg }}``, ``{{ ctx.* }}`` and ``{{ var.* }}`` in one pass.

    Args:
        template: The url or body template.
        arguments: The call's arguments (schema defaults already filled).
        context: The session's context; ``None`` means no session values at all.
        escape: Applied to every substituted value (percent-encoding, JSON escaping).
        missing: Collects each ``ctx``/``var`` placeholder that has no value (rendered empty).

    Returns:
        The rendered text. An unknown argument (and a ``{{ secret.… }}``) is left as-is, as
        before V6-07, so ``_reject_unresolved`` still refuses it.
    """

    def _sub(match: re.Match[str]) -> str:
        namespace, name = match.group(1), match.group(2)
        if namespace is None:
            if name not in arguments:
                return match.group(0)
            return escape(str(arguments[name]))
        ref = PlaceholderRef("ctx" if namespace == "ctx" else "var", name)
        value = context.value(ref) if context is not None else None
        if value is None:
            if ref not in missing:
                missing.append(ref)
            return ""
        return escape(value)

    return _PLACEHOLDER_RE.sub(_sub, template)


def missing_refs(texts: Iterable[str | None], context: ToolCallContext | None) -> list[PlaceholderRef]:
    """The ``ctx``/``var`` placeholders named in ``texts`` that have no value now (in order)."""
    missing: list[PlaceholderRef] = []
    for text in texts:
        for ref in context_placeholders(text):
            if (context is None or context.value(ref) is None) and ref not in missing:
                missing.append(ref)
    return missing


def _join(labels: list[str]) -> str:
    if len(labels) <= 1:
        return "".join(labels)
    return f"{', '.join(labels[:-1])} and {labels[-1]}"


def missing_message(refs: Iterable[PlaceholderRef], context: ToolCallContext | None) -> str:
    """The refusal the model reads when values are missing ("I need … first")."""
    labels: list[str] = []
    for ref in refs:
        label = context.label(ref) if context is not None else variable_label(ref.name)
        if label not in labels:
            labels.append(label)
    return (
        f"I need {_join(labels)} first. Ask the caller for what is missing (or wait until it is known), "
        "then try again. Nothing was sent."
    )


def check_requires(requires_vars: Iterable[str], context: ToolCallContext | None, *, tool: str) -> None:
    """Refuse the call when a ``requires_vars`` variable is not set.

    Raises:
        ToolError: Listing the missing variables so the model asks for them.
    """
    refs = [PlaceholderRef("var", name) for name in requires_vars]
    missing = [ref for ref in refs if context is None or context.value(ref) is None]
    if missing:
        _log.debug("tool_context.requires_vars_missing", tool=tool, missing=[ref.name for ref in missing])
        raise ToolError(missing_message(missing, context))


# ---------------------------------------------------------------------- read-back


def readback_parameters(parameters: dict[str, Any], names: list[str]) -> dict[str, Any]:
    """The schema the model sees for a read-back tool: ``parameters`` plus ``confirmed``.

    The stored schema is never changed (a copy is returned).
    """
    if not names:
        return parameters
    properties = dict(parameters.get("properties") or {})
    properties[CONFIRMED_PARAMETER] = {
        "type": "boolean",
        "description": (
            f"Set true only after you have read {', '.join(names)} back to the caller and they said it "
            "is right. Leave it false the first time; the tool then tells you what to read back."
        ),
    }
    return {**parameters, "properties": properties}


def check_readback(arguments: dict[str, Any], names: list[str], *, tool: str) -> dict[str, Any]:
    """Refuse until the model confirms a read-back; return the arguments without ``confirmed``.

    Args:
        arguments: The call's arguments (may carry ``confirmed``).
        names: ``confirm_readback``: the arguments to read back.
        tool: The tool name (for the message).

    Raises:
        ToolError: ``confirmed`` is not true: the message says what to read back, spelled out
            where it helps a voice (``spell_back``'s wording).
    """
    if not names:
        return arguments
    confirmed = arguments.pop(CONFIRMED_PARAMETER, False)
    if confirmed is True or (isinstance(confirmed, str) and confirmed.strip().lower() == "true"):
        return arguments
    from lkap_agent.tools.builtin.spell_back import spell  # noqa: PLC0415 - avoids an import cycle

    parts: list[str] = []
    for name in names:
        value = arguments.get(name)
        text = format_value(value)
        if text is None:
            parts.append(f"{variable_label(name)}: (not given yet)")
            continue
        try:
            spoken = spell(text, "auto")[1]
        except ValueError:
            spoken = text
        said = f" (say it as: {spoken})" if spoken != text else ""
        parts.append(f"{variable_label(name)}: {text}{said}")
    _log.debug("tool_context.readback_requested", tool=tool, names=list(names))
    raise ToolError(
        "Before this runs, read these back to the caller and ask them to confirm: "
        + "; ".join(parts)
        + f". If they confirm, call {tool} again with the same values and {CONFIRMED_PARAMETER}=true. "
        "Nothing was sent."
    )


# ---------------------------------------------------------------------- pinned arguments


def hide_pinned(parameters: dict[str, Any], pinned: Mapping[str, Any]) -> dict[str, Any]:
    """The schema without the pinned arguments (the model never supplies them)."""
    if not pinned:
        return parameters
    properties = {k: v for k, v in dict(parameters.get("properties") or {}).items() if k not in pinned}
    required = [name for name in parameters.get("required") or [] if name not in pinned]
    schema = {**parameters, "properties": properties}
    if "required" in parameters:
        schema["required"] = required
    return schema


def pin_arguments(
    arguments: dict[str, Any], pinned: Mapping[str, Any], context: ToolCallContext | None, *, tool: str
) -> dict[str, Any]:
    """Apply the admin's pinned arguments over the model's (string values rendered).

    Raises:
        ToolError: A pinned value names a session value or variable that is not set.
    """
    if not pinned:
        return arguments
    missing: list[PlaceholderRef] = []
    result = dict(arguments)
    for name, value in pinned.items():
        if isinstance(value, str):
            result[name] = render_template(value, {}, context, escape=lambda v: v, missing=missing)
        else:
            result[name] = value
    if missing:
        _log.debug(
            "tool_context.pinned_missing", tool=tool, missing=[f"{r.namespace}.{r.name}" for r in missing]
        )
        raise ToolError(missing_message(missing, context))
    return result
