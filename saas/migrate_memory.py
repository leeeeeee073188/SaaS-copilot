"""Copy old SQLite working history to Redis without replacing newer sessions."""
import argparse
import asyncio
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from dotenv import load_dotenv
from redis.asyncio import from_url
from saas.memory import memory_namespace


async def migrate(path, redis_url):
    path = Path(path).resolve()
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        exists = db.execute("SELECT name FROM sqlite_master WHERE name='conversations'").fetchone()
        rows = db.execute("SELECT scope, history FROM conversations").fetchall() if exists else []
    client = from_url(redis_url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2)
    copied = 0
    try:
        await client.ping()
        for scope, history in rows:
            messages = json.loads(history)
            if not isinstance(messages, list):
                raise ValueError("Invalid working history")
            copied += bool(await client.set(f"ff:wm:{memory_namespace(path)}:{scope}", history, ex=86400, nx=True))
        return {"copied": copied, "skipped": len(rows) - copied}
    finally:
        await client.aclose()


if __name__ == "__main__":
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="data/flowforge/flowforge.db")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(migrate(args.database, os.getenv("SAAS_COPILOT_DEMO_REDIS_URL") or "redis://127.0.0.1:6380/0"))))
