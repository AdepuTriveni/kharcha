import importlib

import pytest

SUBPACKAGES = ["dataset", "distill", "finetune", "eval", "export", "risk"]


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackage_imports(name: str) -> None:
    module = importlib.import_module(f"kharcha_ml.{name}")
    assert module.__doc__
