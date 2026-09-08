import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from fastapi.testclient import TestClient
from agents.agent_orchestrator import Request, SuccessAgent
from api.demo import create_demo_app
from core.business_intent import classify_business
from saas.agent_tools import business_tools
from saas.knowledge import SandboxKnowledge
from saas.service import Actor, SaaSService


class BusinessAgentTest(unittest.TestCase):
    def test_consultation_and_negation_never_get_write_intent(self):
        for text in ["怎么邀请成员", "Growth 多少钱", "不要升级套餐", "能否帮我邀请 a@b.example"]:
            self.assertEqual(classify_business(text)["action"], "read")
        self.assertEqual(classify_business("下周期升级到 Growth")["action"], "change")

    def test_live_tool_use_contract_with_fake_provider(self):
        async def run():
            path = Path(tempfile.mkdtemp(prefix="ff-tool-test-"))
            service = SaaSService(path / "state.db")
            kb = SandboxKnowledge(path / "chroma")
            actor = Actor("aurora_owner", "org_aurora", "owner")
            req = Request(message="下周期升级到 Growth", user_id=actor.user_id, conv_id="c", domain="billing", action="change")
            req.business_tools = business_tools(service, kb, actor, {"domains": ["billing"], "action": "change"})
            client = SimpleNamespace(messages=SimpleNamespace(create=AsyncMock(side_effect=[
                SimpleNamespace(content=[{"type": "tool_use", "id": "t1", "name": "preview_subscription_change", "input": {"target_plan": "growth_v1"}}]),
                SimpleNamespace(content=[{"type": "text", "text": "请确认操作卡，下周期生效。"}]),
            ])))
            response = await SuccessAgent(client, "fake-model").handle(req)
            self.assertTrue(response.success)
            self.assertEqual(response.tools_used, ["preview_subscription_change"])
            self.assertEqual(req.operations[0]["status"], "requires_confirmation")
            self.assertIsNone(service.read(actor, "subscription")["data"]["scheduled_plan_id"])
            names = [t["name"] for t in client.messages.create.call_args_list[0].kwargs["tools"]]
            self.assertNotIn("confirm", names)
            self.assertNotIn("advance_clock", names)
            denied = business_tools(service, kb, Actor("aurora_developer", "org_aurora", "developer"), {"domains": ["billing"], "action": "change"})
            self.assertNotIn("apply_subscription_change", denied)
        asyncio.run(run())

    def test_shared_chat_and_org_history(self):
        app = create_demo_app(tempfile.mkdtemp(prefix="ff-chat-test-"))
        with TestClient(app) as client:
            def login(name):
                token = client.post("/saas/demo/login", json={"user_id": f"{name}_owner", "org_id": f"org_{name}"}).json()["token"]
                return {"Authorization": "Bearer " + token}
            aurora, cedar = login("aurora"), login("cedar")
            self.assertEqual(client.post("/chat", json={"message": "查账单"}).status_code, 401)
            first = client.post("/chat", json={"message": "下周期升级到 Growth", "conv_id": "same"}, headers=aurora).json()
            self.assertEqual(first["engine"], "deterministic_demo")
            op = first["operations"][0]["operation_id"]
            self.assertEqual(client.post(f"/saas/operations/{op}/confirm", headers=cedar).status_code, 404)
            client.post(f"/saas/operations/{op}/confirm", headers=aurora)
            client.post("/saas/subscription/changes", json={"operation_id": op}, headers=aurora)
            next_turn = client.post("/chat", json={"message": "现在套餐是什么", "conv_id": "same"}, headers=aurora).json()
            self.assertIn("scheduled_plan_id", next_turn["response"])
            self.assertTrue(all(c["org_id"] in {"global", "org_aurora"} for c in next_turn["citations"]))
            other = client.post("/chat", json={"message": "现在套餐是什么", "conv_id": "same"}, headers=cedar).json()
            self.assertNotIn("org_aurora", str(other))
            self.assertTrue(all(t["org_id"] == "org_cedar" for t in client.get("/trace/tools", headers=cedar).json()["traces"]))
