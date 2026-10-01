"""``kharcha-dataset``: synthesize, distill and build leakage-free splits (PROJECT_SPEC §15.2-15.3).

    uv run kharcha-dataset synth --n 6000 --out data/synthetic/synthetic.jsonl
    uv run kharcha-dataset distill data/raw/events.jsonl --out data/labels/distilled.jsonl \\
        --queue data/labels/review_queue.jsonl --model qwen2.5:7b
    uv run kharcha-label review data/labels/review_queue.jsonl --out data/labels/labels.jsonl
    uv run kharcha-dataset build data/synthetic/synthetic.jsonl data/labels/distilled.jsonl \\
        --reviewed data/labels/labels.jsonl --out data/processed
"""

import argparse
import asyncio
import sys
from pathlib import Path

from kharcha_ml.dataset.card import render_card
from kharcha_ml.dataset.labels import LabeledExample, read_jsonl, write_jsonl
from kharcha_ml.dataset.splits import Splits, leaking_templates, make_splits
from kharcha_ml.dataset.synthetic import generate
from kharcha_ml.distill.teacher import OllamaTeacher, distill

CARD = Path(__file__).resolve().parents[3] / "data" / "DATASET_CARD.md"


def merge(inputs: list[Path], reviewed: Path | None) -> list[LabeledExample]:
    """Later inputs override earlier ones by event id; reviewed labels win over everything."""
    by_id: dict[str, LabeledExample] = {}
    for path in inputs:
        for e in read_jsonl(path):
            by_id[e.event_id] = e
    if reviewed is not None and reviewed.exists():
        for e in read_jsonl(reviewed):
            by_id[e.event_id] = e.model_copy(update={"reviewed": True})
    return list(by_id.values())


def build(inputs: list[Path], reviewed: Path | None, out: Path, card: Path = CARD) -> Splits:
    splits = make_splits(merge(inputs, reviewed))
    if leaks := leaking_templates(splits):
        raise SystemExit(f"template leakage across splits: {sorted(leaks)[:5]}")
    for name, members in splits.by_name().items():
        write_jsonl(out / f"{name}.jsonl", members)
    card.parent.mkdir(parents=True, exist_ok=True)
    card.write_text(render_card(splits), encoding="utf-8", newline="\n")
    return splits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kharcha-dataset")
    sub = parser.add_subparsers(dest="command", required=True)
    syn = sub.add_parser("synth", help="generate synthetic examples with exact labels")
    syn.add_argument("--n", type=int, default=6000)
    syn.add_argument("--seed", type=int, default=0)
    syn.add_argument("--out", type=Path, required=True)
    dis = sub.add_parser("distill", help="teacher-label exported events, write the review queue")
    dis.add_argument("source", type=Path)
    dis.add_argument("--out", type=Path, required=True)
    dis.add_argument("--queue", type=Path, required=True)
    dis.add_argument("--model", default="qwen2.5:7b")
    dis.add_argument("--ollama", default="http://localhost:11434")
    bld = sub.add_parser("build", help="merge, split by template, write splits + dataset card")
    bld.add_argument("inputs", type=Path, nargs="+")
    bld.add_argument("--reviewed", type=Path, default=None)
    bld.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    out = sys.stdout

    if args.command == "synth":
        n = write_jsonl(args.out, generate(args.n, seed=args.seed))
        out.write(f"wrote {n} synthetic examples to {args.out}\n")
    elif args.command == "distill":
        result = asyncio.run(
            distill(list(read_jsonl(args.source)), OllamaTeacher(args.model, args.ollama))
        )
        write_jsonl(args.out, result.labeled)
        write_jsonl(args.queue, result.review_queue)
        out.write(
            f"labeled {len(result.labeled)}, review queue {len(result.review_queue)} "
            f"(disagreements {result.disagreements}, failures {result.failures})\n"
        )
    else:
        splits = build(args.inputs, args.reviewed, args.out)
        sizes = ", ".join(f"{k}={len(v)}" for k, v in splits.by_name().items())
        out.write(f"splits: {sizes}; card: {CARD}\n")
    return 0


def run() -> None:
    raise SystemExit(main())
