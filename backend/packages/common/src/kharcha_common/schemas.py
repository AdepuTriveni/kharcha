"""Export JSON Schemas from the Pydantic event models (for Android + contract tests).

Usage: ``uv run python -m kharcha_common.schemas export [--out DIR]``
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from kharcha_common.events import (
    CashEvent,
    CleanTransactionEvent,
    ParsedTransactionEvent,
    RawEvent,
)

EXPORTED: dict[str, type[BaseModel]] = {
    "raw-event": RawEvent,
    "parsed-transaction-event": ParsedTransactionEvent,
    "clean-transaction-event": CleanTransactionEvent,
    "cash-event": CashEvent,
}

DEFAULT_OUT = Path(__file__).resolve().parents[4] / "schemas"


def build_schemas() -> dict[str, dict[str, Any]]:
    return {
        name: model.model_json_schema(by_alias=True, mode="serialization")
        for name, model in EXPORTED.items()
    }


def render(schema: dict[str, Any]) -> str:
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def export(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, schema in build_schemas().items():
        path = out_dir / f"{name}.schema.json"
        path.write_text(render(schema), encoding="utf-8", newline="\n")
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kharcha_common.schemas")
    sub = parser.add_subparsers(dest="command", required=True)
    exp = sub.add_parser("export", help="write JSON Schema files")
    exp.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    for path in export(args.out):
        sys.stdout.write(f"wrote {path}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
