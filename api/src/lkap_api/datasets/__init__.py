"""Datasets: read-only lookup tables for the ``dataset`` tool kind (V6-16, D-V6-27).

* :mod:`~lkap_api.datasets.parse` — a CSV or JSON upload read into columns and rows;
* :mod:`~lkap_api.datasets.normalise` — how a key cell becomes a lookup value;
* :mod:`~lkap_api.datasets.service` — create, import (the ``dataset_import`` job), look up,
  preview, export and delete, every read scoped to a workspace;
* :mod:`~lkap_api.datasets.validation` — the agent-config checks, registered on import.
"""

from lkap_api.datasets import validation as _validation  # noqa: F401 - registers the validator
