"""Dataset card (PROJECT_SPEC §15.3): version, counts per source / bank / status / split."""

import hashlib
from collections import Counter
from datetime import UTC, datetime

from kharcha_ml.dataset.labels import LabeledExample
from kharcha_ml.dataset.splits import Splits, template_of


def dataset_version(splits: Splits) -> str:
    digest = hashlib.sha256()
    for name, members in splits.by_name().items():
        for e in sorted(members, key=lambda x: x.event_id):
            digest.update(f"{name}|{e.event_id}|{e.label.model_dump_json()}".encode())
    return digest.hexdigest()[:12]


def _bank(e: LabeledExample) -> str:
    return (e.sender or "").split("-")[-1].upper() or (e.source_app or "unknown")


def _table(title: str, counters: dict[str, Counter[str]]) -> list[str]:
    keys = sorted({k for c in counters.values() for k in c})
    lines = [
        f"### {title}",
        "",
        "| | " + " | ".join(counters) + " |",
        "|---|" + "---|" * len(counters),
    ]
    for k in keys:
        lines.append(f"| {k} | " + " | ".join(str(c.get(k, 0)) for c in counters.values()) + " |")
    return [*lines, ""]


def render_card(splits: Splits, notes: str = "") -> str:
    parts = splits.by_name()
    by: dict[str, dict[str, Counter[str]]] = {
        "Source": {n: Counter(e.source for e in m) for n, m in parts.items()},
        "Bank / app": {n: Counter(_bank(e) for e in m) for n, m in parts.items()},
        "Status": {
            n: Counter(e.label.status or "NOT_TRANSACTION" for e in m) for n, m in parts.items()
        },
    }
    lines = [
        "# Kharcha parser dataset card",
        "",
        f"- Version: `{dataset_version(splits)}`",
        f"- Built: {datetime.now(UTC):%Y-%m-%d %H:%M} UTC",
        "- Splits by template (one template, one split); `gold_test` is human-verified and is",
        "  **never used for training**; held-out banks appear only in `gold_test`.",
        f"- Near-duplicates removed from train/val: {splits.near_duplicates_removed}",
        "- Real data only from users with `ml_consent = true`; redacted on the phone and again",
        "  at export.",
        "",
        "| Split | Examples | Templates | Reviewed |",
        "|---|---|---|---|",
    ]
    for name, members in parts.items():
        templates = len({template_of(e) for e in members})
        reviewed = sum(e.reviewed for e in members)
        lines.append(f"| {name} | {len(members)} | {templates} | {reviewed} |")
    lines.append("")
    for title, counters in by.items():
        lines += _table(title, counters)
    if notes:
        lines += ["## Notes", "", notes, ""]
    return "\n".join(lines)
