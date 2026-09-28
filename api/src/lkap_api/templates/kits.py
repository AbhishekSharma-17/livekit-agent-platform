"""Tool kits (V6-18, D-V6-26): the catalogue, its placeholders and its load-time checks.

One file per kit under ``lkap_api/templates/kits/<id>.json`` (a
:class:`~lkap_contracts.kits.ToolKit` written with ``{{ kit.* }}`` placeholders), in
:data:`KIT_ORDER`. :func:`render_kit` fills the placeholders and validates the result:

* ``{{ kit.prefix }}`` — the ``block_prefix`` (names of tools, blocks, rules, variables);
* ``{{ kit.<setting> }}`` — the admin's value of a ``defaults`` entry (an address, a site);
* ``{{ kit.dataset_id }}`` / ``{{ kit.key_column }}`` — a ``dataset`` variant's table and its
  first key column;
* ``{{ kit.tool.<key> }}`` — the name a kit tool ends up with (a second pass, once the names
  are known: app actions are named by the app).

Anything else in ``{{ … }}`` (``secret.*``, ``ctx.*``, ``var.*``, a tool argument) is left for
the tool as usual. The loader rules, checked at import like the starter catalogue (R-V4-5)
by :func:`load_kits`: every kit renders for every variant with its preview values; every
snippet passes :func:`~lkap_contracts.kits.snippet_problems`; an HTTP tool is ``https`` and
names only the secrets its variant lists; what a kit names (tools, blocks, rules) starts with
its prefix, except the shared blocks and tools named by a template or an app.

:func:`resolve_settings` checks an admin's values: an address is ``https`` with a host and
no user, query or fragment; a site is a bare host name; nothing may hold ``{{`` or ``}}``, so
a value can never add a placeholder of its own.
"""

from __future__ import annotations

import copy
import importlib.resources
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Final
from urllib.parse import urlsplit

from lkap_contracts.kits import KitSetting, KitVariant, ToolKit, snippet_problems
from lkap_contracts.tools import HttpToolDefinition
from pydantic import ValidationError

from lkap_api.templates.tools import tool_template

__all__ = [
    "KIT_ORDER",
    "KitError",
    "KitTokens",
    "get_kit",
    "load_kits",
    "load_raw_kits",
    "raw_kit",
    "render_kit",
    "resolve_settings",
    "tool_names",
]

#: The package and directory that hold the kit files.
KITS_PACKAGE: Final[str] = "lkap_api.templates"
KITS_DIR: Final[str] = "kits"
#: Kits in gallery order (one ``<id>.json`` each).
KIT_ORDER: Final[tuple[str, ...]] = (
    "record_lookup",
    "case_ticket",
    "structured_intake",
    "verify_identity",
    "payment_esign_link",
    "notify_escalate",
    "sheet_crm_log",
    "booking",
)
#: Placeholder names the api fills itself (a setting may not use them).
RESERVED_TOKENS: Final[frozenset[str]] = frozenset({"prefix", "dataset_id", "key_column", "tool"})
#: What the preview shows for a lookup table (``GET /v1/tool-kits``).
# Shaped like a real dataset id (S6-15 pins `^[0-9a-f]{32}$`); stands in for the caller's table
# in the catalogue and in previews until a dataset is chosen.
PREVIEW_DATASET_ID: Final[str] = "0" * 32
PREVIEW_KEY_COLUMN: Final[str] = "reference"

_TOKEN_RE: Final = re.compile(r"{{\s*kit\.([a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)?)\s*}}")
_SECRET_RE: Final = re.compile(r"{{\s*secret\.([A-Za-z_][A-Za-z0-9_]*)\s*}}")
_HOST_RE: Final = re.compile(
    r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)
_MAX_URL: Final[int] = 500
_MAX_TEXT: Final[int] = 200


class KitError(ValueError):
    """A kit file breaks a loader rule, or an admin's value does not fit a setting."""


@dataclass(frozen=True)
class KitTokens:
    """The first-pass placeholder values of one rendering."""

    prefix: str
    settings: Mapping[str, str] = field(default_factory=dict)
    dataset_id: str = PREVIEW_DATASET_ID
    key_column: str = PREVIEW_KEY_COLUMN

    def values(self) -> dict[str, str]:
        """``{name: value}`` for :func:`render`."""
        return {
            **self.settings,
            "prefix": self.prefix,
            "dataset_id": self.dataset_id,
            "key_column": self.key_column,
        }


def _render_text(text: str, tokens: Mapping[str, str], *, keep_tools: bool) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in tokens:
            return tokens[name]
        if keep_tools and name.startswith("tool."):
            return match.group(0)
        raise KitError(f"unknown placeholder {{{{ kit.{name} }}}}")

    return _TOKEN_RE.sub(replace, text)


def render(value: Any, tokens: Mapping[str, str], *, keep_tools: bool = False) -> Any:
    """``value`` with every ``{{ kit.* }}`` filled (keys of objects included).

    Raises:
        KitError: A placeholder has no value (``tool.*`` ones are kept when ``keep_tools``).
    """
    if isinstance(value, str):
        return _render_text(value, tokens, keep_tools=keep_tools)
    if isinstance(value, list):
        return [render(item, tokens, keep_tools=keep_tools) for item in value]
    if isinstance(value, dict):
        return {
            _render_text(str(key), tokens, keep_tools=keep_tools): render(item, tokens, keep_tools=keep_tools)
            for key, item in value.items()
        }
    return value


# ------------------------------------------------------------------ the files
@lru_cache(maxsize=1)
def load_raw_kits() -> tuple[dict[str, Any], ...]:
    """Every kit file as parsed JSON, in :data:`KIT_ORDER`.

    Raises:
        KitError: A file is missing, unexpected, unreadable or names another id.
    """
    root = importlib.resources.files(KITS_PACKAGE).joinpath(KITS_DIR)
    present = sorted(entry.name for entry in root.iterdir() if entry.name.endswith(".json"))
    expected = sorted(f"{kit_id}.json" for kit_id in KIT_ORDER)
    if present != expected:
        raise KitError(f"the kit files are {present}; KIT_ORDER expects {expected}")
    raws: list[dict[str, Any]] = []
    for kit_id in KIT_ORDER:
        try:
            data = json.loads(root.joinpath(f"{kit_id}.json").read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise KitError(f"kit '{kit_id}': {exc}") from exc
        if not isinstance(data, dict) or data.get("id") != kit_id:
            raise KitError(f"kit file '{kit_id}.json' must hold the kit '{kit_id}'")
        raws.append(data)
    return tuple(raws)


def raw_kit(kit_id: str) -> dict[str, Any] | None:
    """The parsed file of ``kit_id``, or ``None``."""
    return next((raw for raw in load_raw_kits() if raw.get("id") == kit_id), None)


def _preview_settings(raw: Mapping[str, Any]) -> dict[str, str]:
    values: dict[str, str] = {}
    for entry in raw.get("defaults") or []:
        setting = KitSetting.model_validate(entry)
        sample = setting.example or setting.default
        if sample is None:
            raise KitError(f"kit '{raw.get('id')}': setting '{setting.name}' needs an example or a default")
        values[setting.name] = sample
    return values


# ------------------------------------------------------------------ rendering
def tool_names(variant: Mapping[str, Any], *, app: str | None = None) -> dict[str, str]:
    """``{tool key: tool name}`` of a first-pass-rendered variant.

    App actions are named as materialisation names them (``<toolkit>_<action>``) for ``app``
    (default: the variant's first app); the real names replace these once the tools exist.
    """
    from lkap_api.tool_providers.materialise import tool_name_for  # noqa: PLC0415 - heavy import

    names: dict[str, str] = {}
    apps = [entry for entry in variant.get("apps") or [] if isinstance(entry, Mapping)]
    if apps:
        chosen = next((entry for entry in apps if entry.get("toolkit") == app), apps[0])
        for action in chosen.get("actions") or []:
            names[str(action["key"])] = tool_name_for(str(chosen["toolkit"]), str(action["slug"]))
    for tool in variant.get("tools") or []:
        if tool.get("template"):
            template = tool_template(str(tool["template"]))
            if template is None:
                raise KitError(f"unknown tool template '{tool['template']}'")
            names[str(tool["key"])] = template.definition.name
        else:
            names[str(tool["key"])] = str((tool.get("definition") or {}).get("name", ""))
    return names


def render_kit(
    raw: Mapping[str, Any],
    tokens: KitTokens,
    *,
    variant_id: str | None = None,
    names: Mapping[str, str] | None = None,
    app: str | None = None,
) -> ToolKit:
    """The kit rendered and validated.

    With ``variant_id`` only that variant is kept (and becomes the default); without it every
    variant is rendered with its own tool names and the kit's own fields with the default
    variant's. ``names`` overrides the tool names of the kept (or default) variant.

    Raises:
        KitError: A placeholder has no value, or the rendered kit is not a valid kit.
    """
    data = copy.deepcopy(dict(raw))
    if variant_id is not None:
        data["variants"] = [v for v in data.get("variants") or [] if v.get("id") == variant_id]
        if not data["variants"]:
            raise KitError(f"kit '{raw.get('id')}' has no variant '{variant_id}'")
        data["default_variant"] = variant_id
        data["defaults"] = [
            setting
            for setting in data.get("defaults") or []
            if not setting.get("variants") or variant_id in setting["variants"]
        ]
    first = render(data, tokens.values(), keep_tools=True)
    default_id = first.get("default_variant")
    variants: list[dict[str, Any]] = []
    kit_names: dict[str, str] = {}
    for variant in first.get("variants") or []:
        own = dict(tool_names(variant, app=app))
        if variant.get("id") == default_id:
            if names is not None:
                own.update(names)
            kit_names = own
        variants.append(render(variant, {f"tool.{key}": name for key, name in own.items()}))
    first["variants"] = variants
    second = render(first, {f"tool.{key}": name for key, name in kit_names.items()})
    try:
        return ToolKit.model_validate(second)
    except ValidationError as exc:
        raise KitError(f"kit '{raw.get('id')}': {exc}") from exc


# ------------------------------------------------------------------ settings
def _clean_url(value: str, label: str) -> str:
    text = value.strip().rstrip("/")
    parts = urlsplit(text)
    if (
        len(text) > _MAX_URL
        or any(ch.isspace() for ch in text)
        or parts.scheme != "https"
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
    ):
        raise KitError(f"{label} must be an https:// address with no query, e.g. https://api.example.com/v1")
    return text


def _clean_host(value: str, label: str) -> str:
    text = value.strip().lower().rstrip(".")
    if not _HOST_RE.match(text):
        raise KitError(f"{label} must be a site name such as pay.example.com")
    return text


def _clean(setting: KitSetting, value: str | int | float | bool) -> str:
    label = f"'{setting.label}'"
    if isinstance(value, bool):
        raise KitError(f"{label} takes text, not true or false")
    text = str(value)
    if "{{" in text or "}}" in text:
        raise KitError(f"{label} may not hold '{{{{' or '}}}}'")
    match setting.kind:
        case "url":
            return _clean_url(text, label)
        case "host":
            return _clean_host(text, label)
        case "integer":
            stripped = text.strip()
            if isinstance(value, float) and value.is_integer():
                return str(int(value))
            if not stripped.isdigit():
                raise KitError(f"{label} must be a whole number")
            return str(int(stripped))
        case "text":
            if "\n" in text or len(text) > _MAX_TEXT or not text.strip():
                raise KitError(f"{label} must be one short line of text")
            return text.strip()


def resolve_settings(
    kit: ToolKit, variant: KitVariant, given: Mapping[str, str | int | float | bool]
) -> dict[str, str]:
    """The checked values of the settings ``variant`` uses (``given``, else their defaults).

    Raises:
        KitError: A value names no setting of the kit, a required value is missing, or a
            value does not fit its setting's kind.
    """
    known = {setting.name: setting for setting in kit.defaults}
    unknown = sorted(set(given) - set(known))
    if unknown:
        raise KitError(f"the kit has no setting(s) {', '.join(unknown)}; its settings: {', '.join(known)}")
    values: dict[str, str] = {}
    for setting in kit.defaults:
        if setting.variants and variant.id not in setting.variants:
            continue
        if setting.name in given:
            values[setting.name] = _clean(setting, given[setting.name])
        elif setting.default is not None:
            values[setting.name] = _clean(setting, setting.default)
        elif setting.required:
            raise KitError(f"the kit needs a value for '{setting.name}' ({setting.label})")
    return values


# ------------------------------------------------------------------ the catalogue checks
def _check_variant(kit: ToolKit, variant: KitVariant, prefix: str) -> None:
    where = f"kit '{kit.id}' variant '{variant.id}'"
    snippet = variant.instructions_snippet or kit.instructions_snippet
    problems = snippet_problems(snippet)
    if problems:
        raise KitError(f"{where}: {'; '.join(problems)}")
    for tool in variant.tools:
        definition = tool.definition
        if tool.template is not None:
            if tool_template(tool.template) is None:
                raise KitError(f"{where}: unknown tool template '{tool.template}'")
            continue
        if definition is None:
            continue
        if not definition.name.startswith(f"{prefix}_"):
            raise KitError(f"{where}: tool '{definition.name}' must start with the prefix")
        if isinstance(definition, HttpToolDefinition):
            parts = urlsplit(definition.url)
            if parts.scheme != "https" or not parts.hostname:
                raise KitError(f"{where}: tool '{definition.name}' must call an https:// address")
            if definition.allowed_hosts or definition.credential_id is not None:
                raise KitError(f"{where}: tool '{definition.name}' sets allowed_hosts or a key itself")
            texts = [definition.url, *definition.headers.values(), definition.body_template or ""]
            secrets = {name for text in texts for name in _SECRET_RE.findall(text)}
            if secrets - set(variant.requires.secret_names):
                raise KitError(f"{where}: tool '{definition.name}' names secrets its variant does not list")
    for block in [*kit.blocks, *variant.blocks]:
        if not block.shared and not block.id.startswith(f"{prefix}_"):
            raise KitError(f"{where}: block '{block.id}' must start with the prefix (or be shared)")
    for rule in [*kit.rules, *variant.rules]:
        if not rule.id.startswith(f"{prefix}_"):
            raise KitError(f"{where}: rule '{rule.id}' must start with the prefix")


def _check(raw: Mapping[str, Any]) -> ToolKit:
    settings = _preview_settings(raw)
    for name in settings:
        if name in RESERVED_TOKENS:
            raise KitError(f"kit '{raw.get('id')}': '{name}' is reserved and cannot be a setting")
    prefix = str(raw.get("default_prefix", ""))
    tokens = KitTokens(prefix=prefix, settings=settings)
    preview = render_kit(raw, tokens)
    for variant in preview.variants:
        kept = render_kit(raw, tokens, variant_id=variant.id)
        _check_variant(kept, kept.variants[0], prefix)
    return preview


@lru_cache(maxsize=1)
def load_kits() -> tuple[ToolKit, ...]:
    """Every kit, rendered with its default prefix and the settings' examples (``GET /v1/tool-kits``).

    Raises:
        KitError: A kit breaks one of the loader rules (module docstring).
    """
    return tuple(_check(raw) for raw in load_raw_kits())


def get_kit(kit_id: str) -> ToolKit | None:
    """The previewed kit ``kit_id``, or ``None``."""
    return next((kit for kit in load_kits() if kit.id == kit_id), None)
