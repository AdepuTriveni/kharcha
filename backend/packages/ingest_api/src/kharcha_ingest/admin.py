"""Admin CLI (``kharcha-admin``). Phase 1: create a user and an API key for the phone.

    uv run kharcha-admin create-user --name "Me"
    uv run kharcha-admin link-code --user u_1234abcd
    uv run kharcha-admin seed-merchants
    uv run kharcha-admin export-labeling --out ../ml/data/raw/events.jsonl
    uv run kharcha-admin register-model ../ml/exports/v1/manifest.json \
        --eval ../ml/reports/v1/eval_report.json --url https://host/kharcha-parser-v1.gguf
    uv run kharcha-admin promote-model parser-v1-q4_k_m --to SHADOW

Prints the key once (put it in the app) and the settings entry for the server.
"""

import argparse
import asyncio
import json
import secrets
import sys
from pathlib import Path
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.dialects.postgresql import insert

from kharcha_common import model_registry
from kharcha_common.db import make_engine, make_sessionmaker
from kharcha_common.db.models import User
from kharcha_common.linking import create_link_code
from kharcha_common.merchants import seed_merchants
from kharcha_common.settings import get_settings
from kharcha_ingest import labeling
from kharcha_ingest.auth import hash_api_key, new_api_key


async def create_user(user_id: str, name: str | None) -> None:
    engine = make_engine(get_settings())
    try:
        async with make_sessionmaker(engine).begin() as session:
            await session.execute(
                insert(User).values(id=user_id, display_name=name).on_conflict_do_nothing()
            )
    finally:
        await engine.dispose()


async def load_seeds() -> int:
    engine = make_engine(get_settings())
    try:
        async with make_sessionmaker(engine).begin() as session:
            return await seed_merchants(session)
    finally:
        await engine.dispose()


async def export_labeling(out: Path) -> int:
    engine = make_engine(get_settings())
    try:
        async with make_sessionmaker(engine)() as session:
            return labeling.write(out, await labeling.collect(session))
    finally:
        await engine.dispose()


async def register_model(manifest: dict[str, Any], report: dict[str, Any], url: str) -> str:
    engine = make_engine(get_settings())
    try:
        async with make_sessionmaker(engine).begin() as session:
            return await model_registry.register(session, manifest, url, report)
    finally:
        await engine.dispose()


async def promote_model(model_id: str, to: str) -> None:
    engine = make_engine(get_settings())
    try:
        async with make_sessionmaker(engine).begin() as session:
            await model_registry.promote(session, model_id, model_registry.ModelStatus(to))
    finally:
        await engine.dispose()


async def link_code(user_id: str) -> str:
    redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
    try:
        return await create_link_code(redis, user_id)
    finally:
        await redis.aclose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kharcha-admin")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-user", help="create a user and print a new API key")
    create.add_argument("--id", default=None, help="user id (default: random u_xxxx)")
    create.add_argument("--name", default=None)
    link = sub.add_parser("link-code", help="print a Telegram link code for a user")
    link.add_argument("--user", required=True)
    sub.add_parser("seed-merchants", help="load backend/seeds/seed-merchants.yaml")
    export = sub.add_parser("export-labeling", help="consenting users' events for labeling")
    export.add_argument("--out", type=Path, default=Path("../ml/data/raw/events.jsonl"))
    reg = sub.add_parser("register-model", help="add a parser model as CANDIDATE")
    reg.add_argument("manifest", type=Path)
    reg.add_argument("--eval", type=Path, required=True, dest="report")
    reg.add_argument("--url", required=True, help="download URL of the GGUF file")
    pro = sub.add_parser("promote-model", help="CANDIDATE -> SHADOW -> ACTIVE (gate required)")
    pro.add_argument("model_id")
    pro.add_argument("--to", choices=["SHADOW", "ACTIVE", "RETIRED"], required=True)
    args = parser.parse_args(argv)

    if args.command == "register-model":
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        report = json.loads(args.report.read_text(encoding="utf-8"))
        model_id = asyncio.run(register_model(manifest, report, args.url))
        sys.stdout.write(f"registered {model_id} as CANDIDATE\n")
        return 0
    if args.command == "promote-model":
        try:
            asyncio.run(promote_model(args.model_id, args.to))
        except model_registry.PromotionError as exc:
            sys.stderr.write(f"refused: {exc}\n")
            return 1
        sys.stdout.write(f"{args.model_id} is now {args.to}\n")
        return 0

    if args.command == "export-labeling":
        count = asyncio.run(export_labeling(args.out))
        sys.stdout.write(f"exported {count} events to {args.out}\n")
        return 0

    if args.command == "seed-merchants":
        sys.stdout.write(f"seeded {asyncio.run(load_seeds())} merchants\n")
        return 0

    if args.command == "link-code":
        code = asyncio.run(link_code(args.user))
        sys.stdout.write(f"Send this to the bot within 10 minutes: /start {code}\n")
        return 0

    user_id = args.id or f"u_{secrets.token_hex(4)}"
    asyncio.run(create_user(user_id, args.name))
    key = new_api_key()
    existing = dict(get_settings().api_keys)
    existing[hash_api_key(key)] = user_id
    out = sys.stdout
    out.write(f"user id : {user_id}\n")
    out.write(f"API key : {key}   (shown once; enter it in the app)\n")
    out.write("Add to backend/.env (keeps existing keys):\n")
    out.write(f"KHARCHA_API_KEYS='{json.dumps(existing)}'\n")
    return 0


def run() -> None:
    raise SystemExit(main())
