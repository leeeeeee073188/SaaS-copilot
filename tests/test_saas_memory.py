import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from saas.knowledge import SandboxKnowledge
from saas.memory import SandboxMemory
from saas.service import Actor, SaaSService
from saas.migrate_memory import migrate


class SandboxMemoryTest(unittest.TestCase):
    def test_migrate_old_history_without_overwriting_redis(self):
        async def run():
            import json
            root = Path(tempfile.mkdtemp(prefix="ff-memory-migrate-"))
            service = SaaSService(root / "state.db")
            kb = SandboxKnowledge(root / "chroma")
            memory = SandboxMemory(service, kb.client)
            actor = Actor("u", "org", "owner")
            original = [{"role": "user", "content": "old history"}]
            with service.tx() as db:
                db.execute("CREATE TABLE conversations(scope TEXT PRIMARY KEY, history TEXT)")
                db.execute("INSERT INTO conversations VALUES (?, ?)", (memory.scope(actor, "c"), json.dumps(original)))
            try:
                report = await migrate(service.path, "redis://127.0.0.1:6380/0")
                self.assertEqual(report, {"copied": 1, "skipped": 0})
                self.assertEqual(await memory.history(actor, "c"), original)
                await memory.append(actor, "c", "new", "answer")
                report = await migrate(service.path, "redis://127.0.0.1:6380/0")
                self.assertEqual(report, {"copied": 0, "skipped": 1})
                self.assertEqual(len(await memory.history(actor, "c")), 3)
            finally:
                await memory.close()
        asyncio.run(run())

    def test_archive_order_preferences_and_org_isolation(self):
        async def run():
            root = Path(tempfile.mkdtemp(prefix="ff-memory-"))
            kb = SandboxKnowledge(root / "chroma")
            memory = SandboxMemory(SaaSService(root / "state.db"), kb.client)
            owner = Actor("aurora_owner", "org_aurora", "owner")
            other = Actor("cedar_owner", "org_cedar", "owner")
            for i in range(9):
                await memory.append(owner, "same", f"请简洁 {i}", f"answer {i}")
            history, context = await memory.context(owner, "same", "请简洁")
            self.assertEqual([m["content"] for m in history[::2]], [f"请简洁 {i}" for i in range(5, 9)])
            self.assertEqual(context["preferences"], ["简洁"])
            self.assertIn("answer 0", context["episodes"][0])
            foreign_history, foreign = await memory.context(other, "same", "请简洁")
            self.assertEqual((foreign_history, foreign["episodes"], foreign["preferences"]), ([], [], []))
            await memory.close()
        asyncio.run(run())

    def test_failed_archive_keeps_persisted_history(self):
        async def run():
            root = Path(tempfile.mkdtemp(prefix="ff-memory-failure-"))
            kb = SandboxKnowledge(root / "chroma")
            memory = SandboxMemory(SaaSService(root / "state.db"), kb.client)
            actor = Actor("aurora_owner", "org_aurora", "owner")
            for i in range(8):
                await memory.append(actor, "c", str(i), "answer")
            before = await memory.history(actor, "c")
            with patch.object(type(memory.episodes), "upsert", side_effect=RuntimeError("storage down")):
                with self.assertRaises(RuntimeError):
                    await memory.append(actor, "c", "next", "answer")
            self.assertEqual(await memory.history(actor, "c"), before)
            await memory.close()
        asyncio.run(run())

    def test_redis_concurrent_clients_ttl_and_no_sqlite_history(self):
        async def run():
            root = Path(tempfile.mkdtemp(prefix="ff-redis-concurrency-"))
            service = SaaSService(root / "state.db")
            kb = SandboxKnowledge(root / "chroma")
            memories = [SandboxMemory(service, kb.client) for _ in range(2)]
            actor = Actor("u", "org_aurora", "owner")
            try:
                await asyncio.gather(*(memories[i % 2].append(actor, "shared", f"message {i}", f"answer {i}") for i in range(8)))
                history = await memories[0].history(actor, "shared")
                self.assertEqual(len(history), 16)
                self.assertEqual({m["content"] for m in history[::2]}, {f"message {i}" for i in range(8)})
                for user, assistant in zip(history[::2], history[1::2]):
                    self.assertEqual(user["content"].split()[-1], assistant["content"].split()[-1])
                key = memories[0].key(actor, "shared")
                self.assertTrue(86390 <= await memories[0].redis.ttl(key) <= 86400)
                with service.tx() as db:
                    self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='conversations'").fetchone())
                await memories[0].redis.pexpire(key, 10)
                await asyncio.sleep(.03)
                self.assertEqual(await memories[1].history(actor, "shared"), [])
            finally:
                await asyncio.gather(*(memory.close() for memory in memories))
        asyncio.run(run())
