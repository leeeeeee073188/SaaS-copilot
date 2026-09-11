"""Org-scoped short history and Chroma episodes/preferences for the demo."""
import asyncio
import hashlib
import json
import os
import random
import time
from pathlib import Path
from redis.asyncio import from_url
from redis.exceptions import WatchError
from saas.knowledge import LexicalEmbedding


def memory_namespace(path):
    return hashlib.sha256(str(Path(path).resolve()).encode()).hexdigest()[:16]


class SandboxMemory:
    def __init__(self, service, chroma, redis_url=None):
        self.service = service
        url = redis_url or os.getenv("ECHOMIND_DEMO_REDIS_URL") or "redis://127.0.0.1:6380/0"
        self.redis = from_url(url, decode_responses=True, max_connections=32,
                              socket_connect_timeout=2, socket_timeout=2)
        # A database-specific namespace isolates independent sandboxes and test runs.
        self.namespace = memory_namespace(service.path)
        self.episodes = chroma.get_or_create_collection("flowforge_episodes_v1", embedding_function=LexicalEmbedding())
        self.profiles = chroma.get_or_create_collection("flowforge_profiles_v1", embedding_function=LexicalEmbedding())

    def key(self, actor, conv_id):
        return f"ff:wm:{self.namespace}:" + self.scope(actor, conv_id)

    @staticmethod
    def scope(actor, conv_id=""):
        return hashlib.sha256(json.dumps([actor.org_id, actor.user_id, conv_id]).encode()).hexdigest()

    async def history(self, actor, conv_id):
        return json.loads(await self.redis.get(self.key(actor, conv_id)) or "[]")

    async def context(self, actor, conv_id, query):
        history = await self.history(actor, conv_id)
        scope = self.scope(actor)
        episodes = await asyncio.to_thread(self.episodes.query, query_texts=[query], n_results=2, where={"scope": scope}) if self.episodes.count() else {}
        profile = await asyncio.to_thread(self.profiles.get, ids=[scope])
        return history, {"history": history, "episodes": (episodes.get("documents") or [[]])[0], "preferences": profile.get("documents") or [],
                         "notice": "历史不是当前权限、账单或套餐状态；必须重新查询业务工具。"}

    async def append(self, actor, conv_id, message, answer):
        key = self.key(actor, conv_id)
        for attempt in range(8):
            try:
                async with self.redis.pipeline() as pipe:
                    await pipe.watch(key)
                    history = json.loads(await pipe.get(key) or "[]")
                    history += [{"role": "user", "content": message}, {"role": "assistant", "content": answer}]
                    if len(history) > 16:
                        text = json.dumps(history[:-8], ensure_ascii=False)[:4000]
                        # Archive before trimming; stable IDs make retries idempotent.
                        await asyncio.to_thread(self.episodes.upsert, ids=[self.scope(actor, conv_id) + ":" + hashlib.sha256(text.encode()).hexdigest()],
                                                documents=[text], metadatas=[{"scope": self.scope(actor), "org_id": actor.org_id, "recorded_at": time.time()}])
                        history = history[-8:]
                    pipe.multi()
                    pipe.setex(key, 86400, json.dumps(history))
                    await pipe.execute()
                break
            except WatchError:
                if attempt == 7:
                    raise RuntimeError("Concurrent memory update limit exceeded") from None
                await asyncio.sleep(random.uniform(.005, .02) * (attempt + 1))
        preference = "简洁" if "请简洁" in message else "English" if "use English" in message else None
        if preference:
            await asyncio.to_thread(self.profiles.upsert, ids=[self.scope(actor)], documents=[preference],
                                    metadatas=[{"org_id": actor.org_id, "explicit": True}])

    async def close(self):
        await self.redis.aclose()
