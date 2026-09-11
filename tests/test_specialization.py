import asyncio
import json
import tempfile
import unittest
from collections import deque
from dataclasses import replace
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock

from agents.agent_orchestrator import (
    AgentOrchestrator, AgentType, Request, ResponseComposer, SupportAgent, SuccessAgent, TriageAgent,
)
from agents.tools import make_tool
from saas.specialization import parse_findings, specialize
from saas.agent_tools import business_tools
from saas.service import Actor, SaaSService


def text(value):
    return SimpleNamespace(content=[{"type": "text", "text": value}])


def finding(summary, sources):
    return json.dumps({"summary": summary, "evidence_ids": sources, "missing": [], "next_steps": ["核对生效时间"]})


def request(action="read"):
    async def handler(req, args):
        name = args.get("source", "get_usage")
        req.evidence.append({"source_id": name, "content": "verified"})
        return {"success": True, "source_id": name}
    tools = {}
    for name, domain, effect in [
        ("get_usage", "integration", "read"), ("get_subscription", "billing", "read"),
        ("list_members", "account", "read"), ("get_entitlements", "product", "read"),
        ("preview_subscription_change", "billing", "prepare"), ("invite_member", "account", "write"),
    ]:
        tools[name] = replace(make_tool(name, name, {"source": {"type": "string"}}, handler), domain=domain, effect=effect)
    evidence = [{"source_id": f"ff-{domain}-01", "content": domain} for domain in ("integration", "billing", "product")]
    return Request("API 429 是否套餐不够，升级后能解决吗", "u", "c", domain="integration",
                   domains=["integration", "billing"], action=action,
                   context=json.dumps({"memory": "请简洁回答", "evidence": evidence}),
                   business_tools=tools, evidence=evidence)


def orchestrator(client):
    obj = AgentOrchestrator.__new__(AgentOrchestrator)
    obj._pool = {cls.agent_type: [cls(client, "fake")] for cls in (SupportAgent, SuccessAgent, TriageAgent)}
    obj._composer = ResponseComposer(client, "fake")
    obj._recent_tool_traces = deque(maxlen=20)
    return obj


class SpecializationTests(unittest.TestCase):
    def test_scopes_narrow_tools_context_and_write_owner(self):
        req = request()
        support, billing = specialize(req, "support"), specialize(req, "success")
        self.assertNotIn("get_subscription", support.business_tools)
        self.assertNotIn("get_usage", billing.business_tools)
        self.assertNotIn("billing-01", support.context)
        self.assertNotIn("integration-01", billing.context)
        self.assertIn("请简洁回答", json.loads(support.context)["memory"])
        self.assertIsNot(support.evidence, billing.evidence)
        self.assertEqual(req.domains, ["integration", "billing"])
        denied = replace(req, business_tools={"get_usage": req.business_tools["get_usage"]})
        self.assertEqual(specialize(denied, "success").business_tools, {})
        change = replace(req, domain="billing", domains=["billing", "account"], action="change")
        scoped = specialize(change, "success")
        self.assertIn("preview_subscription_change", scoped.business_tools)
        self.assertNotIn("invite_member", scoped.business_tools)
        parallel = specialize(replace(req, specialist_output=True), "success")
        self.assertTrue(all(t.effect == "read" for t in parallel.business_tools.values()))

    def test_result_contract_rejects_invented_sources_and_wrong_types(self):
        self.assertEqual(parse_findings(finding("缺少日志", []), [])["summary"], "缺少日志")
        for content in [finding("编造", ["unknown"]), '{"summary":"缺少字段"}',
                        '{"summary":"x","evidence_ids":"x","missing":[],"next_steps":[]}']:
            with self.assertRaises(ValueError):
                parse_findings(content, [])

    def test_parallel_specialists_execute_distinct_tools_and_composer_contract(self):
        async def run():
            calls = []
            async def create(**kwargs):
                calls.append(kwargs)
                system = kwargs.get("system", "")
                if "汇总支持答复" in system:
                    packet = json.loads(kwargs["messages"][0]["content"])
                    self.assertEqual([r["domains"] for r in packet["results"]], [["integration"], ["billing"]])
                    return text("429 需核对限流原因；预约升级下周期生效。")
                support = "负责领域：integration" in system
                name = "get_usage" if support else "get_subscription"
                names = {t["name"] for t in kwargs["tools"]}
                self.assertNotIn("get_subscription" if support else "get_usage", names)
                if not any(isinstance(m["content"], list) for m in kwargs["messages"]):
                    await asyncio.sleep(.001)
                    return SimpleNamespace(content=[{"type": "tool_use", "id": name, "name": name, "input": {"source": name}}])
                return text(finding("分钟限流" if support else "下周期生效", [name]))
            client = SimpleNamespace(messages=SimpleNamespace(create=create))
            req = request()
            result = await orchestrator(client).run(req)
            self.assertTrue(result.success)
            self.assertEqual(result.supporting_agents, [AgentType.SUCCESS])
            self.assertEqual(set(result.tools_used), {"get_usage", "get_subscription"})
            self.assertTrue({"get_usage", "get_subscription"} <= {e["source_id"] for e in req.evidence})
            self.assertEqual(req.domains, ["integration", "billing"])
            self.assertEqual(len(calls), 5)
        asyncio.run(run())

    def test_denied_cross_role_tool_is_not_executed(self):
        async def run():
            req = request()
            forbidden = AsyncMock()
            req.business_tools["get_subscription"] = replace(req.business_tools["get_subscription"], handler=forbidden)
            client = SimpleNamespace(messages=SimpleNamespace(create=AsyncMock(side_effect=[
                SimpleNamespace(content=[{"type": "tool_use", "id": "x", "name": "get_subscription", "input": {}}]),
                text("由订阅领域核验"),
            ])))
            response = await SupportAgent(client, "fake").handle(req)
            forbidden.assert_not_awaited()
            self.assertFalse(response.tool_traces[0]["success"])
        asyncio.run(run())

    def test_real_business_tools_and_retrieval_obey_specialist_scope(self):
        async def run():
            with tempfile.TemporaryDirectory() as directory:
                service = SaaSService(Path(directory) / "state.db")
                actor = Actor("aurora_owner", "org_aurora", "owner")
                req = request()
                kb = SimpleNamespace(search=lambda *args: list(req.evidence))
                req.business_tools = business_tools(service, kb, actor, {"domains": req.domains, "action": "read"})
                scoped = specialize(req, "support")
                found = await scoped.business_tools["search_product_knowledge"].handler(scoped, {"query": "API 套餐"})
                self.assertNotIn("ff-billing-01", {e["source_id"] for e in found["results"]})
                async def create(**kwargs):
                    if "汇总支持答复" in kwargs["system"]:
                        return text("结合用量与订阅核验")
                    support = "负责领域：integration" in kwargs["system"]
                    name = "get_usage" if support else "get_subscription"
                    results = [m for m in kwargs["messages"] if m["role"] == "user" and isinstance(m["content"], list)]
                    if not results:
                        return SimpleNamespace(content=[{"type": "tool_use", "id": name, "name": name, "input": {}}])
                    data = json.loads(results[0]["content"][0]["content"])
                    self.assertTrue(data["success"])
                    self.assertEqual(data["source_id"], name)
                    self.assertIn("data", data)
                    return text(finding("核验完成", [data["source_id"]]))
                result = await orchestrator(SimpleNamespace(messages=SimpleNamespace(create=create))).run(req)
                self.assertTrue(result.success)
                self.assertEqual(set(result.tools_used), {"get_usage", "get_subscription"})
                self.assertEqual(req.operations, [])
                self.assertIsNone(service.read(actor, "subscription")["data"]["scheduled_plan_id"])
        asyncio.run(run())

    def test_partial_failure_and_composer_failure_preserve_evidence(self):
        async def run():
            async def create(**kwargs):
                if "汇总支持答复" in kwargs["system"]:
                    raise RuntimeError("composer unavailable")
                return text(finding("已核验", ["ff-product-01"]))
            client = SimpleNamespace(messages=SimpleNamespace(create=create))
            obj = orchestrator(client)
            result = await obj.run(request())
            self.assertIn("[ff-product-01]", result.response)
            self.assertTrue(result.success)
            obj._pool[AgentType.SUPPORT] = []
            result = await obj.run(request())
            self.assertFalse(result.success)
            self.assertIn("部分领域未完成核验", result.response)
            self.assertIn("[ff-product-01]", result.response)
            self.assertEqual(obj._pool[AgentType.TRIAGE][0].stats.total, 0)
        asyncio.run(run())

    def test_same_agent_requests_do_not_mix_traces(self):
        async def run():
            async def create(**kwargs):
                await asyncio.sleep(.001)
                return text("核验说明")
            agent = SupportAgent(SimpleNamespace(messages=SimpleNamespace(create=create)), "fake")
            first, second = request(), request()
            results = await asyncio.gather(agent.handle(first), agent.handle(second))
            self.assertTrue(all(r.success for r in results))
            self.assertIsNot(first.evidence, second.evidence)
            self.assertEqual(agent.stats.total, 2)
        asyncio.run(run())
