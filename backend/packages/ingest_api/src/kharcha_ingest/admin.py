"""Admin CLI (``kharcha-admin``). Phase 1: create a user and an API key for the phone.

    uv run kharcha-admin create-user --name "Me"

Prints the key once (put it in the app) and the settings entry for the server.
"""

import argparse
import asyncio
import json
import secrets
import sys

from sqlalchemy.dialects.postgresql import insert

from kharcha_common.db import make_engine, make_sessionmaker
from kharcha_common.db.models import User
from kharcha_common.settings import get_settings
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kharcha-admin")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-user", help="create a user and print a new API key")
    create.add_argument("--id", default=None, help="user id (default: random u_xxxx)")
    create.add_argument("--name", default=None)
    args = parser.parse_args(argv)

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
