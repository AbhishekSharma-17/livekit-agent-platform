"""Vendor catalog adapters, caching and workspace-enablement validation (V2-06).

Importing this package registers the workspace-enablement validator into
`lkap_api.config_service.VALIDATORS` (see :mod:`lkap_api.catalogs.validation`)
as a side effect — `lkap_api.routers.providers` imports this package for
exactly that reason, so no other module needs to know it exists.
"""

from __future__ import annotations

from lkap_api.catalogs import validation as _validation  # noqa: F401 - registers the validator
from lkap_api.catalogs.adapters import ADAPTERS, get_adapter
from lkap_api.catalogs.base import (
    CatalogAdapter,
    CatalogAdapterError,
    UnsupportedCatalogKind,
    normalize_secrets,
)
from lkap_api.catalogs.service import get_catalog, static_items

__all__ = [
    "ADAPTERS",
    "CatalogAdapter",
    "CatalogAdapterError",
    "UnsupportedCatalogKind",
    "get_adapter",
    "get_catalog",
    "normalize_secrets",
    "static_items",
]
