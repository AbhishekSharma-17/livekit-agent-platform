"""Snapshot every LiveKit Agents plugin class's constructor signature via AST.

The provider registry (`contracts/src/lkap_contracts/providers.py`) declares
`secret_fields`/`fields` for ~90 providers, most of whose plugin packages are
*not* installed in this project's venv (`agent/.venv` only carries the 8
packages the v1 MVP set needs). This script is how V2-05 verified every other
entry's field names without guessing: it AST-parses the real release-tagged
source tree (never imports it — most of these packages have native/heavy
dependencies this project doesn't install) and writes a JSON fixture of every
class's accepted keyword names.

Usage::

    git clone --depth 1 --branch livekit-agents@1.8.3 https://github.com/livekit/agents /tmp/lk-agents-1.8.3
    uv run python scripts/snapshot_plugin_signatures.py \\
        --source /tmp/lk-agents-1.8.3 \\
        --out agent/tests/fixtures/plugin_signatures.json

The output is committed (`agent/tests/fixtures/plugin_signatures.json`) so
`agent/tests/unit/test_factory_signatures.py` can check every registry
`FieldSpec.name` against it without needing the clone at test time.

What it captures per class (keyed by its dotted path, e.g.
``livekit.plugins.google.realtime.RealtimeModel``):

- ``params``: every ``__init__`` parameter name (keyword-or-positional,
  keyword-only, and positional-only), unioned across every ``@overload``
  found for that class (Rime has three, the OpenAI realtime classes have an
  Azure overload) — a name accepted by *any* overload counts as accepted.
- ``has_var_keyword``: true if any overload's ``__init__`` takes ``**kwargs``
  (LemonSlice) — the field-name check is skipped for such classes since any
  name is technically forwarded.
- ``positional_only``: names that are positional-only or positional-or-keyword
  *before* the first ``*``/keyword-only marker and have no default — used to
  flag the ``FieldSpec.positional=True`` cases (D-ID's ``agent_id``,
  Synthesia's ``avatar_config``).

Classmethod constructors (``RealtimeModel.with_azure``, ``LLM.with_cerebras``,
``VAD.load``, and the static ``LLM.with_openrouter``) are additionally
captured under a synthetic ``<Class>.<classmethod>`` key with the same shape, because the registry may
point `python_class` at one of these instead of `__init__`.

Nested config dataclasses/BaseModels referenced from a provider's `fields[]`
via `nested_model` (``SimliConfig``, ``PersonaConfig``, Synthesia's
``AvatarConfig``, D-ID's ``AudioConfig``) are captured the same way so a
dotted field name like ``simli_config.face_id`` can be checked against
``SimliConfig``'s own ``__init__``/field names.

Star-import shims (R-V4-55): since livekit-agents 1.8.3 (upstream #7318)
``livekit/plugins/openai/realtime/realtime_model.py`` is a
``from livekit.agents.llm._realtime.openai import *`` shim. A plugin file's
``from livekit.agents.<module> import *`` is followed exactly one hop into the
``livekit-agents/`` tree of the same clone: the target file is snapshotted
under its own module path, and each public class it defines (its ``__all__``
if it declares one, else every class whose name has no leading underscore) is
re-exported under the shim's module path, so the ordinary ``__init__.py``
re-export pass then gives it its public key
(``livekit.plugins.openai.realtime.RealtimeModel``). A star import inside the
target is reported, not followed.
"""

from __future__ import annotations

import argparse
import ast
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Nested config classes referenced by `nested_model` in the registry, beyond
#: whatever `AvatarSession`/`STT`/`TTS`/`LLM`/`RealtimeModel` classes a plain
#: directory walk already finds. Listed explicitly because they live in the
#: same file as their consuming class and a generic walk already reaches them
#: too — kept here only as the canonical list this script promises to check.
NESTED_MODEL_CLASSES: frozenset[str] = frozenset(
    {"SimliConfig", "PersonaConfig", "AvatarConfig", "AudioConfig", "Credentials"}
)

#: Method names, beyond `__init__`, worth snapshotting as alternate
#: constructors. Selected by name, not by decorator: `LLM.with_openrouter` is a
#: `@staticmethod` (not a classmethod) and is captured the same way (its `is_classmethod`
#: flag records the decorator honestly, so it is `false` there).
CLASSMETHOD_CONSTRUCTORS: frozenset[str] = frozenset(
    {"with_azure", "with_cerebras", "with_openrouter", "load", "create"}
)


def _decorator_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    names: set[str] = set()
    for dec in node.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, ast.Attribute):
            names.add(target.attr)
    return names


def _param_names(args: ast.arguments) -> tuple[list[str], list[str]]:
    """Return (all param names excluding self/cls, positional-only-with-no-default names)."""
    all_names: list[str] = []
    no_default_positional: list[str] = []

    posonly = list(args.posonlyargs)
    pos = list(args.args)
    combined = posonly + pos
    # Drop leading self/cls.
    if combined and combined[0].arg in ("self", "cls"):
        combined = combined[1:]
        if posonly and posonly[0].arg in ("self", "cls"):
            posonly = posonly[1:]

    defaults_for_combined = list(args.defaults)
    n_without_default = len(combined) - len(defaults_for_combined)
    for i, a in enumerate(combined):
        all_names.append(a.arg)
        if i < n_without_default:
            no_default_positional.append(a.arg)

    for a in args.kwonlyargs:
        all_names.append(a.arg)

    return all_names, no_default_positional


def _has_var_keyword(args: ast.arguments) -> bool:
    return args.kwarg is not None


_Method = ast.FunctionDef | ast.AsyncFunctionDef


def _function_defs(tree: ast.Module) -> list[tuple[str, ast.ClassDef, _Method]]:
    """Yield (class_name, class_node, method_node) for every method of every top-level class."""
    out: list[tuple[str, ast.ClassDef, _Method]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.append((node.name, node, item))
    return out


def _dataclass_field_names(tree: ast.Module) -> dict[str, list[str]]:
    """Return {class_name: [field names]} for classes with no explicit ``__init__``.

    Covers ``@dataclass`` (and Pydantic ``BaseModel``) config classes referenced
    by the registry's ``nested_model`` (``SimliConfig``, ``PersonaConfig``,
    Synthesia's ``AvatarConfig``, D-ID's ``AudioConfig``): none of these define
    an explicit ``__init__``, so their constructor kwargs are the class body's
    top-level annotated assignments instead.
    """
    out: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        has_explicit_init = any(
            isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == "__init__"
            for item in node.body
        )
        if has_explicit_init:
            continue
        fields = [
            item.target.id
            for item in node.body
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
        ]
        if fields:
            out[node.name] = fields
    return out


def snapshot_file(path: Path, dotted_module: str) -> dict[str, dict[str, Any]]:
    """Return {dotted_class_or_classmethod_path: signature} for one source file."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        logger.warning("skipping unparseable file: %s", path)
        return {}

    # Group by (class, method_name) to union @overload signatures.
    grouped: dict[tuple[str, str], list[ast.arguments]] = {}
    classmethod_flags: dict[tuple[str, str], bool] = {}
    for class_name, _class_node, method in _function_defs(tree):
        decs = _decorator_names(method)
        method_name = method.name
        if method_name != "__init__" and method_name not in CLASSMETHOD_CONSTRUCTORS:
            continue
        key = (class_name, method_name)
        grouped.setdefault(key, []).append(method.args)
        classmethod_flags[key] = "classmethod" in decs

    result: dict[str, dict[str, Any]] = {}
    for (class_name, method_name), arglists in grouped.items():
        all_names: set[str] = set()
        no_default: set[str] = set()
        var_kw = False
        for args in arglists:
            names, nd = _param_names(args)
            all_names.update(names)
            no_default.update(nd)
            var_kw = var_kw or _has_var_keyword(args)
        class_path = f"{dotted_module}.{class_name}"
        dotted = class_path if method_name == "__init__" else f"{class_path}.{method_name}"
        result[dotted] = {
            "params": sorted(all_names),
            "has_var_keyword": var_kw,
            "positional_only": sorted(no_default),
            "is_classmethod": classmethod_flags[(class_name, method_name)],
        }

    # Dataclass-style config classes (no explicit __init__): field names ARE the ctor kwargs.
    for class_name, field_names in _dataclass_field_names(tree).items():
        dotted = f"{dotted_module}.{class_name}"
        if dotted in result:
            continue
        result[dotted] = {
            "params": sorted(field_names),
            "has_var_keyword": False,
            "positional_only": [],
            "is_classmethod": False,
        }
    return result


def _reexport_edges(path: Path, dotted_module: str) -> list[tuple[str, str, str, str]]:
    """Return (public_module, public_name, source_module, source_name) for ``from``-imports in one file.

    Only relative imports (``from . import x``, ``from .sub import Y as Z``) are
    resolved — every package in this catalog re-exports its public surface with
    relative imports, and resolving absolute imports too would risk chasing
    imports into unrelated packages (``typing``, ``pydantic``, ...).
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return []
    edges: list[tuple[str, str, str, str]] = []
    module_parts = dotted_module.split(".") if dotted_module else []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.level < 1:
            continue
        # `level` dots strip that many trailing components from the *package* the
        # importing module lives in (for an __init__.py, the module IS the package).
        base = module_parts[: len(module_parts) - (node.level - 1)] if node.level > 1 else module_parts
        if node.module:
            source_module = ".".join([*base, node.module])
        else:
            source_module = ".".join(base)
        for alias in node.names:
            if alias.name == "*":
                continue
            public_name = alias.asname or alias.name
            edges.append((dotted_module, public_name, source_module, alias.name))
    return edges


def _apply_reexports(
    signatures: dict[str, dict[str, Any]], edges: list[tuple[str, str, str, str]]
) -> dict[str, dict[str, Any]]:
    """Fixed-point: copy every signature reachable through a re-export chain to its public path."""
    result = dict(signatures)
    changed = True
    while changed:
        changed = False
        for pub_mod, pub_name, src_mod, src_name in edges:
            pub_prefix = f"{pub_mod}.{pub_name}"
            src_prefix = f"{src_mod}.{src_name}"
            if pub_prefix in result:
                continue
            # Exact class/classmethod match, or a classmethod nested under the class.
            for key, sig in list(result.items()):
                if key == src_prefix or key.startswith(src_prefix + "."):
                    new_key = pub_prefix + key[len(src_prefix) :]
                    if new_key not in result:
                        result[new_key] = sig
                        changed = True
    return result


def _star_imports(path: Path) -> list[str]:
    """Return the absolute ``livekit.agents…`` modules a file star-imports (``from X import *``)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return []
    return [
        node.module
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.level == 0
        and node.module is not None
        and (node.module == "livekit.agents" or node.module.startswith("livekit.agents."))
        and any(alias.name == "*" for alias in node.names)
    ]


def _core_module_file(core_root: Path, dotted_module: str) -> Path | None:
    """Map ``livekit.agents.x.y`` to its file under ``livekit-agents/livekit`` (module or package)."""
    parts = dotted_module.split(".")[1:]  # drop the leading "livekit"
    module_file = core_root.joinpath(*parts).with_suffix(".py")
    if module_file.is_file():
        return module_file
    package_init = core_root.joinpath(*parts, "__init__.py")
    return package_init if package_init.is_file() else None


def _star_exported_classes(path: Path) -> list[str]:
    """Class names ``from <path's module> import *`` would bind: ``__all__``, else public classes."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    classes = [node.name for node in tree.body if isinstance(node, ast.ClassDef)]
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets)
            and isinstance(node.value, (ast.List, ast.Tuple))
        ):
            exported = {e.value for e in node.value.elts if isinstance(e, ast.Constant)}
            return [name for name in classes if name in exported]
    return [name for name in classes if not name.startswith("_")]


def _follow_star_import(
    shim_module: str, target_module: str, core_root: Path
) -> tuple[dict[str, dict[str, Any]], list[tuple[str, str, str, str]]]:
    """Snapshot one star-import target (one hop) and the edges that re-export its classes."""
    target_file = _core_module_file(core_root, target_module)
    if target_file is None:
        logger.warning("star import target not found in the clone: %s (from %s)", target_module, shim_module)
        return {}, []
    for nested in _star_imports(target_file):
        logger.warning("not following a second star-import hop: %s -> %s", target_module, nested)
    signatures = snapshot_file(target_file, target_module)
    edges = [(shim_module, name, target_module, name) for name in _star_exported_classes(target_file)]
    return signatures, edges


def _dotted_module_for(py_file: Path, package_root: Path) -> str:
    rel = py_file.relative_to(package_root).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def snapshot_source_tree(source: Path) -> dict[str, dict[str, Any]]:
    """Walk every `livekit-plugins-*` package plus core `livekit-agents/livekit/agents/inference`."""
    signatures: dict[str, dict[str, Any]] = {}
    edges: list[tuple[str, str, str, str]] = []

    roots: list[Path] = []
    plugin_root = source / "livekit-plugins"
    for pkg_dir in sorted(plugin_root.glob("livekit-plugins-*")):
        pkg_pkg_root = pkg_dir / "livekit"
        if pkg_pkg_root.exists():
            roots.append(pkg_pkg_root)

    core_root = source / "livekit-agents" / "livekit"
    if (core_root / "agents" / "inference").exists():
        roots.append(core_root)

    for pkg_root in roots:
        walk_root = (pkg_root / "agents" / "inference") if pkg_root == core_root else pkg_root
        for py_file in sorted(walk_root.rglob("*.py")):
            if "/tests/" in str(py_file) or py_file.name.startswith("test_"):
                continue
            dotted_module = f"livekit.{_dotted_module_for(py_file, pkg_root)}"
            signatures.update(snapshot_file(py_file, dotted_module))
            # Re-export edges only come from `__init__.py`'s public surface (every
            # package in this catalog re-exports through `__init__.py` alone) —
            # collecting them from every file too would pull in thousands of
            # irrelevant `from .log import logger`-style edges and make the
            # fixed-point pass below quadratic in the whole tree for no benefit.
            if py_file.name == "__init__.py":
                edges.extend(_reexport_edges(py_file, dotted_module))
            # A plugin module that is a `from livekit.agents… import *` shim (R-V4-55).
            if pkg_root != core_root:
                for target_module in _star_imports(py_file):
                    followed, star_edges = _follow_star_import(dotted_module, target_module, core_root)
                    for key, sig in followed.items():
                        signatures.setdefault(key, sig)
                    edges.extend(star_edges)

    return _apply_reexports(signatures, edges)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path, help="Path to the cloned livekit/agents tree")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("agent/tests/fixtures/plugin_signatures.json"),
        help="Output JSON fixture path",
    )
    args = parser.parse_args()

    signatures = snapshot_source_tree(args.source)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        json.dump(signatures, f, indent=2, sort_keys=True)
        f.write("\n")
    print(f"wrote {len(signatures)} signatures to {args.out}")


if __name__ == "__main__":
    main()
