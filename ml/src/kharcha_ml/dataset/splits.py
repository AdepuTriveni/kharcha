"""Leakage-free splits (PROJECT_SPEC §15.3).

- Group by template (``example.template``: the real template id for synthetic data, the
  sender template signature for real messages). A template lands in exactly one split.
- ``gold_test`` holds only human-verified examples, includes the held-out banks, and is never
  trained on (rule 8). Unreviewed templates can only go to train/val.
- Near-duplicates (MinHash over masked-text shingles, Jaccard >= 0.8) that cross splits are
  removed from the training side.
"""

import hashlib
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from kharcha_ml.dataset.labels import LabeledExample, template_signature
from kharcha_ml.dataset.synthetic import HELD_OUT_SENDERS

SPLITS = ("train", "val", "gold_test")
NUM_PERM = 64
NEAR_DUP = 0.8
_MASK = re.compile(r"\d+")


def template_of(example: LabeledExample) -> str:
    return example.template or template_signature(example.text, example.sender)


def _bucket(template: str, salt: str) -> float:
    digest = hashlib.sha256(f"{salt}:{template}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def _sender_key(example: LabeledExample) -> str:
    return (example.sender or "").split("-")[-1].upper()


@dataclass
class Splits:
    train: list[LabeledExample] = field(default_factory=list)
    val: list[LabeledExample] = field(default_factory=list)
    gold_test: list[LabeledExample] = field(default_factory=list)
    near_duplicates_removed: int = 0

    def by_name(self) -> dict[str, list[LabeledExample]]:
        return {"train": self.train, "val": self.val, "gold_test": self.gold_test}


def assign_templates(
    examples: Sequence[LabeledExample],
    val_share: float = 0.1,
    gold_share: float = 0.15,
    salt: str = "kharcha-v1",
) -> dict[str, str]:
    """template -> split. Deterministic for a salt."""
    groups: dict[str, list[LabeledExample]] = defaultdict(list)
    for e in examples:
        groups[template_of(e)].append(e)
    out: dict[str, str] = {}
    for template, members in groups.items():
        all_reviewed = all(e.reviewed for e in members)
        held_out = any(_sender_key(e) in HELD_OUT_SENDERS for e in members)
        if held_out and all_reviewed:
            out[template] = "gold_test"
            continue
        if held_out:
            continue  # held-out bank but unreviewed: keep out of training, not usable as gold
        b = _bucket(template, salt)
        if all_reviewed and b < gold_share:
            out[template] = "gold_test"
        elif b < gold_share + val_share:
            out[template] = "val"
        else:
            out[template] = "train"
    return out


def _shingles(text: str, k: int = 4) -> set[str]:
    words = _MASK.sub("0", text.lower()).split()
    if len(words) < k:
        return {" ".join(words)}
    return {" ".join(words[i : i + k]) for i in range(len(words) - k + 1)}


def minhash(text: str, num_perm: int = NUM_PERM) -> tuple[int, ...]:
    shingles = _shingles(text)
    return tuple(
        min(
            int.from_bytes(hashlib.blake2b(f"{seed}:{s}".encode(), digest_size=8).digest(), "big")
            for s in shingles
        )
        for seed in range(num_perm)
    )


def similarity(a: tuple[int, ...], b: tuple[int, ...]) -> float:
    return sum(x == y for x, y in zip(a, b, strict=True)) / len(a)


def _bands(signature: tuple[int, ...], rows: int = 4) -> Iterable[tuple[int, tuple[int, ...]]]:
    for i in range(0, len(signature), rows):
        yield i, signature[i : i + rows]


def drop_near_duplicates(
    protected: Sequence[LabeledExample], candidates: Sequence[LabeledExample]
) -> tuple[list[LabeledExample], int]:
    """Remove candidates that are near-duplicates of any protected example (LSH + check)."""
    index: dict[tuple[int, tuple[int, ...]], list[tuple[int, ...]]] = defaultdict(list)
    for e in protected:
        sig = minhash(e.text)
        for band in _bands(sig):
            index[band].append(sig)
    kept, removed = [], 0
    for e in candidates:
        sig = minhash(e.text)
        seen = {other for band in _bands(sig) for other in index.get(band, [])}
        if any(similarity(sig, other) >= NEAR_DUP for other in seen):
            removed += 1
        else:
            kept.append(e)
    return kept, removed


def make_splits(examples: Sequence[LabeledExample], salt: str = "kharcha-v1") -> Splits:
    assignment = assign_templates(examples, salt=salt)
    splits = Splits()
    for e in examples:
        name = assignment.get(template_of(e))
        if name is not None:
            getattr(splits, name).append(e)
    # Gold is frozen: near-duplicates of gold or val are removed from train, of gold from val.
    splits.val, dropped_val = drop_near_duplicates(splits.gold_test, splits.val)
    splits.train, dropped_train = drop_near_duplicates(splits.gold_test + splits.val, splits.train)
    splits.near_duplicates_removed = dropped_val + dropped_train
    return splits


def leaking_templates(splits: Splits) -> dict[str, set[str]]:
    """Templates found in more than one split (must be empty)."""
    seen: dict[str, set[str]] = defaultdict(set)
    for name, members in splits.by_name().items():
        for e in members:
            seen[template_of(e)].add(name)
    return {t: names for t, names in seen.items() if len(names) > 1}
