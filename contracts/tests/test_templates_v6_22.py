"""V6-22 (ask #105): starters that seed lookup tables, add kits, and set extraction, rules and tests."""

from typing import Any

import pytest
from pydantic import ValidationError

from lkap_contracts.export import EXPORTED_MODELS, build_combined_schema
from lkap_contracts.kits import KIT_ID_PATTERN, KIT_PREFIX_PATTERN
from lkap_contracts.templates import (
    _KIT_ID_PATTERN,
    _KIT_PREFIX_PATTERN,
    MAX_TEMPLATE_DATASET_SEEDS,
    MAX_TEMPLATE_KITS,
    DatasetSeed,
    StarterTemplate,
    TemplateKit,
)


def _template(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "claims_intake",
        "name": "Claims intake",
        "tagline": "Takes a first notice of loss",
        "description": "d",
        "category": "forms",
    }
    base.update(overrides)
    return base


_SEED: dict[str, Any] = {
    "name": "Demo · Policy directory",
    "file": "policy_directory.csv",
    "key_columns": [{"name": "policy_number"}, {"name": "policyholder_name"}],
}


def test_a_template_without_the_new_fields_leaves_them_empty() -> None:
    template = StarterTemplate.model_validate(_template())

    assert (template.extraction, template.rules, template.tests) == (None, [], [])
    assert (template.dataset_seeds, template.kits) == ([], [])


def test_a_template_with_seeds_kits_extraction_rules_and_tests_round_trips() -> None:
    template = StarterTemplate.model_validate(
        _template(
            extraction={"enabled": True, "fields": [{"name": "policy_number", "required": True}]},
            rules=[
                {"id": "r", "when": "var.policy_number is set", "then": [{"do": "status.set", "label": "Ok"}]}
            ],
            tests=[{"id": "fnol", "name": "FNOL", "persona_instructions": "You had a leak."}],
            dataset_seeds=[_SEED],
            kits=[{"kit_id": "record_lookup", "block_prefix": "policy", "dataset": _SEED["name"]}],
        )
    )

    again = StarterTemplate.model_validate_json(template.model_dump_json())

    assert again == template
    assert again.kits[0].add_test_case is True
    assert [column.type for column in again.dataset_seeds[0].key_columns] == ["string", "string"]


def test_a_kit_naming_an_unknown_dataset_seed_is_refused() -> None:
    with pytest.raises(ValidationError, match="which no dataset_seeds entry is"):
        StarterTemplate.model_validate(_template(kits=[{"kit_id": "record_lookup", "dataset": "Nope"}]))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("dataset_seeds", [_SEED, _SEED], "names must be unique"),
        (
            "dataset_seeds",
            [{**_SEED, "name": f"t{i}"} for i in range(MAX_TEMPLATE_DATASET_SEEDS + 1)],
            "at most",
        ),
        ("kits", [{"kit_id": "notify_escalate"}] * (MAX_TEMPLATE_KITS + 1), "at most"),
        (
            "tests",
            [
                {"id": "a", "name": "A", "persona_instructions": "p"},
                {"id": "a", "name": "B", "persona_instructions": "p"},
            ],
            "ids must be unique",
        ),
    ],
)
def test_template_bounds_are_enforced(field: str, value: Any, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        StarterTemplate.model_validate(_template(**{field: value}))


@pytest.mark.parametrize("file_name", ["../escape.csv", "policy.txt", "sub/dir.csv", ".hidden.csv"])
def test_a_dataset_seed_file_is_a_plain_csv_or_json_name(file_name: str) -> None:
    with pytest.raises(ValidationError):
        DatasetSeed.model_validate({**_SEED, "file": file_name})


@pytest.mark.parametrize("keys", [[], [{"name": "a"}, {"name": "a"}], [{"name": f"k{i}"} for i in range(9)]])
def test_a_dataset_seed_names_one_to_eight_distinct_key_columns(keys: list[dict[str, str]]) -> None:
    with pytest.raises(ValidationError):
        DatasetSeed.model_validate({**_SEED, "key_columns": keys})


def test_template_kit_patterns_equal_the_kit_contract() -> None:
    assert (_KIT_ID_PATTERN, _KIT_PREFIX_PATTERN) == (KIT_ID_PATTERN, KIT_PREFIX_PATTERN)
    with pytest.raises(ValidationError):
        TemplateKit.model_validate({"kit_id": "Record-Lookup"})


@pytest.mark.parametrize("name", ["DatasetSeed", "TemplateKit"])
def test_the_new_template_models_are_exported(name: str) -> None:
    assert name in EXPORTED_MODELS
    assert name in build_combined_schema()["properties"]
