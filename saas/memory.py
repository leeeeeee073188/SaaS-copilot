"""Org-scoped short history and Chroma episodes/preferences for the demo."""
import asyncio
import hashlib
import json
import time
from saas.knowledge import LexicalEmbedding


class SandboxMemory:
    def __init__(self, service, chroma, redis_url=None):
        self.service = service
        self.redis = None
        if redis_url:
            from redis.asyncio import from_url
            self.redis = from_url(redis_url, decode_responses=True)
        self.episodes = chroma.get_or_create_collection("flowforge_episodes_v1", embedding_function=LexicalEmbedding())
        self.profiles = chroma.get_or_create_collection("flowforge_profiles_v1", embedding_function=LexicalEmbedding())
        self._lock = asyncio.Lock()
        with service.tx() as db:
            db.execute("CREATE TABLE IF NOT EXISTS conversations(scope TEXT PRIMARY KEY, history TEXT)")

    @staticmethod
    def scope(actor, conv_id=""):
        return hashlib.sha256(json.dumps([actor.org_id, actor.user_id, conv_id]).encode()).hexdigest()

    async def history(self, actor, conv_id):
        key = self.scope(actor, conv_id)
        if self.redis:
            return json.loads(await self.redis.get("ff:wm:" + key) or "[]")
        with self.service.tx() as db:
            row = db.execute("SELECT history FROM conversations WHERE scope=?", (key,)).fetchone()
            return json.loads(row[0]) if row else []

    async def context(self, actor, conv_id, query):
        history = await self.history(actor, conv_id)
        scope = self.scope(actor)
        episodes = await asyncio.to_thread(self.episodes.query, query_texts=[query], n_results=2, where={"scope": scope}) if self.episodes.count() else {}
        profile = await asyncio.to_thread(self.profiles.get, ids=[scope])
        return history, {"history": history, "episodes": (episodes.get("documents") or [[]])[0], "preferences": profile.get("documents") or [],
                         "notice": "历史不是当前权限、账单或套餐状态；必须重新查询业务工具。"}

    async def append(self, actor, conv_id, message, answer):
        # Short demo lock keeps read/append/archive coherent within this process.
        async with self._lock:
            history = await self.history(actor, conv_id)
            history += [{"role": "user", "content": message}, {"role": "assistant", "content": answer}]
            if len(history) > 16:
                archived = history[:-8]
                text = json.dumps(archived, ensure_ascii=False)[:4000]
                await asyncio.to_thread(self.episodes.upsert, ids=[self.scope(actor, conv_id) + ":" + hashlib.sha256(text.encode()).hexdigest()],
                                        documents=[text], metadatas=[{"scope": self.scope(actor), "org_id": actor.org_id, "recorded_at": time.time()}])
                history = history[-8:]
            key = self.scope(actor, conv_id)
            if self.redis:
                await self.redis.setex("ff:wm:" + key, 86400, json.dumps(history))
            else:
                with self.service.tx() as db:
                    db.execute("INSERT OR REPLACE INTO conversations VALUES (?,?)", (key, json.dumps(history)))
            preference = "简洁" if "请简洁" in message else "English" if "use English" in message else None
            if preference:
                await asyncio.to_thread(self.profiles.upsert, ids=[self.scope(actor)], documents=[preference],
                                        metadatas=[{"org_id": actor.org_id, "explicit": True}])

    async def close(self):
        if self.redis:
            await self.redis.aclose()
