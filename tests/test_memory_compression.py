import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from memory.conversation_memory import MemoryManager, Message, MsgRole


class CompressionTest(unittest.TestCase):
    def memory(self, failure=False, archive=True):
        memory = MemoryManager.__new__(MemoryManager)
        memory._client = SimpleNamespace(messages=SimpleNamespace(create=AsyncMock(
            side_effect=RuntimeError("offline") if failure else None,
            return_value=SimpleNamespace(content=[{"type": "text", "text": "summary"}]))))
        memory._model = "test"
        memory._redis = SimpleNamespace(get=AsyncMock(return_value=""), setex=AsyncMock(), ltrim=AsyncMock(), expire=AsyncMock())
        memory._get_working_memory = AsyncMock(return_value=[Message(MsgRole.USER, str(i)) for i in range(15)])
        memory._store_episodic = AsyncMock(return_value=archive)
        return memory

    def test_failed_summary_or_archive_preserves_raw_history(self):
        for failure, archive in [(True, True), (False, False)]:
            memory = self.memory(failure, archive)
            asyncio.run(memory._compress("u", "c"))
            memory._redis.ltrim.assert_not_called()

    def test_success_trims_without_reversing_messages(self):
        memory = self.memory()
        asyncio.run(memory._compress("u", "c"))
        memory._redis.ltrim.assert_awaited_once_with("wm:u:c", 0, 4)
