import json
import asyncio
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from memory.conversation_memory import MemoryManager, Message, MsgRole


class _ProfileCollectionWithMultipleSessions:
    def get(self, *, where=None, limit=None, ids=None):
        records = [
            {
                "id": "user-1_profile_session-old",
                "document": json.dumps({"version": "old"}),
                "metadata": {"user_id": "user-1", "ts": "2026-09-01T08:00:00"},
            },
            {
                "id": "user-1_profile_session-new",
                "document": json.dumps({"version": "new"}),
                "metadata": {"user_id": "user-1", "ts": "2026-09-07T08:00:00"},
            },
        ]
        if ids is not None:
            records = [record for record in records if record["id"] in ids]
        if where is not None:
            records = [record for record in records if record["metadata"]["user_id"] == where.get("user_id")]
        if limit is not None:
            records = records[:limit]
        return {
            "ids": [record["id"] for record in records],
            "documents": [record["document"] for record in records],
            "metadatas": [record["metadata"] for record in records],
        }


class _RecordingProfileCollection:
    def __init__(self):
        self.upsert_calls = []

    def upsert(self, **kwargs):
        self.upsert_calls.append(kwargs)


class _FakeMessagesClient:
    async def create(self, **kwargs):
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps({
                "changes": [{
                    "operation": "add", "section": "preferences", "key": "answer_style",
                    "value": "concise", "evidence": "Prefer concise answers",
                    "confidence": 1.0, "explicit": True,
                }]
            }))]
        )


class _InMemoryRedis:
    def __init__(self, messages):
        self._messages = messages

    async def lrange(self, key, start, end):
        return self._messages[start:end + 1]

    async def get(self, key):
        return None


class _InMemoryProfileCollection:
    def __init__(self, profile):
        self._records = {
            "legacy-profile": {
                "document": json.dumps(profile),
                "metadata": {"user_id": "user-1", "ts": "2026-09-01T08:00:00"},
            }
        }

    def get(self, *, where=None, limit=None, ids=None):
        records = list(self._records.items())
        if ids is not None:
            records = [(doc_id, record) for doc_id, record in records if doc_id in ids]
        if where is not None:
            records = [
                (doc_id, record)
                for doc_id, record in records
                if record["metadata"].get("user_id") == where.get("user_id")
            ]
        if limit is not None:
            records = records[:limit]
        return {
            "ids": [doc_id for doc_id, _ in records],
            "documents": [record["document"] for _, record in records],
            "metadatas": [record["metadata"] for _, record in records],
        }

    def upsert(self, *, ids, documents, metadatas):
        for doc_id, document, metadata in zip(ids, documents, metadatas):
            self._records[doc_id] = {"document": document, "metadata": metadata}

    def delete(self, *, ids):
        for doc_id in ids:
            self._records.pop(doc_id, None)


class _EmptyEpisodicCollection:
    def query(self, **kwargs):
        return {"documents": [[]]}


class _ProfileChangeMessagesClient:
    def __init__(self, payload):
        self._payload = payload
        self.last_prompt = None

    async def create(self, **kwargs):
        self.last_prompt = kwargs["messages"][0]["content"]
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps(self._payload, ensure_ascii=False))]
        )


class _ConversationAwareMessagesClient:
    async def create(self, **kwargs):
        prompt = kwargs["messages"][0]["content"]
        if "user: Please use English" in prompt:
            change = {
                "operation": "add", "section": "preferences", "key": "language",
                "value": "en-US", "evidence": "Please use English", "confidence": 1.0,
                "explicit": True,
            }
        else:
            change = {
                "operation": "add", "section": "preferences", "key": "answer_style",
                "value": "concise", "evidence": "请简洁回答", "confidence": 1.0,
                "explicit": True,
            }
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps({"changes": [change]}, ensure_ascii=False))]
        )


class _ConversationRedis:
    def __init__(self):
        self._messages = {
            "wm:user-1:english-session": [json.dumps({
                "role": "user", "content": "Please use English",
                "ts": "2026-09-07T09:00:00", "metadata": {},
            })],
            "wm:user-1:style-session": [json.dumps({
                "role": "user", "content": "请简洁回答",
                "ts": "2026-09-07T09:01:00", "metadata": {},
            })],
        }

    async def lrange(self, key, start, end):
        return self._messages.get(key, [])[start:end + 1]

    async def get(self, key):
        return None


class MemoryProfileTests(unittest.IsolatedAsyncioTestCase):
    def _manager_with_profile_change(self, old_profile, change_set):
        recent_message = json.dumps({
            "role": "user",
            "content": "请按我最新说明更新偏好",
            "ts": "2026-09-07T09:00:00",
            "metadata": {},
        })
        manager = MemoryManager.__new__(MemoryManager)
        manager._redis = _InMemoryRedis([recent_message])
        manager._profile = _InMemoryProfileCollection(old_profile)
        manager._episodic = _EmptyEpisodicCollection()
        manager._client = SimpleNamespace(messages=_ProfileChangeMessagesClient(change_set))
        manager._model = "test-model"
        manager._profile_locks = {}
        return manager

    async def test_get_profile_selects_latest_across_sessions(self):
        manager = MemoryManager.__new__(MemoryManager)
        manager._profile = _ProfileCollectionWithMultipleSessions()

        profile = await manager._get_profile("user-1")

        self.assertEqual({"version": "new"}, profile)

    async def test_update_profile_upserts_one_canonical_record_per_user(self):
        manager = MemoryManager.__new__(MemoryManager)
        manager._profile = _RecordingProfileCollection()
        manager._client = SimpleNamespace(messages=_FakeMessagesClient())
        manager._model = "test-model"
        manager._profile_locks = {}
        manager._get_working_memory = AsyncMock(return_value=[
            Message(role=MsgRole.USER, content="Prefer concise answers", timestamp=datetime(2026, 9, 7, 9, 0, 0)),
        ])

        await manager.update_profile("user-1", "session-2")

        self.assertEqual(1, len(manager._profile.upsert_calls))
        call = manager._profile.upsert_calls[0]
        self.assertEqual([manager._profile_doc_id("user-1")], call["ids"])
        self.assertEqual("session-2", call["metadatas"][0]["conv_id"])
        self.assertEqual("2026-09-07T09:00:00", call["metadatas"][0]["source_ts"])

    async def test_profile_change_adds_new_preference_without_losing_existing_preference(self):
        old_profile = {
            "profile_version": 2,
            "preferences": {
                "language": {
                    "value": "zh-CN",
                    "source": "用户明确要求中文",
                    "confidence": 1.0,
                    "updated_at": "2026-09-01T08:00:00",
                    "expires_at": None,
                }
            },
            "entities": {},
        }
        change_set = {
            "changes": [
                {
                    "operation": "add",
                    "section": "preferences",
                    "key": "answer_style",
                    "value": "concise",
                    "evidence": "用户要求简洁回答",
                    "confidence": 0.95,
                    "explicit": True,
                }
            ]
        }
        manager = self._manager_with_profile_change(old_profile, change_set)

        await manager.update_profile("user-1", "session-2")
        context = await manager.get_context("user-1", "session-2")

        self.assertEqual("zh-CN", context.user_profile["preferences"]["language"]["value"])
        self.assertEqual("concise", context.user_profile["preferences"]["answer_style"]["value"])

    async def test_explicit_high_confidence_change_replaces_an_existing_preference(self):
        old_profile = {
            "profile_version": 2,
            "preferences": {
                "answer_style": {
                    "value": "detailed",
                    "source": "用户过去要求详细回答",
                    "confidence": 0.95,
                    "updated_at": "2026-09-01T08:00:00",
                    "expires_at": None,
                }
            },
            "entities": {},
        }
        change_set = {
            "changes": [
                {
                    "operation": "replace",
                    "section": "preferences",
                    "key": "answer_style",
                    "value": "concise",
                    "evidence": "用户明确说以后都要简洁回答",
                    "confidence": 0.95,
                    "explicit": True,
                }
            ]
        }
        manager = self._manager_with_profile_change(old_profile, change_set)

        await manager.update_profile("user-1", "session-2")
        context = await manager.get_context("user-1", "session-2")

        self.assertEqual("concise", context.user_profile["preferences"]["answer_style"]["value"])

    async def test_explicit_high_confidence_remove_deletes_an_existing_preference(self):
        old_profile = {
            "profile_version": 2,
            "preferences": {
                "answer_style": {
                    "value": "detailed",
                    "source": "用户过去要求详细回答",
                    "confidence": 0.95,
                    "updated_at": "2026-09-01T08:00:00",
                    "expires_at": None,
                },
                "language": {
                    "value": "zh-CN",
                    "source": "用户要求中文",
                    "confidence": 1.0,
                    "updated_at": "2026-09-01T08:00:00",
                    "expires_at": None,
                },
            },
            "entities": {},
        }
        change_set = {
            "changes": [
                {
                    "operation": "remove",
                    "section": "preferences",
                    "key": "answer_style",
                    "evidence": "用户明确说不再保留回答风格偏好",
                    "confidence": 0.95,
                    "explicit": True,
                }
            ]
        }
        manager = self._manager_with_profile_change(old_profile, change_set)

        await manager.update_profile("user-1", "session-2")
        context = await manager.get_context("user-1", "session-2")

        self.assertNotIn("answer_style", context.user_profile["preferences"])
        self.assertEqual("zh-CN", context.user_profile["preferences"]["language"]["value"])

    async def test_sensitive_credential_change_is_rejected(self):
        old_profile = {
            "profile_version": 2,
            "preferences": {},
            "entities": {},
        }
        change_set = {
            "changes": [
                {
                    "operation": "add",
                    "section": "entities",
                    "key": "api_key",
                    "value": "sk-test1234",
                    "evidence": "用户在对话中提供了 API Key",
                    "confidence": 1.0,
                    "explicit": True,
                }
            ]
        }
        manager = self._manager_with_profile_change(old_profile, change_set)

        await manager.update_profile("user-1", "session-2")
        context = await manager.get_context("user-1", "session-2")

        self.assertNotIn("api_key", context.user_profile["entities"])

    async def test_explicit_remove_can_purge_a_legacy_sensitive_field(self):
        old_profile = {
            "profile_version": 2,
            "preferences": {},
            "entities": {
                "api_key": {
                    "value": "sk-test1234",
                    "source": "legacy_profile",
                    "confidence": 1.0,
                    "updated_at": "2026-09-01T08:00:00",
                    "expires_at": None,
                }
            },
        }
        change_set = {
            "changes": [
                {
                    "operation": "remove",
                    "section": "entities",
                    "key": "api_key",
                    "evidence": "用户明确要求删除已保存的 API Key",
                    "confidence": 1.0,
                    "explicit": True,
                }
            ]
        }
        manager = self._manager_with_profile_change(old_profile, change_set)

        await manager.update_profile("user-1", "session-2")
        context = await manager.get_context("user-1", "session-2")

        self.assertNotIn("api_key", context.user_profile["entities"])

    async def test_legacy_credentials_are_redacted_before_profile_is_sent_to_llm(self):
        old_profile = {
            "profile_version": 2,
            "preferences": {},
            "entities": {
                "api_key": {
                    "value": "sk-test1234",
                    "source": "legacy_profile",
                    "confidence": 1.0,
                    "updated_at": "2026-09-01T08:00:00",
                    "expires_at": None,
                }
            },
        }
        manager = self._manager_with_profile_change(old_profile, {"changes": []})
        messages_client = manager._client.messages

        await manager.update_profile("user-1", "session-2")

        self.assertNotIn("sk-test1234", messages_client.last_prompt)
        self.assertNotIn('"api_key"', messages_client.last_prompt)

    async def test_low_confidence_inference_is_not_added_to_profile(self):
        old_profile = {
            "profile_version": 2,
            "preferences": {},
            "entities": {},
        }
        change_set = {
            "changes": [
                {
                    "operation": "add",
                    "section": "preferences",
                    "key": "preferred_region",
                    "value": "华东",
                    "evidence": "用户提到过一次上海",
                    "confidence": 0.45,
                    "explicit": False,
                }
            ]
        }
        manager = self._manager_with_profile_change(old_profile, change_set)

        await manager.update_profile("user-1", "session-2")
        context = await manager.get_context("user-1", "session-2")

        self.assertNotIn("preferred_region", context.user_profile["preferences"])

    async def test_full_snapshot_response_cannot_bypass_field_level_merge_rules(self):
        old_profile = {
            "profile_version": 2,
            "preferences": {
                "language": {
                    "value": "zh-CN", "source": "用户要求中文", "confidence": 1.0,
                    "updated_at": "2026-09-01T08:00:00", "expires_at": None,
                }
            },
            "entities": {},
        }
        manager = self._manager_with_profile_change(
            old_profile,
            {"preferences": {}, "entities": {"api_key": "sk-test1234"}},
        )

        await manager.update_profile("user-1", "session-2")
        context = await manager.get_context("user-1", "session-2")

        self.assertEqual("zh-CN", context.user_profile["preferences"]["language"]["value"])
        self.assertNotIn("api_key", context.user_profile["entities"])

    async def test_legacy_preference_list_is_preserved_during_first_delta_update(self):
        old_profile = {
            "preferences": ["偏好中文回答", "关注项目部署状态"],
            "entities": {"product": ["FlowForge Cloud"]},
        }
        change_set = {
            "changes": [
                {
                    "operation": "add",
                    "section": "preferences",
                    "key": "answer_style",
                    "value": "concise",
                    "evidence": "用户要求简洁回答",
                    "confidence": 0.95,
                    "explicit": True,
                }
            ]
        }
        manager = self._manager_with_profile_change(old_profile, change_set)

        await manager.update_profile("user-1", "session-2")
        context = await manager.get_context("user-1", "session-2")

        preference_values = [item["value"] for item in context.user_profile["preferences"].values()]
        self.assertIn("偏好中文回答", preference_values)
        self.assertIn("关注项目部署状态", preference_values)
        self.assertEqual("concise", context.user_profile["preferences"]["answer_style"]["value"])

    async def test_concurrent_updates_for_same_user_preserve_both_changes(self):
        manager = MemoryManager.__new__(MemoryManager)
        manager._redis = _ConversationRedis()
        manager._profile = _InMemoryProfileCollection({
            "profile_version": 2, "preferences": {}, "entities": {},
        })
        manager._episodic = _EmptyEpisodicCollection()
        manager._client = SimpleNamespace(messages=_ConversationAwareMessagesClient())
        manager._model = "test-model"
        manager._profile_locks = {}

        await asyncio.gather(
            manager.update_profile("user-1", "english-session"),
            manager.update_profile("user-1", "style-session"),
        )
        context = await manager.get_context("user-1", "style-session")

        self.assertEqual("en-US", context.user_profile["preferences"]["language"]["value"])
        self.assertEqual("concise", context.user_profile["preferences"]["answer_style"]["value"])


if __name__ == "__main__":
    unittest.main()
