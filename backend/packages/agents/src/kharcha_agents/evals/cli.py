"""``kharcha-eval`` (PROJECT_SPEC §27.2). Each suite writes JSON + Markdown and an eval_runs row.

uv run kharcha-eval parsing ../ml/data/samples/parsing_sample.jsonl
uv run kharcha-eval parsing ../ml/data/labels/gold_test.jsonl --model ollama/qwen2.5:7b
"""

import asyncio
import json
import subprocess
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import typer
from sqlalchemy.exc import SQLAlchemyError

from kharcha_agents.evals.parsing import NoModel, run_parsing, seed_rules_compiled
from kharcha_common.db import make_engine, make_sessionmaker
from kharcha_common.db.models import EvalRun
from kharcha_common.ids import uuid7
from kharcha_common.settings import Settings, get_settings
from kharcha_ml.dataset.labels import read_jsonl
from kharcha_processor.rules import CompiledRule
from kharcha_processor.teacher import Extractor, TeacherLLM


class ParserMode(StrEnum):
    TIERED = "tiered"  # seed rules, then the teacher (what production does)
    RULES = "rules"  # seed rules only
    TEACHER = "teacher"  # teacher only


app = typer.Typer(help="Kharcha evaluation suites", no_args_is_help=True)


def git_sha() -> str | None:
    try:
        done = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607 - fixed command, no input
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return done.stdout.strip() or None


def write_reports(out_dir: Path, stem: str, metrics: dict[str, Any], markdown: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{stem}.json").write_text(
        json.dumps(metrics, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    md_path = out_dir / f"{stem}.md"
    md_path.write_text(markdown, encoding="utf-8")
    return md_path


async def record_run(settings: Settings, suite: str, subject: str, metrics: dict[str, Any]) -> str:
    run_id = str(uuid7())
    engine = make_engine(settings)
    try:
        async with make_sessionmaker(engine).begin() as session:
            session.add(
                EvalRun(id=run_id, suite=suite, subject=subject, metrics=metrics, git_sha=git_sha())
            )
    finally:
        await engine.dispose()
    return run_id


async def _parsing(
    labels: Path,
    out_dir: Path,
    settings: Settings,
    extractor: Extractor,
    rules: list[CompiledRule],
    subject: str,
    record: bool,
) -> Path:
    examples = list(read_jsonl(labels))
    result = await run_parsing(examples, extractor, rules, subject)
    metrics = {"dataset": labels.name, **result.metrics()}
    stem = f"parsing-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    md = write_reports(out_dir, stem, metrics, result.markdown(f"Parsing eval: {labels.name}"))
    typer.echo(md.read_text(encoding="utf-8"))
    if record:
        try:
            run_id = await record_run(settings, "PARSER", result.subject, metrics)
            typer.echo(f"eval_runs row {run_id}")
        except (OSError, SQLAlchemyError) as exc:
            typer.echo(f"warning: eval_runs not written ({type(exc).__name__})", err=True)
    return md


@app.command()
def parsing(
    labels: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="labels .jsonl")],
    out_dir: Annotated[Path, typer.Option(help="report directory")] = Path("reports/parsing"),
    parser: Annotated[ParserMode, typer.Option(help="which tiers to run")] = ParserMode.TIERED,
    model: Annotated[str | None, typer.Option(help="LiteLLM model (default: settings)")] = None,
    record: Annotated[bool, typer.Option(help="write a row to eval_runs")] = True,
) -> None:
    """Server parser (pre-filter -> rules -> teacher -> validation) against labeled examples."""
    settings = get_settings()
    if model:
        settings = settings.model_copy(update={"llm_model": model})
    rules = [] if parser is ParserMode.TEACHER else seed_rules_compiled()
    extractor: Extractor = NoModel() if parser is ParserMode.RULES else TeacherLLM(settings)
    subject = {
        ParserMode.RULES: "rules:seed",
        ParserMode.TEACHER: extractor.model_version,
        ParserMode.TIERED: f"rules:seed+{extractor.model_version}",
    }[parser]
    md = asyncio.run(_parsing(labels, out_dir, settings, extractor, rules, subject, record))
    typer.echo(f"report: {md}")


@app.command()
def ask() -> None:
    """Ask Kharcha execution accuracy (W17)."""
    raise typer.Exit(_not_yet("ask", "W17"))


@app.command()
def agents() -> None:
    """Agent policy-gate and memory suites (W8+)."""
    raise typer.Exit(_not_yet("agents", "W8"))


@app.command()
def forecast() -> None:
    """Broke-date forecast backtest (W7)."""
    raise typer.Exit(_not_yet("forecast", "W7"))


def _not_yet(suite: str, week: str) -> int:
    typer.echo(f"suite '{suite}' arrives in {week}", err=True)
    return 2


def run() -> None:
    app()
