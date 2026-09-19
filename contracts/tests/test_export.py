"""The committed ``generated/`` tree must match what ``export.py`` produces.

The JSON half runs fully offline. The TypeScript half needs pnpm (and a network for
``pnpm dlx``), so its byte-for-byte diff is marked ``slow`` and skipped when pnpm is
absent; an offline structural check still guards against a stale ``.d.ts``.
"""

import json
from pathlib import Path

import pytest

from lkap_contracts.export import (
    COMBINED_TITLE,
    EXPORTED_MODELS,
    EXPORTED_UNIONS,
    build_combined_schema,
    build_providers_document,
    build_schema_documents,
    default_output_dir,
    generate_typescript,
    prepare_for_typescript,
    typescript_available,
    write_json_outputs,
)
from lkap_contracts.providers import REGISTRY

TS_RELATIVE_PATH = Path("ts") / "lkap-contracts.d.ts"


def _exported_names() -> list[str]:
    return [*EXPORTED_MODELS, *EXPORTED_UNIONS]


def test_generated_directory_exists(generated_dir: Path) -> None:
    assert generated_dir.is_dir(), "run `uv run python -m lkap_contracts.export`"


def test_generated_up_to_date(generated_dir: Path, tmp_path: Path) -> None:
    """Re-export the JSON artefacts to a temp dir and diff against the committed tree."""
    write_json_outputs(tmp_path)

    fresh = {p.relative_to(tmp_path) for p in tmp_path.rglob("*.json")}
    committed = {p.relative_to(generated_dir) for p in generated_dir.rglob("*.json")}
    assert fresh == committed, "generated JSON file set is stale"

    stale: list[str] = []
    for relative in sorted(fresh):
        if (tmp_path / relative).read_text(encoding="utf-8") != (generated_dir / relative).read_text(
            encoding="utf-8"
        ):
            stale.append(str(relative))
    assert not stale, f"stale generated files: {stale}"


def test_providers_json_lists_every_registry_entry(generated_dir: Path) -> None:
    document = json.loads((generated_dir / "providers.json").read_text(encoding="utf-8"))
    assert set(document) == {"v", "providers"}, "providers.json must match ProvidersResponse exactly"
    assert document["v"] == 1
    assert [p["id"] for p in document["providers"]] == [spec.id for spec in REGISTRY]


def test_providers_json_lists_at_least_seventeen_mvp_providers(generated_dir: Path) -> None:
    document = json.loads((generated_dir / "providers.json").read_text(encoding="utf-8"))
    mvp = [p for p in document["providers"] if p["status"] == "mvp"]
    assert len(mvp) >= 17


def test_providers_json_never_contains_secret_values(generated_dir: Path) -> None:
    """The registry describes secret *fields*; it must never carry secret values."""
    text = (generated_dir / "providers.json").read_text(encoding="utf-8")
    document = json.loads(text)
    for provider in document["providers"]:
        for field in provider["secret_fields"]:
            assert field["type"] == "secret"
            assert field["default"] is None


def test_providers_document_is_deterministic() -> None:
    assert build_providers_document() == build_providers_document()


def test_a_schema_file_exists_for_every_exported_model(generated_dir: Path) -> None:
    schemas_dir = generated_dir / "schemas"
    on_disk = {p.name.removesuffix(".schema.json") for p in schemas_dir.glob("*.schema.json")}
    assert on_disk == set(_exported_names())


@pytest.mark.parametrize("name", _exported_names())
def test_each_schema_file_is_valid_json_with_an_id(generated_dir: Path, name: str) -> None:
    schema = json.loads((generated_dir / "schemas" / f"{name}.schema.json").read_text(encoding="utf-8"))
    assert schema["$id"] == f"lkap-contracts/{name}.schema.json"
    assert schema["title"] == name


def test_schema_documents_cover_the_models_contracts_requires() -> None:
    required = {
        "AgentConfig",
        "ResolvedAgentConfig",
        "DispatchMetadata",
        "UiSnapshot",
        "UiPatch",
        "ActivityEvent",
        "PackManifest",
        "ToolDefinition",
    }
    assert required <= set(build_schema_documents())


def test_combined_schema_references_every_exported_name() -> None:
    combined = build_combined_schema()
    assert combined["title"] == COMBINED_TITLE
    for name in _exported_names():
        assert combined["properties"][name] == {"$ref": f"#/$defs/{name}"}
        assert name in combined["$defs"]


def test_typescript_preparation_leaves_no_dangling_references() -> None:
    prepared = prepare_for_typescript(build_combined_schema())
    definitions = prepared["definitions"]
    missing: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str):
                target = ref.removeprefix("#/definitions/")
                if target not in definitions:
                    missing.append(ref)
                assert set(node) == {"$ref"}, f"$ref must stand alone, got {sorted(node)}"
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(prepared)
    assert not missing, f"dangling refs: {missing}"


def test_typescript_preparation_titles_only_real_models() -> None:
    prepared = prepare_for_typescript(build_combined_schema())
    for name, schema in prepared["definitions"].items():
        assert schema["title"] == name
        for key in ("properties", "items"):
            nested = schema.get(key)
            if isinstance(nested, dict):
                assert all("title" not in sub for sub in nested.values() if isinstance(sub, dict)), (
                    f"{name}.{key} leaks a per-property title into TypeScript"
                )


def test_committed_typescript_exists_and_declares_ui_state(generated_dir: Path) -> None:
    text = (generated_dir / TS_RELATIVE_PATH).read_text(encoding="utf-8")
    assert "export interface UiState" in text


@pytest.mark.parametrize("name", _exported_names())
def test_committed_typescript_declares_every_exported_name(generated_dir: Path, name: str) -> None:
    """Offline staleness guard: a new contract model must show up in the .d.ts."""
    text = (generated_dir / TS_RELATIVE_PATH).read_text(encoding="utf-8")
    assert f"export interface {name} " in text or f"export type {name} " in text


def test_committed_typescript_has_no_duplicate_declarations(generated_dir: Path) -> None:
    text = (generated_dir / TS_RELATIVE_PATH).read_text(encoding="utf-8")
    declared = [
        line.split()[2]
        for line in text.splitlines()
        if line.startswith(("export interface ", "export type "))
    ]
    assert len(declared) == len(set(declared))
    suffixed = [name for name in declared if name[-1].isdigit()]
    assert not suffixed, f"duplicate-derived declarations: {suffixed}"


@pytest.mark.slow
def test_generated_typescript_up_to_date(generated_dir: Path, tmp_path: Path) -> None:
    """Regenerate the .d.ts with pnpm and diff it byte for byte."""
    if not typescript_available():
        pytest.skip("pnpm is not installed; cannot regenerate TypeScript")
    target = tmp_path / TS_RELATIVE_PATH
    if not generate_typescript(build_combined_schema(), target):
        pytest.skip("json-schema-to-typescript is unavailable (offline?)")
    assert target.read_text(encoding="utf-8") == (generated_dir / TS_RELATIVE_PATH).read_text(
        encoding="utf-8"
    )


def test_default_output_dir_points_at_the_committed_tree() -> None:
    assert default_output_dir().name == "generated"
    assert (default_output_dir().parent / "pyproject.toml").is_file()


def _interface_body(text: str, name: str) -> str:
    """Return the source between ``export interface <name> {`` and its closing ``}``."""
    start = text.index(f"export interface {name} {{")
    end = text.index("\n}", start)
    return text[start:end]


def test_ts_any_fields_are_unknown_not_index_objects(generated_dir: Path) -> None:
    """D-W2-3: an empty/``Any`` property schema must become ``unknown``, not ``{[k: string]: unknown}``."""
    text = (generated_dir / TS_RELATIVE_PATH).read_text(encoding="utf-8")
    assert "value?: unknown;" in _interface_body(text, "UiPatchOp")
    assert "details?: unknown;" in _interface_body(text, "ErrorBody")
