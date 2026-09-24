"""Starter templates: data-only presets layered on a pack (docs/v4/TEMPLATES.md).

* :mod:`lkap_api.templates.catalog` loads the shipped catalogue (``catalog/<id>/``)
  and derives a starter for every pack without one.
* :mod:`lkap_api.templates.seed` turns a starter into an ``AgentConfig`` through
  the one pack seeding rule, and creates its tool rows.
* :mod:`lkap_api.templates.router` serves ``GET /v1/templates``.
"""
