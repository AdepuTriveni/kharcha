"""Human review queue for parsing labels (PROJECT_SPEC §15.2) -- ``kharcha-label``.

    uv run kharcha-label review data/raw/events.jsonl --out data/labels/labels.jsonl
    uv run kharcha-label stats data/labels/labels.jsonl

``review`` shows each message with the proposed label (from the server parse or the teacher),
lets you accept, edit, or mark it as not a transaction, and appends reviewed labels to
``--out``. It is resumable: examples already in ``--out`` are skipped.
"""

import argparse
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from kharcha_ml.dataset.labels import (
    Extraction,
    LabeledExample,
    append_jsonl,
    read_jsonl,
    template_signature,
)

EDITABLE = (
    "amount",
    "direction",
    "channel",
    "status",
    "merchant_raw",
    "counterparty_vpa",
    "reference_id",
    "account_hint",
    "balance_after",
    "promised_refund_days",
)
Ask = Callable[[str], str]
Say = Callable[[str], None]


def _show(example: LabeledExample, say: Say) -> None:
    say("")
    say(f"[{example.event_id}] sender={example.sender} app={example.source_app}")
    say(f"  {example.text}")
    label = example.label
    if not label.is_transaction:
        say("  proposed: NOT a transaction")
        return
    for name in EDITABLE:
        value = getattr(label, name)
        if value is not None:
            say(f"  {name:<20} {value}")


def _edit(label: Extraction, ask: Ask, say: Say) -> Extraction | None:
    """Field-by-field edit. Enter keeps the value, '-' clears it. Returns None on bad input."""
    data = label.model_dump()
    data["is_transaction"] = True
    for name in EDITABLE:
        current = data.get(name)
        answer = ask(f"  {name} [{'' if current is None else current}]: ").strip()
        if answer == "-":
            data[name] = None
        elif answer:
            data[name] = int(answer) if name == "promised_refund_days" else answer
    try:
        return Extraction.model_validate(data)
    except ValueError as exc:
        say(f"  invalid label: {exc.__class__.__name__}; try again")
        return None


def review(
    source: Path, out: Path, ask: Ask = input, say: Say = print, limit: int | None = None
) -> int:
    """Review unlabeled examples from ``source``. Returns the number of labels written."""
    done = {e.event_id for e in read_jsonl(out)} if out.exists() else set()
    written = 0
    for example in read_jsonl(source):
        if example.event_id in done:
            continue
        if limit is not None and written >= limit:
            break
        _show(example, say)
        while True:
            choice = ask("[a]ccept [e]dit [n]ot a transaction [s]kip [q]uit > ").strip().lower()
            if choice == "q":
                return written
            if choice == "s":
                break
            label: Extraction | None = None
            if choice in {"a", ""}:
                label = example.label
            elif choice == "n":
                label = Extraction(is_transaction=False)
            elif choice == "e":
                label = _edit(example.label, ask, say)
            if label is None:
                continue
            reviewed = example.model_copy(
                update={
                    "label": label,
                    "reviewed": True,
                    "template": template_signature(example.text, example.sender),
                }
            )
            append_jsonl(out, reviewed)
            written += 1
            break
    return written


def stats(path: Path, say: Say = print) -> None:
    examples = list(read_jsonl(path))
    say(f"examples: {len(examples)}  reviewed: {sum(e.reviewed for e in examples)}")
    say(f"templates: {len({e.template or template_signature(e.text, e.sender) for e in examples})}")
    for title, counter in (
        ("source", Counter(e.source for e in examples)),
        ("sender", Counter(e.sender or "unknown" for e in examples)),
        ("status", Counter(e.label.status or "-" for e in examples if e.label.is_transaction)),
        ("isTransaction", Counter(str(e.label.is_transaction) for e in examples)),
    ):
        say(f"{title}: " + ", ".join(f"{k}={v}" for k, v in counter.most_common()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kharcha-label")
    sub = parser.add_subparsers(dest="command", required=True)
    rev = sub.add_parser("review", help="review proposed labels")
    rev.add_argument("source", type=Path)
    rev.add_argument("--out", type=Path, required=True)
    rev.add_argument("--limit", type=int, default=None)
    st = sub.add_parser("stats", help="dataset counts")
    st.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    if args.command == "review":
        n = review(args.source, args.out, limit=args.limit)
        sys.stdout.write(f"\n{n} labels written to {args.out}\n")
    else:
        stats(args.path)
    return 0


def run() -> None:
    raise SystemExit(main())
