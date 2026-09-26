"""Tool templates (V5-25, D-V5-36): ready-made HTTP tools shipped with the api.

One directory per group under ``lkap_api/templates/tools/<group>/`` holding one
``NN-<name>.json`` per template (a :class:`~lkap_contracts.tools.ToolTemplate`;
the two-digit prefix is the order). The Cal.com set is the first group: six
booking tools keyed by a Cal.com API key kept in an ``http-tool-secret`` key
as ``CAL_API_KEY``, with the event type as an argument default.

The loader rules, checked at import (a malformed template is a startup error,
like the starter catalogue): the id is ``<group>.<definition name>``; every
``{{ secret.NAME }}`` in the url, headers and body is in ``secret_names``; the
url's host is in ``allowed_hosts`` and is ``https``; every ``defaults`` entry
names a parameter; every ``{{ arg }}`` placeholder is a parameter.

:func:`instantiate_definition` turns a template and an admin's ``defaults``
into the stored definition: each default becomes its parameter's JSON Schema
``default`` (coerced to the parameter's type) and leaves ``required``. The
worker fills an argument the model left out from that default.
"""

from __future__ import annotations

import importlib.resources
import json
import re
from functools import lru_cache
from importlib.resources.abc import Traversable
from typing import Any, Final
from urllib.parse import urlsplit

from lkap_contracts.tools import HttpToolDefinition, ToolTemplate
from pydantic import ValidationError

__all__ = [
    "GROUP_ORDER",
    "ToolTemplateError",
    "group_templates",
    "instantiate_definition",
    "load_tool_templates",
    "tool_template",
]

#: The package that holds the template groups.
TEMPLATES_PACKAGE: Final[str] = "lkap_api.templates.tools"
#: Groups in gallery order.
GROUP_ORDER: Final[tuple[str, ...]] = ("cal_com",)

_SECRET_RE = re.compile(r"{{\s*secret\.([A-Za-z_][A-Za-z0-9_]*)\s*}}")
_ARG_RE = re.compile(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*}}")
_FILE_RE = re.compile(r"^\d{2}-[a-z0-9_]+\.json$")


class ToolTemplateError(ValueError):
    """A template is malformed (at load), or an instantiation's defaults do not fit it."""


def _placeholders(definition: HttpToolDefinition) -> tuple[set[str], set[str]]:
    surfaces = [definition.url, *definition.headers.values(), definition.body_template or ""]
    secrets: set[str] = set()
    arguments: set[str] = set()
    for text in surfaces:
        secrets.update(_SECRET_RE.findall(text))
        arguments.update(name for name in _ARG_RE.findall(text) if name != "secret")
    return secrets, arguments


def _check(template: ToolTemplate, where: str) -> None:
    definition = template.definition
    group, _, name = template.id.partition(".")
    if group != template.group or name != definition.name:
        raise ToolTemplateError(f"{where}: id must be '<group>.<definition name>'")
    parsed = urlsplit(definition.url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in {h.lower() for h in definition.allowed_hosts}:
        raise ToolTemplateError(f"{where}: the url must be https on a host in allowed_hosts")
    secrets, arguments = _placeholders(definition)
    if secrets - set(template.secret_names):
        raise ToolTemplateError(
            f"{where}: secret(s) {sorted(secrets - set(template.secret_names))} not listed"
        )
    properties = definition.parameters.get("properties")
    names = set(properties) if isinstance(properties, dict) else set()
    if arguments - names:
        raise ToolTemplateError(f"{where}: placeholder(s) {sorted(arguments - names)} are not parameters")
    unknown = {d.name for d in template.defaults} - names
    if unknown:
        raise ToolTemplateError(f"{where}: default(s) {sorted(unknown)} are not parameters")
    if definition.credential_id is not None:
        raise ToolTemplateError(f"{where}: a template carries no credential_id")


def _load_group(directory: Traversable) -> list[ToolTemplate]:
    templates: list[ToolTemplate] = []
    for entry in sorted(directory.iterdir(), key=lambda item: item.name):
        if not entry.name.endswith(".json"):
            continue
        where = f"tool template '{directory.name}/{entry.name}'"
        if not _FILE_RE.match(entry.name):
            raise ToolTemplateError(f"{where}: file names are 'NN-<name>.json'")
        try:
            template = ToolTemplate.model_validate(json.loads(entry.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ToolTemplateError(f"{where}: {exc}") from exc
        _check(template, where)
        templates.append(template)
    return templates


@lru_cache(maxsize=1)
def load_tool_templates() -> tuple[ToolTemplate, ...]:
    """Every template, in :data:`GROUP_ORDER` then file order.

    Raises:
        ToolTemplateError: A template breaks one of the loader rules (module docstring).
    """
    root = importlib.resources.files(TEMPLATES_PACKAGE)
    templates: list[ToolTemplate] = []
    for group in GROUP_ORDER:
        templates.extend(_load_group(root.joinpath(group)))
    ids = [t.id for t in templates]
    if len(ids) != len(set(ids)):
        raise ToolTemplateError("tool template ids must be unique")
    return tuple(templates)


def tool_template(template_id: str) -> ToolTemplate | None:
    """The template with this id, or ``None``."""
    return next((t for t in load_tool_templates() if t.id == template_id), None)


def group_templates(group: str) -> list[ToolTemplate]:
    """Every template of ``group`` (empty for an unknown group)."""
    return [t for t in load_tool_templates() if t.group == group]


def _coerce(value: str | int | float | bool, schema: dict[str, Any], name: str) -> Any:
    """``value`` as the parameter's JSON type (a console posts strings), or an error."""
    kind = schema.get("type")
    match kind:
        case "integer":
            if isinstance(value, int) and not isinstance(value, bool):
                return value
            if isinstance(value, float) and value.is_integer():
                return int(value)
            if isinstance(value, str) and value.strip().isdigit():
                return int(value.strip())
        case "number":
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return value
        case "boolean":
            if isinstance(value, bool):
                return value
        case "string":
            if not isinstance(value, bool):
                return str(value)
        case _:
            return value
    raise ToolTemplateError(f"the default for '{name}' must be of type {kind}")


def instantiate_definition(
    template: ToolTemplate, defaults: dict[str, str | int | float | bool], *, credential_id: str
) -> HttpToolDefinition:
    """The HTTP tool a template becomes with an admin's ``defaults`` and key.

    Only the defaults the template declares are applied (the caller checks the others);
    a declared ``required`` default without a value is an error.

    Raises:
        ToolTemplateError: A required default is missing or a value has the wrong type.
    """
    parameters: dict[str, Any] = json.loads(json.dumps(template.definition.parameters))
    properties: dict[str, Any] = parameters.setdefault("properties", {})
    required = [name for name in parameters.get("required", []) if isinstance(name, str)]
    for spec in template.defaults:
        if spec.name not in defaults:
            if spec.required:
                raise ToolTemplateError(f"'{template.id}' needs a value for '{spec.name}' ({spec.label})")
            continue
        schema = properties.setdefault(spec.name, {})
        schema["default"] = _coerce(defaults[spec.name], schema, spec.name)
        required = [name for name in required if name != spec.name]
    if "required" in parameters:
        parameters["required"] = required
    return template.definition.model_copy(update={"parameters": parameters, "credential_id": credential_id})
