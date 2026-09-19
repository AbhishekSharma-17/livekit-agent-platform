"""Shared fixtures for the contracts test suite (no network, no vendor keys)."""

from pathlib import Path

import pytest

from lkap_contracts.export import default_output_dir


@pytest.fixture(scope="session")
def generated_dir() -> Path:
    """Return the committed ``contracts/generated`` directory."""
    return default_output_dir()
