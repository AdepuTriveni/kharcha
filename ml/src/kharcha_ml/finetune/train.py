"""LoRA SFT of a small instruct model on the parser task (PROJECT_SPEC §15.4).

Prompt = the §10.3 extraction prompt (same file the server uses), target = canonical JSON
only, loss on target tokens only. Notebooks (Kaggle/Colab) only call :func:`train` so a run
is reproducible from the package. Heavy deps live in the ``train`` extra:

    uv sync --extra train
    uv run python -m kharcha_ml.finetune.train --base Qwen/Qwen2.5-0.5B-Instruct \\
        --data data/processed --out runs/qwen05-lora
"""

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from kharcha_ml.dataset.labels import Extraction, LabeledExample, read_jsonl
from kharcha_ml.distill.teacher import load_sections

TARGET_FIELDS = (
    "isTransaction",
    "amount",
    "direction",
    "channel",
    "status",
    "merchantRaw",
    "counterpartyVpa",
    "referenceId",
    "accountHint",
    "balanceAfter",
    "promisedRefundDays",
)


@dataclass(frozen=True)
class Hyper:
    """§15.4 starting point."""

    rank: int = 16
    alpha: int = 32
    dropout: float = 0.05
    lr: float = 2e-4
    epochs: int = 3
    max_seq: int = 384
    batch_size: int = 8
    grad_accum: int = 2
    warmup_ratio: float = 0.03
    seed: int = 42


def target_json(label: Extraction) -> str:
    """Canonical target: fixed key order, nulls kept for transactions, compact."""
    data = label.model_dump(by_alias=True)
    if not label.is_transaction:
        return '{"isTransaction":false}'
    return json.dumps(
        {k: data.get(k) for k in TARGET_FIELDS}, ensure_ascii=False, separators=(",", ":")
    )


def prompt_messages(example: LabeledExample) -> list[dict[str, str]]:
    sections = load_sections()
    user = (
        sections["user"]
        .replace("{sender}", example.sender or "unknown")
        .replace("{source_app}", example.source_app or "unknown")
        .replace("{text}", example.text.replace(">>>", "> > >"))
    )
    return [{"role": "system", "content": sections["system"]}, {"role": "user", "content": user}]


def to_record(example: LabeledExample) -> dict[str, Any]:
    """TRL prompt/completion record (conversational): loss only on the completion."""
    return {
        "prompt": prompt_messages(example),
        "completion": [{"role": "assistant", "content": target_json(example.label)}],
    }


def load_split(data_dir: Path, name: str) -> list[dict[str, Any]]:
    if name == "gold_test":
        raise ValueError("gold_test is never used for training (rule 8)")
    return [to_record(e) for e in read_jsonl(data_dir / f"{name}.jsonl")]


def train(  # pragma: no cover - needs a GPU
    base: str, data_dir: Path, out: Path, hyper: Hyper | None = None
) -> dict[str, Any]:
    hyper = hyper or Hyper()
    import torch
    from datasets import Dataset
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from trl import SFTConfig, SFTTrainer

    out.mkdir(parents=True, exist_ok=True)
    tokenizer = AutoTokenizer.from_pretrained(base)
    bf16 = torch.cuda.is_available() and torch.cuda.is_bf16_supported()
    model = AutoModelForCausalLM.from_pretrained(
        base, torch_dtype=torch.bfloat16 if bf16 else torch.float16
    )
    config = SFTConfig(
        output_dir=str(out),
        num_train_epochs=hyper.epochs,
        learning_rate=hyper.lr,
        per_device_train_batch_size=hyper.batch_size,
        gradient_accumulation_steps=hyper.grad_accum,
        warmup_ratio=hyper.warmup_ratio,
        max_length=hyper.max_seq,
        completion_only_loss=True,
        bf16=bf16,
        fp16=not bf16,
        eval_strategy="epoch",
        save_strategy="epoch",
        load_best_model_at_end=True,
        logging_steps=20,
        seed=hyper.seed,
        report_to=[],
    )
    trainer = SFTTrainer(
        model=model,
        args=config,
        train_dataset=Dataset.from_list(load_split(data_dir, "train")),
        eval_dataset=Dataset.from_list(load_split(data_dir, "val")),
        processing_class=tokenizer,
        peft_config=LoraConfig(
            r=hyper.rank,
            lora_alpha=hyper.alpha,
            lora_dropout=hyper.dropout,
            target_modules="all-linear",
            task_type="CAUSAL_LM",
        ),
    )
    result = trainer.train()
    trainer.save_model(str(out / "adapter"))
    tokenizer.save_pretrained(str(out / "adapter"))
    metrics = {
        "base": base,
        "hyper": asdict(hyper),
        "train": result.metrics,
        "eval": trainer.evaluate(),
        "dataCard": (data_dir.parent / "DATASET_CARD.md").name,
    }
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str), encoding="utf-8")
    return metrics


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI wrapper
    parser = argparse.ArgumentParser(prog="kharcha_ml.finetune.train")
    parser.add_argument("--base", required=True)
    parser.add_argument("--data", type=Path, default=Path("data/processed"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=Hyper.epochs)
    args = parser.parse_args(argv)
    train(args.base, args.data, args.out, Hyper(epochs=args.epochs))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
