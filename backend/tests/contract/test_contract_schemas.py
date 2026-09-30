"""Exported JSON Schemas must match the Pydantic models (ADR-006)."""

import json
from pathlib import Path

import jsonschema
import pytest

from kharcha_common.schemas import DEFAULT_OUT, build_schemas, render

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"


def test_default_out_is_backend_schemas() -> None:
    assert DEFAULT_OUT == SCHEMA_DIR


@pytest.mark.parametrize(("name", "schema"), sorted(build_schemas().items()))
def test_exported_schema_is_current(name: str, schema: dict[str, object]) -> None:
    path = SCHEMA_DIR / f"{name}.schema.json"
    assert path.exists(), "run: uv run python -m kharcha_common.schemas export"
    assert path.read_text(encoding="utf-8") == render(schema), (
        f"{path.name} is stale; run: uv run python -m kharcha_common.schemas export"
    )
    jsonschema.Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))
