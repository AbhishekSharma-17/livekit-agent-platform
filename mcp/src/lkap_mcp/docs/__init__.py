"""The hand-written docs and the generated registry files, as plain functions.

This module is the seam between V3-03 (this directory's content) and V3-01
(`server.py`, `resources.py`, `prompts.py`, `tools/discovery.py`), per
``docs/v3/PLAN-V3.md``'s V3-01 card: "reading docs from ``docs/`` and
``generated/`` through a small loader V3-03 also uses". `server.py` did not
exist yet when this was written (V3-01 had not landed), so this module is
written to the design's interface rather than against real code, and the one
line V3-01 must add is logged in ``docs/v3/_asks.md``.

Deliberately dependency-free: stdlib only (no FastMCP, no httpx, no pydantic),
so it can be imported and unit-tested — including by
``mcp/tests/test_docs_lint.py`` — before the rest of ``lkap_mcp`` exists, and
so importing it can never fail for a reason unrelated to a missing doc file.

Layout it reads:
    mcp/src/lkap_mcp/docs/guide.md
    mcp/src/lkap_mcp/docs/concepts/<topic>.md
    mcp/src/lkap_mcp/docs/recipes/<name>.md
    mcp/src/lkap_mcp/docs/prompts/<name>.md   (frontmatter + body, see Prompt)
    mcp/src/lkap_mcp/generated/providers.json
    mcp/src/lkap_mcp/generated/builtin_tools.json
    mcp/src/lkap_mcp/generated/schemas/<Model>.schema.json
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

__all__ = [
    "DocNotFoundError",
    "Prompt",
    "SearchHit",
    "builtin_tools",
    "concept",
    "concept_topics",
    "guide",
    "prompt",
    "prompt_names",
    "providers",
    "recipe",
    "recipe_names",
    "schema",
    "schema_names",
    "search",
]

_DOCS_DIR: Final[Path] = Path(__file__).parent
_CONCEPTS_DIR: Final[Path] = _DOCS_DIR / "concepts"
_RECIPES_DIR: Final[Path] = _DOCS_DIR / "recipes"
_PROMPTS_DIR: Final[Path] = _DOCS_DIR / "prompts"
_GENERATED_DIR: Final[Path] = _DOCS_DIR.parent / "generated"

#: The 15 topics of AGENT-ACCESS.md §3.1, in the order the guide lists them.
CONCEPT_TOPICS: Final[tuple[str, ...]] = (
    "agents",
    "pipeline-modes",
    "providers-and-keys",
    "connections-and-pools",
    "knowledge",
    "tools-http",
    "tools-mcp",
    "panels-and-blocks",
    "flows",
    "telephony",
    "qa-and-evals",
    "recordings-and-cost",
    "webhooks",
    "sessions-and-test-chat",
    "roles-and-scopes",
)

#: The 10 recipes of AGENT-ACCESS.md §3.1.
RECIPE_NAMES: Final[tuple[str, ...]] = (
    "connect-livekit",
    "insurance-intake-agent",
    "generic-assistant",
    "add-http-tool",
    "attach-mcp-server",
    "knowledge-from-text",
    "switch-to-flow",
    "composite-panel",
    "test-and-publish",
    "diagnose-a-session",
)

#: The 7 prompts of AGENT-ACCESS.md §3.2.
PROMPT_NAMES: Final[tuple[str, ...]] = (
    "build_agent",
    "connect_livekit",
    "add_http_tool",
    "add_knowledge",
    "test_agent",
    "diagnose_session",
    "review_config",
)


class DocNotFoundError(LookupError):
    """Raised when a requested topic, recipe or prompt does not exist."""


@dataclass(frozen=True)
class Prompt:
    """One MCP prompt: parsed frontmatter, the template body, and the doc refs it inlines.

    ``arguments`` are the prompt's parameter names (all optional strings, in
    MCP prompt convention); ``{name}`` placeholders in ``body`` are filled by
    :meth:`render` with ``str.format``-style substitution, blank when omitted.
    ``concepts``/``recipes`` name the docs :meth:`render` appends verbatim, so
    the prompt's result is "the relevant recipe and concept doc inline plus
    the exact next tool calls" (AGENT-ACCESS.md §3.2) without V3-01's
    ``prompts.py`` needing to know which doc goes with which prompt — that
    mapping lives here, in the frontmatter, next to the prose that uses it.
    V3-01 registers one FastMCP ``@mcp.prompt()`` per entry, calling
    :meth:`render` with the caller's arguments.
    """

    name: str
    description: str
    arguments: tuple[str, ...]
    concepts: tuple[str, ...]
    recipes: tuple[str, ...]
    body: str

    def render(self, **kwargs: str) -> str:
        """Fill the template, then append every referenced concept and recipe doc."""
        values = {arg: kwargs.get(arg, "") for arg in self.arguments}
        parts = [self.body.format(**values)]
        for topic in self.concepts:
            parts.append(f"\n---\n\n## Concept: {topic}\n\n{concept(topic)}")
        for name in self.recipes:
            parts.append(f"\n---\n\n## Recipe: {name}\n\n{recipe(name)}")
        return "\n".join(parts)


@dataclass(frozen=True)
class SearchHit:
    """One result of :func:`search`."""

    uri: str
    title: str
    snippet: str


def guide() -> str:
    """The platform guide (``lkap://guide``, the ``lkap_guide`` tool)."""
    return (_DOCS_DIR / "guide.md").read_text(encoding="utf-8")


def concept_topics() -> tuple[str, ...]:
    """Every concept topic name, in guide order."""
    return CONCEPT_TOPICS


def concept(topic: str) -> str:
    """One concept doc (``lkap://concepts/{topic}``, ``lkap_explain(topic)``).

    Raises:
        DocNotFoundError: ``topic`` is not one of :data:`CONCEPT_TOPICS`.
    """
    if topic not in CONCEPT_TOPICS:
        raise DocNotFoundError(f"unknown concept topic: {topic!r}")
    return (_CONCEPTS_DIR / f"{topic}.md").read_text(encoding="utf-8")


def recipe_names() -> tuple[str, ...]:
    """Every recipe name."""
    return RECIPE_NAMES


def recipe(name: str) -> str:
    """One recipe (``lkap://recipes/{name}``, ``lkap_describe("recipe", name)``).

    Raises:
        DocNotFoundError: ``name`` is not one of :data:`RECIPE_NAMES`.
    """
    if name not in RECIPE_NAMES:
        raise DocNotFoundError(f"unknown recipe: {name!r}")
    return (_RECIPES_DIR / f"{name}.md").read_text(encoding="utf-8")


_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)


def _parse_prompt_file(path: Path) -> Prompt:
    """Parse one ``prompts/<name>.md`` file into a :class:`Prompt`.

    The frontmatter is deliberately not YAML (no extra dependency): two plain
    ``key: value`` lines, ``arguments`` comma-separated.
    """
    text = path.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise ValueError(f"{path}: missing '---' frontmatter block")
    header, body = match.group(1), match.group(2).strip("\n")
    fields: dict[str, str] = {}
    for line in header.splitlines():
        if not line.strip():
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    name = fields.get("name", path.stem)
    description = fields.get("description", "")
    arguments = _split_csv(fields.get("arguments", ""))
    concepts = _split_csv(fields.get("concepts", ""))
    recipes = _split_csv(fields.get("recipes", ""))
    return Prompt(
        name=name,
        description=description,
        arguments=arguments,
        concepts=concepts,
        recipes=recipes,
        body=body,
    )


def _split_csv(raw: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def prompt_names() -> tuple[str, ...]:
    """Every prompt name."""
    return PROMPT_NAMES


def prompt(name: str) -> Prompt:
    """One parsed prompt definition.

    Raises:
        DocNotFoundError: ``name`` is not one of :data:`PROMPT_NAMES`.
    """
    if name not in PROMPT_NAMES:
        raise DocNotFoundError(f"unknown prompt: {name!r}")
    return _parse_prompt_file(_PROMPTS_DIR / f"{name}.md")


def providers() -> dict[str, Any]:
    """The generated provider registry (``mcp/src/lkap_mcp/generated/providers.json``)."""
    doc: dict[str, Any] = json.loads((_GENERATED_DIR / "providers.json").read_text(encoding="utf-8"))
    return doc


def builtin_tools() -> dict[str, Any]:
    """The generated built-in/block tool names document."""
    doc: dict[str, Any] = json.loads((_GENERATED_DIR / "builtin_tools.json").read_text(encoding="utf-8"))
    return doc


def schema_names() -> tuple[str, ...]:
    """Every exported model name with a schema file (sorted)."""
    return tuple(sorted(p.stem for p in (_GENERATED_DIR / "schemas").glob("*.schema.json")))


def schema(model_name: str) -> dict[str, Any]:
    """One exported JSON schema (``lkap://schemas/{Model}``).

    Raises:
        DocNotFoundError: No schema file for ``model_name``.
    """
    path = _GENERATED_DIR / "schemas" / f"{model_name}.schema.json"
    if not path.exists():
        raise DocNotFoundError(f"no exported schema for {model_name!r}")
    doc: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return doc


def _iter_doc_sources() -> list[tuple[str, str, str]]:
    """Every ``(uri, title, text)`` triple that :func:`search` indexes."""
    sources: list[tuple[str, str, str]] = [("lkap://guide", "Platform guide", guide())]
    for topic in CONCEPT_TOPICS:
        sources.append((f"lkap://concepts/{topic}", topic, concept(topic)))
    for name in RECIPE_NAMES:
        sources.append((f"lkap://recipes/{name}", name, recipe(name)))
    return sources


def search(query: str, limit: int = 10) -> list[SearchHit]:
    """Rank every doc by how many query words it contains (in-memory, no embeddings).

    Args:
        query: Free-text search terms.
        limit: Maximum hits to return.

    Returns:
        Hits ordered by descending score, each with a one-line snippet around
        the first match.
    """
    terms = [t for t in re.findall(r"[a-z0-9_]+", query.lower()) if t]
    if not terms:
        return []
    scored: list[tuple[int, SearchHit]] = []
    for uri, title, text in _iter_doc_sources():
        lowered = text.lower()
        score = sum(lowered.count(term) for term in terms)
        if score == 0:
            continue
        idx = min((lowered.find(term) for term in terms if term in lowered), default=0)
        start = max(0, idx - 60)
        snippet = text[start : start + 160].replace("\n", " ").strip()
        scored.append((score, SearchHit(uri=uri, title=title, snippet=snippet)))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [hit for _, hit in scored[:limit]]
