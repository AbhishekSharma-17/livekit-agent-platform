"""V6-16 (D-V6-27): the dataset models and the ``dataset`` tool kind."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from lkap_contracts.api_models import ToolCreate
from lkap_contracts.datasets import (
    MAX_DATASET_KEY_CHARS,
    MAX_DATASET_LOOKUP_ROWS,
    DatasetLookupIn,
    InternalDatasetLookupIn,
)
from lkap_contracts.export import EXPORTED_MODELS
from lkap_contracts.tool_context import placeholder_issues
from lkap_contracts.tools import DatasetToolDefinition, ToolDefinition

_ADAPTER: TypeAdapter[ToolDefinition] = TypeAdapter(ToolDefinition)
DATASET_ID = "0123456789abcdef0123456789abcdef"


def _definition(**overrides: object) -> dict[str, object]:
    return {
        "kind": "dataset",
        "name": "lookup_policy",
        "description": "Find a policy by the caller's phone number.",
        "dataset_id": DATASET_ID,
        "key_columns": ["phone"],
        **overrides,
    }


def test_dataset_definition_parses_through_the_tool_union() -> None:
    parsed = _ADAPTER.validate_python(_definition())

    assert isinstance(parsed, DatasetToolDefinition)
    assert parsed.match == "exact"
    assert parsed.max_rows == 5
    assert parsed.return_columns == []


def test_tool_create_accepts_the_dataset_kind() -> None:
    created = ToolCreate(kind="dataset", name="lookup_policy", definition=_definition())  # type: ignore[arg-type]

    assert created.definition.kind == "dataset"


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"key_columns": []}, "at least 1"),
        ({"key_columns": ["Phone Number"]}, "not a dataset column name"),
        ({"key_columns": ["phone", "phone"]}, "twice"),
        ({"max_rows": MAX_DATASET_LOOKUP_ROWS + 1}, "less than or equal"),
        ({"pinned_arguments": {"email": "x"}}, "outside key_columns"),
        ({"pinned_arguments": {"phone": "{{ ctx.nope }}"}}, "not a session value"),
        ({"return_columns": ["Bad Name"]}, "not a dataset column name"),
    ],
)
def test_dataset_definition_refuses_bad_settings(overrides: dict[str, object], fragment: str) -> None:
    with pytest.raises(ValidationError, match=fragment):
        DatasetToolDefinition.model_validate(_definition(**overrides))


@pytest.mark.parametrize(
    "dataset_id", ["../ds/lookup", "d1/../x", "0123456789abcdef0123456789ABCDEF", "d1", "", "a" * 64]
)
def test_a_dataset_id_with_a_slash_is_refused_by_the_contract(dataset_id: str) -> None:
    """V6-21 (S6-15): the worker puts the id in an api path, so only a ``uuid4().hex`` passes."""
    with pytest.raises(ValidationError, match="dataset_id"):
        DatasetToolDefinition.model_validate(_definition(dataset_id=dataset_id))


def test_pinned_caller_phone_is_a_valid_placeholder() -> None:
    definition = DatasetToolDefinition.model_validate(
        _definition(pinned_arguments={"phone": "{{ ctx.caller_phone }}"}, requires_vars=["policy_number"])
    )

    assert placeholder_issues(definition) == []


def test_placeholder_issues_checks_a_stored_dataset_row() -> None:
    issues = placeholder_issues(
        _definition(pinned_arguments={"phone": "{{ ctx.unknown }}"}, bindings=[{"path": "/0", "to": "link"}])
    )

    fields = {issue.field for issue in issues}
    assert "pinned_arguments.phone" in fields
    assert "bindings[0].to" in fields


def test_lookup_values_are_bounded() -> None:
    with pytest.raises(ValidationError, match="longer than"):
        DatasetLookupIn(keys={"phone": "9" * (MAX_DATASET_KEY_CHARS + 1)})
    with pytest.raises(ValidationError):
        DatasetLookupIn(keys={})
    with pytest.raises(ValidationError):
        InternalDatasetLookupIn(keys={"phone": "1"})  # type: ignore[call-arg]


def test_dataset_models_are_exported() -> None:
    for name in ("DatasetOut", "DatasetPage", "DatasetLookupIn", "DatasetLookupOut", "DatasetToolDefinition"):
        assert name in EXPORTED_MODELS
