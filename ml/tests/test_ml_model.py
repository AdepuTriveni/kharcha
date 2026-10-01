import json
import re
from pathlib import Path

import pytest

from kharcha_ml.dataset.labels import Extraction, LabeledExample, read_jsonl
from kharcha_ml.eval.parser import gate, run_variant, to_markdown
from kharcha_ml.export.gguf import manifest_for, modelfile, sha256_file
from kharcha_ml.finetune.train import Hyper, load_split, target_json, to_record

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "samples" / "parsing_sample.jsonl"


def _examples() -> list[LabeledExample]:
    return list(read_jsonl(SAMPLE))


def test_target_json_is_canonical() -> None:
    first = _examples()[0]
    target = target_json(first.label)
    data = json.loads(target)
    assert list(data)[:3] == ["isTransaction", "amount", "direction"]
    assert data["amount"] == "349.00"
    assert data["balanceAfter"] is None  # nulls kept so the model learns "unknown"
    assert target_json(Extraction(is_transaction=False)) == '{"isTransaction":false}'
    assert " " not in target.replace("zomato@hdfcbank", "")[:20]


def test_records_use_the_server_prompt_and_completion_only() -> None:
    record = to_record(_examples()[0])
    assert record["prompt"][0]["role"] == "system"
    assert "Extract ONE financial transaction" in record["prompt"][0]["content"]
    assert "AX-HDFCBK" in record["prompt"][1]["content"]
    assert record["completion"][0]["role"] == "assistant"
    json.loads(record["completion"][0]["content"])


def test_gold_is_never_loaded_for_training(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="never used for training"):
        load_split(tmp_path, "gold_test")
    assert Hyper().rank == 16
    assert Hyper().alpha == 32


class Perfect:
    name = "perfect"

    async def predict(self, example: LabeledExample) -> str:
        return example.label.model_dump_json(by_alias=True)


class Junk:
    name = "junk"

    async def predict(self, example: LabeledExample) -> str:
        return "sorry, I cannot"


async def test_variants_and_promotion_gate() -> None:
    gold = _examples()
    perfect = (await run_variant(Perfect(), gold)).to_dict()
    junk = (await run_variant(Junk(), gold)).to_dict()
    assert perfect["allFieldsExactMatch"] == 1.0
    assert perfect["jsonValidity"] == 1.0
    assert junk["jsonValidity"] == 0.0
    assert junk["isTransaction"]["recall"] == 0.0

    variants = {"teacher": perfect, "q4": perfect, "base": junk}
    verdict = gate(variants)
    assert verdict["passed"]
    assert not gate({"teacher": perfect, "q4": junk})["passed"]
    assert gate({"q4": perfect})["reason"] == "missing variant"
    md = to_markdown(variants, verdict, len(gold))
    assert "PASS" in md
    assert "| All-fields EM | 1.000 | 1.000 | 0.333 |" in md  # junk still "rejects" negatives


def test_manifest_and_modelfile(tmp_path: Path) -> None:
    gguf = tmp_path / "kharcha-parser-v1-q4_k_m.gguf"
    gguf.write_bytes(b"GGUF" + bytes(100))
    manifest = manifest_for(gguf, "v1", "Qwen/Qwen2.5-0.5B-Instruct", "Q4_K_M")
    assert manifest.sha256 == sha256_file(gguf)
    assert manifest.size_bytes == 104
    assert json.loads(manifest.to_json())["minRamMb"] == 3000
    text = modelfile(gguf.name, 'Say """hi"""')
    assert text.startswith("FROM ./kharcha-parser-v1-q4_k_m.gguf")
    assert "PARAMETER temperature 0" in text
    assert text.count('"""') == 2


def _grammar_regex() -> re.Pattern[str]:
    """Python mirror of parser.gbnf."""
    s = r'"(?:[^"\\\x00-\x1f]|\\["\\/bfnrt])*"'
    sn = rf"(?:{s}|null)"
    pos = (
        r'\{"isTransaction":true,"amount":'
        + s
        + r',"direction":"(?:DEBIT|CREDIT)"'
        + r',"channel":"(?:UPI|CARD|ATM|NETBANKING|WALLET|CASH|UNKNOWN)"'
        + r',"status":"(?:SUCCESS|FAILED|PENDING|REVERSED|REFUND_INITIATED)"'
        + rf',"merchantRaw":{sn},"counterpartyVpa":{sn},"referenceId":{sn},"accountHint":{sn}'
        + rf',"balanceAfter":{sn},"promisedRefundDays":(?:[0-9][0-9]?|null)\}}'
    )
    return re.compile(rf'^(?:\{{"isTransaction":false\}}|{pos})$')


def test_every_training_target_fits_the_device_grammar() -> None:
    """The phone decodes with parser.gbnf; targets outside it could never be produced."""
    from kharcha_ml.dataset.synthetic import generate

    grammar = Path(__file__).resolve().parents[1] / "src/kharcha_ml/export/parser.gbnf"
    text = grammar.read_text(encoding="utf-8")
    assert "root ::=" in text
    pattern = _grammar_regex()
    for example in [*generate(1500, seed=4), *_examples()]:
        target = target_json(example.label)
        assert pattern.match(target), target
