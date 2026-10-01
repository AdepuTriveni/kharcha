"""Five-way parser comparison on ``gold_test`` (PROJECT_SPEC §15.5) -- ``kharcha-ml-eval``.

Variants: rules-only (imported from the backend's ``kharcha-eval parsing --parser rules`` JSON),
base model zero-shot, fine-tuned fp16, fine-tuned Q4 GGUF, teacher. Model variants run through
Ollama with the §10.3 prompt and JSON-schema constrained decoding.

    uv run kharcha-ml-eval data/processed/gold_test.jsonl \\
        --variant base=qwen2.5:0.5b --variant fp16=kharcha-parser:v1-f16 \\
        --variant q4=kharcha-parser:v1 --variant teacher=qwen2.5:7b \\
        --rules-report ../backend/reports/parsing/parsing-<ts>.json --out reports/
"""

import argparse
import asyncio
import json
import statistics
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx

from kharcha_ml.dataset.labels import Extraction, LabeledExample, read_jsonl
from kharcha_ml.distill.teacher import decode, load_sections
from kharcha_ml.eval.metrics import ParsingReport, evaluate

GATE_POINTS = 0.03  # all-fields EM >= teacher - 3 pts
GATE_AMOUNT = 0.99


class Predictor(Protocol):
    @property
    def name(self) -> str: ...

    async def predict(self, example: LabeledExample) -> str: ...


@dataclass
class OllamaPredictor:
    variant: str
    model: str
    base_url: str = "http://localhost:11434"
    constrained: bool = True
    timeout_s: float = 60.0

    @property
    def name(self) -> str:
        return self.variant

    async def predict(self, example: LabeledExample) -> str:
        sections = load_sections()
        user = (
            sections["user"]
            .replace("{sender}", example.sender or "unknown")
            .replace("{source_app}", example.source_app or "unknown")
            .replace("{text}", example.text.replace(">>>", "> > >"))
        )
        schema = Extraction.model_json_schema(by_alias=True)
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            response = await client.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "stream": False,
                    "format": schema if self.constrained else "json",
                    "options": {"temperature": 0},
                    "messages": [
                        {"role": "system", "content": sections["system"]},
                        {"role": "user", "content": user},
                    ],
                },
            )
            response.raise_for_status()
        return str(response.json().get("message", {}).get("content", ""))


@dataclass
class VariantResult:
    name: str
    report: ParsingReport
    valid_json: int = 0
    calls: int = 0
    latencies_ms: list[float] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        ordered = sorted(self.latencies_ms) or [0.0]
        p95 = ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))]
        return {
            "variant": self.name,
            **self.report.to_dict(),
            "jsonValidity": round(self.valid_json / self.calls, 4) if self.calls else None,
            "latencyMs": {"p50": round(statistics.median(ordered), 1), "p95": round(p95, 1)},
        }


async def run_variant(predictor: Predictor, gold: Sequence[LabeledExample]) -> VariantResult:
    result = VariantResult(predictor.name, ParsingReport())
    pairs: list[tuple[str | None, Extraction, Extraction | None]] = []
    for example in gold:
        start = time.perf_counter()
        try:
            raw = await predictor.predict(example)
        except httpx.HTTPError:
            raw = ""
        result.latencies_ms.append((time.perf_counter() - start) * 1000)
        result.calls += 1
        predicted = decode(raw)
        result.valid_json += predicted is not None
        pairs.append((example.sender or example.source_app, example.label, predicted))
    result.report = evaluate(pairs)
    return result


def gate(variants: dict[str, dict[str, Any]], candidate: str = "q4") -> dict[str, Any]:
    """§15.5 promotion gate for ``candidate`` against ``teacher``."""
    if candidate not in variants or "teacher" not in variants:
        return {"candidate": candidate, "passed": False, "reason": "missing variant"}
    cand, teacher = variants[candidate], variants["teacher"]
    em_ok = cand["allFieldsExactMatch"] >= teacher["allFieldsExactMatch"] - GATE_POINTS
    amount_ok = cand["fieldExactMatch"]["amount"] >= GATE_AMOUNT
    return {
        "candidate": candidate,
        "passed": bool(em_ok and amount_ok),
        "allFieldsEmVsTeacher": round(
            cand["allFieldsExactMatch"] - teacher["allFieldsExactMatch"], 4
        ),
        "amountEm": cand["fieldExactMatch"]["amount"],
    }


def to_markdown(variants: dict[str, dict[str, Any]], verdict: dict[str, Any], n: int) -> str:
    cols = list(variants)

    def row(label: str, get: Any) -> str:
        return f"| {label} | " + " | ".join(get(variants[c]) for c in cols) + " |"

    lines = [
        f"# Parser eval on gold_test ({n} examples)",
        "",
        "| Metric | " + " | ".join(cols) + " |",
        "|---|" + "---|" * len(cols),
        row("All-fields EM", lambda v: f"{v['allFieldsExactMatch']:.3f}"),
        row("isTransaction F1", lambda v: f"{v['isTransaction']['f1']:.3f}"),
        row("EM amount", lambda v: f"{v['fieldExactMatch']['amount']:.3f}"),
        row("EM direction", lambda v: f"{v['fieldExactMatch']['direction']:.3f}"),
        row("EM status", lambda v: f"{v['fieldExactMatch']['status']:.3f}"),
        row("EM merchant", lambda v: f"{v['fieldExactMatch']['merchant_raw']:.3f}"),
        row("EM reference", lambda v: f"{v['fieldExactMatch']['reference_id']:.3f}"),
        row(
            "JSON validity",
            lambda v: "-" if v.get("jsonValidity") is None else f"{v['jsonValidity']:.3f}",
        ),
        row("Latency p50 ms (server)", lambda v: str(v.get("latencyMs", {}).get("p50", "-"))),
        "",
        f"**Promotion gate ({verdict['candidate']})**: {'PASS' if verdict['passed'] else 'FAIL'}"
        + (
            f" — Δ all-fields EM vs teacher {verdict.get('allFieldsEmVsTeacher')}, amount EM {verdict.get('amountEm')}"
            if "amountEm" in verdict
            else f" — {verdict.get('reason')}"
        ),
        "",
        "Phone latency/RAM (p50/p95) and model size are added by the Android benchmark (W12).",
    ]
    return "\n".join(lines) + "\n"


async def compare(
    gold: Sequence[LabeledExample], predictors: Sequence[Predictor], rules: dict[str, Any] | None
) -> dict[str, dict[str, Any]]:
    variants: dict[str, dict[str, Any]] = {}
    if rules is not None:
        variants["rules"] = {"variant": "rules", **{k: rules[k] for k in rules if k != "subject"}}
    for predictor in predictors:
        variants[predictor.name] = (await run_variant(predictor, gold)).to_dict()
    return variants


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - needs Ollama
    parser = argparse.ArgumentParser(prog="kharcha-ml-eval")
    parser.add_argument("gold", type=Path)
    parser.add_argument("--variant", action="append", default=[], help="name=ollama-model")
    parser.add_argument("--rules-report", type=Path, default=None)
    parser.add_argument("--candidate", default="q4")
    parser.add_argument("--ollama", default="http://localhost:11434")
    parser.add_argument("--out", type=Path, default=Path("reports"))
    args = parser.parse_args(argv)
    gold = list(read_jsonl(args.gold))
    predictors = [
        OllamaPredictor(name, model, args.ollama)
        for name, model in (v.split("=", 1) for v in args.variant)
    ]
    rules = json.loads(args.rules_report.read_text(encoding="utf-8")) if args.rules_report else None
    variants = asyncio.run(compare(gold, predictors, rules))
    verdict = gate(variants, args.candidate)
    args.out.mkdir(parents=True, exist_ok=True)
    report = {"n": len(gold), "variants": variants, "gate": verdict}
    (args.out / "eval_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (args.out / "eval_report.md").write_text(
        to_markdown(variants, verdict, len(gold)), encoding="utf-8"
    )
    print(to_markdown(variants, verdict, len(gold)))
    return 0 if verdict["passed"] else 1


def run() -> None:  # pragma: no cover
    raise SystemExit(main())
