import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from saas.knowledge import SandboxKnowledge
from saas.memory import SandboxMemory
from saas.service import Actor, SaaSService


class SandboxMemoryTest(unittest.TestCase):
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
        asyncio.run(run())
