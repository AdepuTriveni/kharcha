"""Teacher distillation and the review queue (PROJECT_SPEC §15.2).

The teacher (a larger local model through Ollama, or any ``Teacher``) labels every example
with the §10.3 schema. The review queue gets every disagreement with the proposed label
(server rules/teacher at export time), every example the teacher could not label, and a
random 10% sample. Reviewed labels (``kharcha-label review``) win when the dataset is built.
"""

import json
import random
import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

import httpx
from pydantic import ValidationError

from kharcha_ml.dataset.labels import Extraction, LabeledExample
from kharcha_ml.eval.metrics import FIELDS, field_equal

PROMPT_FILE = Path(__file__).resolve().parents[4] / "backend" / "prompts" / "parser" / "v1.md"
_SECTION = re.compile(r"^## (\w+)\s*$", re.M)


class Teacher(Protocol):
    @property
    def name(self) -> str: ...

    async def label(self, example: LabeledExample) -> Extraction | None: ...


def load_sections(path: Path = PROMPT_FILE) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        text = text.split("---", 2)[2]
    parts = _SECTION.split(text)
    return {parts[i]: parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}


def decode(raw: str) -> Extraction | None:
    text = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        data: Any = json.loads(text, parse_float=Decimal)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    for key in ("amount", "balanceAfter", "referenceId", "accountHint"):
        if isinstance(data.get(key), int | Decimal) and not isinstance(data.get(key), bool):
            data[key] = str(data[key])
    allowed = set(Extraction.model_fields) | {
        f.alias for f in Extraction.model_fields.values() if f.alias
    }
    try:
        return Extraction.model_validate(
            {k: v for k, v in data.items() if k in allowed or _camel(k) in allowed}
        )
    except ValidationError:
        return None


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(w.title() for w in rest)


@dataclass
class OllamaTeacher:
    model: str = "qwen2.5:7b"
    base_url: str = "http://localhost:11434"
    timeout_s: float = 120.0

    @property
    def name(self) -> str:
        return f"ollama/{self.model}#parser/v1"

    async def label(self, example: LabeledExample) -> Extraction | None:
        sections = load_sections()
        user = (
            sections["user"]
            .replace("{sender}", example.sender or "unknown")
            .replace("{source_app}", example.source_app or "unknown")
            .replace("{text}", example.text.replace(">>>", "> > >"))
        )
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            response = await client.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": 0},
                    "messages": [
                        {"role": "system", "content": sections["system"]},
                        {"role": "user", "content": user},
                    ],
                },
            )
            response.raise_for_status()
        return decode(str(response.json().get("message", {}).get("content", "")))


def agrees(a: Extraction, b: Extraction) -> bool:
    if a.is_transaction != b.is_transaction:
        return False
    return not a.is_transaction or all(field_equal(f, a, b) for f in FIELDS)


@dataclass(frozen=True)
class DistillResult:
    labeled: list[LabeledExample]
    review_queue: list[LabeledExample]
    disagreements: int
    failures: int


async def distill(
    examples: Sequence[LabeledExample],
    teacher: Teacher,
    sample_rate: float = 0.1,
    seed: int = 0,
) -> DistillResult:
    rng = random.Random(seed)
    labeled: list[LabeledExample] = []
    queue: list[LabeledExample] = []
    disagreements = failures = 0
    for example in examples:
        if example.reviewed:
            labeled.append(example)  # human labels are never overwritten
            continue
        try:
            label = await teacher.label(example)
        except httpx.HTTPError:
            label = None
        if label is None:
            failures += 1
            queue.append(example)
            labeled.append(example)
            continue
        out = example.model_copy(update={"label": label})
        labeled.append(out)
        if not agrees(label, example.label):
            disagreements += 1
            queue.append(out)
        elif rng.random() < sample_rate:
            queue.append(out)
    return DistillResult(labeled, queue, disagreements, failures)
