"""Custom model ids: the id rule, per-workspace model records, capabilities, validation (V4-07).

docs/v4/CUSTOM-MODELS.md D-V4-23 (the id rule; unknown stays a warning, a
secret-looking value is refused without echo), D-V4-24 (``provider_models``
records and capability resolution) and the validation half of D-V4-26.

* :mod:`~lkap_api.custom_models.ids` — the contracts' rule re-exported, plus :func:`scrub`.
* :mod:`~lkap_api.custom_models.records` — ``provider_models`` reads, writes and catalog sightings.
* :mod:`~lkap_api.custom_models.capabilities` — declared → detected → catalog → registry.
* :mod:`~lkap_api.custom_models.validation` — the registered validator. Importing
  that module registers it; ``lkap_api.routers.providers`` does, so this package
  itself stays import-light (the catalog service imports :mod:`records`).

The "Test model" probes and route that fill ``detected``/``last_test_*`` are V4-08's.
"""
