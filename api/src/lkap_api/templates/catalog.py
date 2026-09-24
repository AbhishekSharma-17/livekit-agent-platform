"""The starter-template catalogue: loading, lookup and derived pack entries (docs/v4/TEMPLATES.md §3).

The catalogue is data shipped with the api: one directory per starter under
``lkap_api/templates/catalog/<id>/`` holding ``template.json``, an optional
``instructions.md`` (long prose kept out of JSON escapes) and ``seeds/*.md``
for knowledge seeds. Unlike packs there is no "being developed elsewhere"
case, so a malformed entry is a :class:`CatalogError` (a startup error when
the router module is imported), never a skipped entry.
"""

from __future__ import annotations

import importlib.resources
import json
from functools import lru_cache
from importlib.resources.abc import Traversable

from lkap_contracts.packs import PackManifest
from lkap_contracts.templates import StarterTemplate, TemplateChip, TemplateRequirements
from pydantic import ValidationError

from lkap_api.templates.seed import requirements_from_pipeline

#: The package whose ``catalog/`` directory holds the starters.
CATALOG_PACKAGE = "lkap_api.templates"
CATALOG_DIR = "catalog"

#: Prefix of the id of a pack's derived starter (D-V4-4).
DERIVED_PREFIX = "pack:"

#: Derived entries sort after every catalogue starter.
DERIVED_ORDER = 1000

_TAGLINE_MAX = 90
_NAME_MAX = 48


class CatalogError(ValueError):
    """A catalogue entry is malformed (the message names the directory and the problem)."""


def catalog_root() -> Traversable:
    """The ``catalog/`` directory inside the installed api package."""
    return importlib.resources.files(CATALOG_PACKAGE).joinpath(CATALOG_DIR)


def _load_entry(directory: Traversable) -> StarterTemplate:
    """Load and validate one catalogue directory.

    Raises:
        CatalogError: The directory breaks one of the loader rules of TEMPLATES §3.
    """
    name = directory.name
    template_file = directory.joinpath("template.json")
    if not template_file.is_file():
        raise CatalogError(f"catalogue entry '{name}' has no template.json")
    try:
        data = json.loads(template_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CatalogError(f"catalogue entry '{name}': template.json is not valid JSON ({exc.msg})") from exc
    if not isinstance(data, dict):
        raise CatalogError(f"catalogue entry '{name}': template.json must hold an object")
    instructions_file = directory.joinpath("instructions.md")
    if instructions_file.is_file():
        if "instructions" in data:
            raise CatalogError(
                f"catalogue entry '{name}' sets instructions in both template.json and instructions.md"
            )
        data["instructions"] = instructions_file.read_text(encoding="utf-8").strip()
    try:
        template = StarterTemplate.model_validate(data)
    except ValidationError as exc:
        raise CatalogError(f"catalogue entry '{name}' is not a valid StarterTemplate: {exc}") from exc
    if template.id != name:
        raise CatalogError(
            f"catalogue entry '{name}' has id '{template.id}'; the id must equal the directory"
        )
    for seed in template.kb_seeds:
        for file_name in seed.files:
            if not directory.joinpath("seeds", file_name).is_file():
                raise CatalogError(f"catalogue entry '{name}': seed file 'seeds/{file_name}' is missing")
    return template


def load_catalog_from(root: Traversable) -> tuple[StarterTemplate, ...]:
    """Load every starter under ``root``, ordered by ``order`` then ``id``.

    Args:
        root: A directory with one sub-directory per starter.

    Returns:
        The validated starters in gallery order.

    Raises:
        CatalogError: Any entry is malformed.
    """
    templates = [
        _load_entry(entry)
        for entry in root.iterdir()
        if entry.is_dir() and not entry.name.startswith(("_", "."))
    ]
    return tuple(sorted(templates, key=lambda t: (t.order, t.id)))


@lru_cache(maxsize=1)
def load_catalog() -> tuple[StarterTemplate, ...]:
    """The shipped catalogue in gallery order (cached; see :func:`clear_catalog_cache`)."""
    return load_catalog_from(catalog_root())


def clear_catalog_cache() -> None:
    """Drop the catalogue cache (tests, like ``packs.clear_manifest_cache``)."""
    load_catalog.cache_clear()


def get_template(template_id: str) -> StarterTemplate | None:
    """The catalogue starter with this id, or ``None`` (derived ids are never in the catalogue)."""
    return next((t for t in load_catalog() if t.id == template_id), None)


def template_root(template_id: str) -> Traversable:
    """The catalogue directory of a starter (its ``seeds/`` live underneath)."""
    return catalog_root().joinpath(template_id)


def _tagline(description: str) -> str:
    """The first sentence of a pack description, cut to the tagline limit."""
    first = description.strip().split(". ")[0].strip().rstrip(".")
    if len(first) <= _TAGLINE_MAX:
        return first
    return first[: _TAGLINE_MAX - 1].rstrip() + "…"


def _derived_chips(manifest: PackManifest) -> list[TemplateChip]:
    chips: list[TemplateChip] = []
    if manifest.tool_names:
        chips.append("code_tools")
    if manifest.kb_seeds:
        chips.append("knowledge_seeds")
    if manifest.capabilities.camera:
        chips.append("camera")
    if manifest.capabilities.screen_share:
        chips.append("screen_share")
    if manifest.capabilities.dtmf:
        chips.append("dtmf")
    if manifest.recommended_pipeline.image_gen is not None:
        chips.append("image_gen")
    return chips


def derived_template(manifest: PackManifest) -> StarterTemplate:
    """The starter a pack without a catalogue template gets (D-V4-4, R-V4-3).

    Every overlay field is ``None``: the manifest is the whole configuration,
    so seeding from it equals seeding from ``pack_id`` alone. Built without
    validation (the manifest is already a validated contract; a third-party
    pack id need not match the slug pattern).

    Args:
        manifest: An installed pack's manifest.

    Returns:
        ``pack:<id>``, category ``example``, chips and required keys computed from the manifest.
    """
    return StarterTemplate.model_construct(
        id=f"{DERIVED_PREFIX}{manifest.id}",
        name=manifest.name[:_NAME_MAX],
        tagline=_tagline(manifest.description),
        description=manifest.description,
        category="example",
        chips=_derived_chips(manifest),
        requires=TemplateRequirements(
            provider_keys=requirements_from_pipeline(manifest.recommended_pipeline)
        ),
        order=DERIVED_ORDER,
        pack_id=manifest.id,
    )


def available_templates(manifests: list[PackManifest]) -> list[tuple[StarterTemplate, PackManifest, bool]]:
    """The gallery: catalogue starters whose pack is installed, then derived pack entries.

    Args:
        manifests: The installed packs (``discover_manifests(LKAP_PACKS)``).

    Returns:
        ``(template, pack manifest, derived)`` in gallery order. A catalogue
        starter whose pack is missing is omitted (the caller logs it); a pack
        that no catalogue starter references gets its derived entry.
    """
    by_id = {manifest.id: manifest for manifest in manifests}
    out: list[tuple[StarterTemplate, PackManifest, bool]] = []
    referenced: set[str] = set()
    for template in load_catalog():
        referenced.add(template.pack_id)
        manifest = by_id.get(template.pack_id)
        if manifest is not None:
            out.append((template, manifest, False))
    for manifest in manifests:
        if manifest.id not in referenced:
            out.append((derived_template(manifest), manifest, True))
    return out


def missing_pack_templates(manifests: list[PackManifest]) -> list[StarterTemplate]:
    """Catalogue starters whose pack is not installed (for the ``template_pack_missing`` log)."""
    installed = {manifest.id for manifest in manifests}
    return [template for template in load_catalog() if template.pack_id not in installed]
