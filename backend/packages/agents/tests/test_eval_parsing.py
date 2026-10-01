import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from kharcha_agents.evals import cli
from kharcha_agents.evals.parsing import run_parsing, to_extraction, to_raw_event
from kharcha_ml.dataset.labels import LabeledExample, read_jsonl
from kharcha_processor.extraction import ExtractionResult
from kharcha_processor.parser import parse_raw_event
from kharcha_processor.teacher import BadModelOutputError

SAMPLE = Path(__file__).resolve().parents[4] / "ml" / "data" / "samples" / "parsing_sample.jsonl"


class LabelTeacher:
    """Answers with the gold label (a perfect teacher), optionally broken for some texts."""

    model_version = "label-teacher#parser/v1"

    def __init__(
        self, examples: list[LabeledExample], broken: frozenset[str] = frozenset()
    ) -> None:
        self._labels = {e.text: e.label for e in examples}
        self._broken = broken

    async def extract(
        self, *, sender: str | None, source_app: str | None, text: str
    ) -> ExtractionResult:
        if text in self._broken:
            raise BadModelOutputError("JSONDecodeError")
        return ExtractionResult.model_validate(self._labels[text].model_dump(exclude_none=True))


def _examples() -> list[LabeledExample]:
    return list(read_jsonl(SAMPLE))


async def test_perfect_teacher_scores_full_marks() -> None:
    examples = _examples()
    result = await run_parsing(examples, LabelTeacher(examples))
    metrics = result.metrics()
    assert metrics["isTransaction"] == {"precision": 1.0, "recall": 1.0, "f1": 1.0}
    assert metrics["allFieldsExactMatch"] == 1.0
    assert metrics["jsonValidity"] == 1.0
    assert result.outcomes["TRANSACTION"] == sum(e.label.is_transaction for e in examples)
    # OTP and promo never reach the model (pre-filter).
    assert result.outcomes["DROPPED_OTP"] == 1
    assert result.model_calls < len(examples)


async def test_bad_model_output_counts_as_miss_and_lowers_validity() -> None:
    examples = _examples()
    first = examples[0]
    result = await run_parsing(examples, LabelTeacher(examples, broken=frozenset({first.text})))
    assert result.report.fn == 1
    assert result.outcomes["FAILED_BAD_MODEL_OUTPUT"] == 1
    assert result.json_validity() == pytest.approx(1 - 1 / result.model_calls)
    assert "FAILED_BAD_MODEL_OUTPUT | 1" in result.markdown("t")


async def test_payload_round_trips_to_extraction() -> None:
    examples = _examples()
    first = examples[0]
    payload = await parse_raw_event(to_raw_event(first), LabelTeacher(examples))
    extraction = to_extraction(payload)  # type: ignore[arg-type]
    assert extraction.amount == "349.00"
    assert extraction.account_hint == "1234"


def test_cli_writes_json_and_markdown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    examples = _examples()

    def fake_teacher(_: Any) -> LabelTeacher:
        return LabelTeacher(examples)

    monkeypatch.setattr(cli, "TeacherLLM", fake_teacher)
    out = tmp_path / "reports"
    res = CliRunner().invoke(
        cli.app, ["parsing", str(SAMPLE), "--out-dir", str(out), "--no-record"]
    )
    assert res.exit_code == 0, res.output
    (report,) = out.glob("parsing-*.json")
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["dataset"] == "parsing_sample.jsonl"
    assert data["subject"] == "label-teacher#parser/v1"
    assert report.with_suffix(".md").exists()
    assert "All-fields exact match" in res.output


def test_later_suites_exit_nonzero() -> None:
    assert CliRunner().invoke(cli.app, ["ask"]).exit_code == 2
