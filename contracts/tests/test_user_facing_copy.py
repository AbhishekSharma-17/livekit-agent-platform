"""User-facing backend text keeps the house copy style (UI-R2a).

The rule: no em dash or en dash in anything a person reads or hears, and no semicolon
joining two statements in a message (split it into two sentences instead). This guards
the strings the backend copy pass cleaned:

* every string of the starter templates, tool kits and seed documents the api ships, and
  the insurance pack's seed documents (shown in the gallery, spoken, or seeded as data),
  plus the other text files shipped as content (the MCP docs and the policy directory CSV);
* the provider registry's help, notes and other notes the console renders;
* error, validation-issue and test-result messages in the api, contracts, agent and mcp
  sources (the literal first arguments and ``message=``-style keywords of their
  constructors), plus the MCP tool catalog snapshot.

Model-facing prompts, docstrings, log lines and OpenAPI descriptions are out of scope.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from lkap_contracts.providers import REGISTRY

REPO = Path(__file__).resolve().parents[2]
DASHES = re.compile(r"[\N{EM DASH}\N{EN DASH}]")
#: A semicolon joining two statements ("X; do Y"), as opposed to one inside code or a list.
STATEMENT_SEMICOLON = re.compile(r"; [a-z]")
#: The same rule for seed files, where a wrapped line can end in the semicolon, and where
#: `` -- `` standing in for a dash is also out.
SEED_SEMICOLON = re.compile(r";\s+[A-Za-z]")
SEED_DASH = re.compile(r"[\N{EM DASH}\N{EN DASH}]| -- ")
#: Template fields whose value is a list of items separated by semicolons (mock policy rows).
SEMICOLON_LIST_KEYS = frozenset({"deductibles", "coverages"})

#: Files the guard reads; each is skipped when this checkout does not contain it.
TEMPLATE_DIRS = (
    REPO / "api" / "src" / "lkap_api" / "templates",
    REPO / "packs" / "src" / "packs" / "insurance_claim" / "seeds",
)
CATALOG = REPO / "api" / "src" / "lkap_api" / "templates" / "catalog"
#: Other text shipped and loaded as content; checked for dashes only.
CONTENT_FILES = (
    CATALOG / "claims_intake" / "seeds" / "policy_directory.csv",
    REPO / "api" / "src" / "lkap_api" / "custom_models" / "probes" / "fixtures" / "README",
    REPO / "mcp" / "src" / "lkap_mcp" / "docs" / "__init__.py",
)
MCP_DOCS = REPO / "mcp" / "src" / "lkap_mcp" / "docs"
MESSAGE_SOURCES = (
    REPO / "api" / "src" / "lkap_api",
    REPO / "contracts" / "src" / "lkap_contracts",
    REPO / "agent" / "src" / "lkap_agent",
    REPO / "mcp" / "src" / "lkap_mcp",
)
MCP_SNAPSHOT = REPO / "mcp" / "tests" / "tools.snap.json"

#: Constructors whose message argument reaches a person (console, caller page, MCP client).
MESSAGE_CALLEE = re.compile(r"(Error|Issue|Result|Out|Warning|Change|Skipped|Health)$")
#: Built-in exceptions are internal unless raised from a contracts validator (a 422 message).
BUILTIN_EXCEPTIONS = frozenset({"ValueError", "RuntimeError", "TypeError", "KeyError", "ConnectionError"})
MESSAGE_KEYWORDS = frozenset({"message", "reason", "detail", "hint", "fix", "error", "summary", "note"})
#: Calls whose keywords carry developer-facing text (OpenAPI, argparse), not product copy.
NON_COPY_CALLS = frozenset({"Field", "Query", "Path", "Body", "add_argument", "add_parser"})
#: Source files that are command-line tools, not product surfaces.
CLI_FILES = frozenset({"__main__.py", "keys.py", "jobs.py", "bootstrap.py", "settings.py", "cli.py"})

#: Registry fields the console renders as help or notes.
PROVIDER_TEXT_KEYS = frozenset(
    {"label", "help", "notes", "note", "unlisted_note", "streaming_note", "avatar_aspect_note", "price_note"}
)


def _json_strings(value: object, path: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _json_strings(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _json_strings(item, f"{path}[{index}]")
    elif isinstance(value, str):
        yield path, value


def _template_files() -> list[Path]:
    files: list[Path] = []
    for root in TEMPLATE_DIRS:
        if root.is_dir():
            files += sorted(p for p in root.rglob("*") if p.suffix in {".json", ".md"})
    return files


def _seed_problems(
    file: Path, pattern: re.Pattern[str], *, skip_keys: frozenset[str] = frozenset()
) -> list[str]:
    """Every place in ``file`` where a string (JSON) or the text (anything else) matches ``pattern``."""
    text = file.read_text(encoding="utf-8")
    where = str(file.relative_to(REPO))
    if file.suffix == ".json":
        return [
            f"{where}:{path}: {value!r}"
            for path, value in _json_strings(json.loads(text))
            if path.rsplit(".", 1)[-1] not in skip_keys and pattern.search(value)
        ]
    return [
        f"{where}:{text.count(chr(10), 0, match.start()) + 1}: {match.group(0)!r}"
        for match in pattern.finditer(text)
    ]


def test_template_kit_and_seed_text_has_no_dashes() -> None:
    files = _template_files()
    if not files:
        pytest.skip("the api templates and pack seeds are not in this checkout")
    offenders: list[str] = []
    for file in files:
        offenders += _seed_problems(file, SEED_DASH)
    assert not offenders, "dashes in user-facing template text:\n" + "\n".join(offenders)


def test_template_kit_and_seed_text_has_no_joining_semicolons() -> None:
    files = _template_files()
    if not files:
        pytest.skip("the api templates and pack seeds are not in this checkout")
    offenders: list[str] = []
    for file in files:
        offenders += _seed_problems(file, SEED_SEMICOLON, skip_keys=SEMICOLON_LIST_KEYS)
    message = "semicolons joining statements in template text (split the sentence):\n"
    assert not offenders, message + "\n".join(offenders)


def test_other_shipped_content_has_no_dashes() -> None:
    files = [file for file in CONTENT_FILES if file.is_file()]
    if MCP_DOCS.is_dir():
        files += sorted(MCP_DOCS.rglob("*.md"))
    if not files:
        pytest.skip("the shipped content files are not in this checkout")
    offenders: list[str] = []
    for file in files:
        offenders += _seed_problems(file, SEED_DASH if file.suffix != ".py" else DASHES)
    assert not offenders, "dashes in shipped content:\n" + "\n".join(offenders)


def test_demo_names_use_a_middle_dot() -> None:
    files = _template_files()
    if not files:
        pytest.skip("the api templates and pack seeds are not in this checkout")
    offenders = [
        str(file.relative_to(REPO))
        for file in files
        if re.search(r"Demo\s*(?:[\N{EM DASH}\N{EN DASH}]|--?)\s", file.read_text("utf-8"))
    ]
    assert not offenders, f"demo names should read 'Demo · <Name>': {offenders}"


def _provider_strings() -> Iterator[tuple[str, str]]:
    document = json.loads(json.dumps([spec.model_dump(mode="json") for spec in REGISTRY]))

    def walk(value: object, key: str | None, where: str) -> Iterator[tuple[str, str]]:
        if isinstance(value, dict):
            for child_key, item in value.items():
                yield from walk(item, child_key, f"{where}.{child_key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                yield from walk(item, key, f"{where}[{index}]")
        elif isinstance(value, str) and key in PROVIDER_TEXT_KEYS:
            yield where, value

    for index, spec in enumerate(document):
        yield from walk(spec, None, str(spec.get("id", index)))


def test_provider_help_and_notes_have_no_dashes_or_joined_statements() -> None:
    offenders = [
        f"{where}: {value!r}"
        for where, value in _provider_strings()
        if DASHES.search(value) or STATEMENT_SEMICOLON.search(value)
    ]
    assert not offenders, "provider registry copy breaks the style:\n" + "\n".join(offenders)


def _callee(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _literal_text(node: ast.AST) -> str | None:
    """The literal parts of a string expression (interpolations become a placeholder)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(str(part.value) if isinstance(part, ast.Constant) else "{}" for part in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _literal_text(node.left), _literal_text(node.right)
        if left is None and right is None:
            return None
        return (left or "{}") + (right or "{}")
    return None


def _message_literals(file: Path, *, validators: bool) -> Iterator[tuple[int, str]]:
    tree = ast.parse(file.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _callee(node)
        candidates: list[ast.AST] = []
        if MESSAGE_CALLEE.search(name) and (validators or name not in BUILTIN_EXCEPTIONS):
            candidates += node.args[:2]
        if name not in NON_COPY_CALLS:
            candidates += [kw.value for kw in node.keywords if kw.arg in MESSAGE_KEYWORDS]
        for candidate in candidates:
            text = _literal_text(candidate)
            if text is not None:
                yield candidate.lineno, text


@pytest.mark.parametrize("root", MESSAGE_SOURCES, ids=lambda root: root.parents[1].name)
def test_error_and_issue_messages_keep_the_copy_style(root: Path) -> None:
    if not root.is_dir():
        pytest.skip(f"{root.relative_to(REPO)} is not in this checkout")
    validators = root.name == "lkap_contracts"
    offenders: list[str] = []
    for file in sorted(root.rglob("*.py")):
        if file.name in CLI_FILES:
            continue
        for line, text in _message_literals(file, validators=validators):
            if DASHES.search(text) or STATEMENT_SEMICOLON.search(text):
                offenders.append(f"{file.relative_to(REPO)}:{line}: {text!r}")
    assert not offenders, "messages with an em dash or a joining semicolon:\n" + "\n".join(offenders)


def test_mcp_tool_catalog_has_no_dashes() -> None:
    if not MCP_SNAPSHOT.is_file():
        pytest.skip("the mcp package is not in this checkout")
    offenders = [
        f"{path}: {value!r}"
        for path, value in _json_strings(json.loads(MCP_SNAPSHOT.read_text(encoding="utf-8")))
        if DASHES.search(value)
    ]
    assert not offenders, "em/en dashes in MCP tool descriptions:\n" + "\n".join(offenders)
