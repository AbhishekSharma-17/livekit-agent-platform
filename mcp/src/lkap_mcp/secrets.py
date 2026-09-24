"""``SecretInput``: secrets go in inline or by reference, and never come out (D-V3-4, R-V3-3).

A secret argument is a plain string in one of these forms:

* ``env:NAME`` — the MCP process's environment variable ``NAME``;
* ``file:/abs/path`` (or ``file:~/path``) — the whole file, trimmed;
* ``file:/abs/path#KEY`` — the ``KEY=value`` line of a dotenv-style file;
* ``raw:<value>`` — the literal ``<value>`` (for values that start with ``env:``/``file:``);
* anything else — the inline value itself, pasted by the user.

Inline values are allowed unless ``LKAP_MCP_INLINE_SECRETS=off``. ``file:`` refs
are refused in HTTP mode. Every value this module sees — inline or resolved —
is added to a process-local set, and :func:`scrub_text` replaces any occurrence
of one in any result, error or log text with ``<redacted>``. Nothing here ever
logs or formats a value; messages name the reference, never its content.
"""

from __future__ import annotations

import os
import re
import stat
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

#: What a secret looks like in any result.
REDACTED = "<redacted>"
#: How an inline secret is shown in a ``plan``.
INLINE_PLACEHOLDER = "<inline secret>"
#: How a by-reference secret is shown in a ``plan``.
REF_PLACEHOLDER = "<ref>"

#: The reference forms, named in every refusal so the agent can switch.
REFERENCE_FORMS = "env:NAME, file:/abs/path, file:/abs/path#KEY"

#: Values shorter than this are not scrubbed from free text (they would shred
#: every result); key-based redaction still covers them.
MIN_SCRUB_LENGTH = 8

_ENV_REF = re.compile(r"^env:(?P<name>[A-Z_][A-Z0-9_]*)$")
_FILE_REF = re.compile(r"^file:(?P<path>(?:/|~/)[^#]+)(?:#(?P<key>[A-Za-z_][A-Za-z0-9_]*))?$")
_DOTENV_LINE = re.compile(r"^\s*(?:export\s+)?(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.*?)\s*$")

SecretKind = Literal["inline", "env", "file"]


class SecretInputError(Exception):
    """A secret argument that cannot be used; the message never contains a value."""

    def __init__(self, code: str, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint


@dataclass(frozen=True)
class SecretInput:
    """A parsed secret argument (the value is held only for inline secrets)."""

    kind: SecretKind
    field_name: str
    inline_value: str | None = field(default=None, repr=False)
    env_name: str | None = None
    path: str | None = None
    key: str | None = None

    @property
    def placeholder(self) -> str:
        """What a ``plan`` shows in place of the value."""
        return INLINE_PLACEHOLDER if self.kind == "inline" else REF_PLACEHOLDER

    @property
    def reference(self) -> str | None:
        """The reference as written (``env:NAME`` / ``file:…#KEY``), or ``None`` for inline."""
        if self.kind == "env":
            return f"env:{self.env_name}"
        if self.kind == "file":
            return f"file:{self.path}" + (f"#{self.key}" if self.key else "")
        return None


@dataclass(frozen=True)
class ResolvedSecret:
    """A resolved value plus warnings (e.g. a group-readable file)."""

    value: str = field(repr=False)
    warnings: tuple[str, ...] = ()


class _SeenValues:
    """The process-local set of every secret value this process has seen."""

    def __init__(self) -> None:
        self._values: set[str] = set()
        self._lock = threading.Lock()
        self._ordered: tuple[str, ...] = ()

    def add(self, value: str) -> None:
        stripped = value.strip()
        with self._lock:
            for candidate in {value, stripped}:
                if len(candidate) >= MIN_SCRUB_LENGTH:
                    self._values.add(candidate)
            self._ordered = tuple(sorted(self._values, key=len, reverse=True))

    def scrub(self, text: str) -> str:
        for value in self._ordered:
            if value in text:
                text = text.replace(value, REDACTED)
        return text

    def clear(self) -> None:
        with self._lock:
            self._values.clear()
            self._ordered = ()


SEEN = _SeenValues()


def remember(value: str) -> None:
    """Record a secret value so it is scrubbed from every later output."""
    SEEN.add(value)


def scrub_text(text: str) -> str:
    """Replace every seen secret value in ``text`` by ``<redacted>``."""
    return SEEN.scrub(text)


def parse_secret(
    raw: str, *, field_name: str, inline_allowed: bool = True, file_allowed: bool = True
) -> SecretInput:
    """Parse a secret argument without reading any environment variable or file.

    An inline value is remembered for scrubbing immediately, before any
    request is made.

    Args:
        raw: The argument as the client sent it.
        field_name: The argument name, for messages.
        inline_allowed: ``False`` under ``LKAP_MCP_INLINE_SECRETS=off``.
        file_allowed: ``False`` in HTTP mode.

    Raises:
        SecretInputError: ``inline_secret_refused``, ``ref_unavailable_in_http_mode``,
            ``invalid_secret_ref`` or ``empty_secret``.
    """
    if raw.startswith("raw:"):
        return _inline(raw.removeprefix("raw:"), field_name, inline_allowed)
    if raw.startswith("env:"):
        match = _ENV_REF.match(raw)
        if match is None:
            raise SecretInputError(
                "invalid_secret_ref",
                f"{field_name}: an env: reference must be env:NAME with NAME in [A-Z_][A-Z0-9_]*",
                hint="Write raw:<value> for a literal value that starts with 'env:'.",
            )
        return SecretInput(kind="env", field_name=field_name, env_name=match.group("name"))
    if raw.startswith("file:"):
        match = _FILE_REF.match(raw)
        if match is None:
            raise SecretInputError(
                "invalid_secret_ref",
                f"{field_name}: a file: reference must be file:/abs/path or file:/abs/path#KEY",
                hint="Write raw:<value> for a literal value that starts with 'file:'.",
            )
        if not file_allowed:
            raise SecretInputError(
                "ref_unavailable_in_http_mode",
                f"{field_name}: file: references are not available on the remote MCP service",
                hint="Use env:NAME (provisioned by the operator) or paste the value inline.",
            )
        return SecretInput(
            kind="file", field_name=field_name, path=match.group("path"), key=match.group("key")
        )
    return _inline(raw, field_name, inline_allowed)


def _inline(value: str, field_name: str, inline_allowed: bool) -> SecretInput:
    if value.strip():
        remember(value)
    if not inline_allowed:
        raise SecretInputError(
            "inline_secret_refused",
            f"{field_name}: inline secret values are disabled on this server (LKAP_MCP_INLINE_SECRETS=off)",
            hint=f"Pass a reference instead: {REFERENCE_FORMS}.",
        )
    if not value.strip():
        raise SecretInputError("empty_secret", f"{field_name}: the secret is empty")
    return SecretInput(kind="inline", field_name=field_name, inline_value=value)


def resolve_secret(secret: SecretInput) -> ResolvedSecret:
    """Resolve a parsed secret to its value (reads the environment or a file).

    Synchronous on purpose: the file read is tiny and this keeps blocking I/O
    out of ``async def`` bodies.

    Raises:
        SecretInputError: ``secret_ref_unresolved`` when the variable, file or key is missing.
    """
    if secret.kind == "inline":
        assert secret.inline_value is not None
        return ResolvedSecret(value=secret.inline_value)
    if secret.kind == "env":
        assert secret.env_name is not None
        value = os.environ.get(secret.env_name)
        if value is None or not value.strip():
            raise SecretInputError(
                "secret_ref_unresolved",
                f"{secret.field_name}: environment variable {secret.env_name} is not set in the MCP process",
            )
        remember(value)
        return ResolvedSecret(value=value.strip())
    return _resolve_file(secret)


def _resolve_file(secret: SecretInput) -> ResolvedSecret:
    assert secret.path is not None
    path = Path(secret.path).expanduser()
    reference = secret.reference
    try:
        mode = path.stat().st_mode
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SecretInputError(
            "secret_ref_unresolved",
            f"{secret.field_name}: cannot read {reference} ({type(exc).__name__})",
        ) from None
    warnings: tuple[str, ...] = ()
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        warnings = (f"{secret.field_name}: {path} is readable by group or others; chmod 600 it",)
    if secret.key is None:
        value = content.strip()
    else:
        found = _dotenv_value(content, secret.key)
        if found is None:
            raise SecretInputError(
                "secret_ref_unresolved", f"{secret.field_name}: key {secret.key} not found in {path}"
            )
        value = found
    if not value:
        raise SecretInputError("secret_ref_unresolved", f"{secret.field_name}: {reference} is empty")
    remember(value)
    return ResolvedSecret(value=value, warnings=warnings)


def _dotenv_value(content: str, key: str) -> str | None:
    for line in content.splitlines():
        if line.lstrip().startswith("#"):
            continue
        match = _DOTENV_LINE.match(line)
        if match is None or match.group("key") != key:
            continue
        value = match.group("value")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        return value
    return None
