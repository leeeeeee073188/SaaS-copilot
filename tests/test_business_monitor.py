import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from api.demo import create_demo_app
from agents.agent_orchestrator import SuccessAgent
from monitor.business_monitor import BusinessMonitor, Trace, current_trace, record_usage
from saas.service import Actor


class BusinessMonitorTest(unittest.TestCase):
    def make_app(self):
        with patch.dict("os.environ", {"SAAS_COPILOT_DEMO_LLM": "0", "SAAS_COPILOT_DEMO_REDIS_URL": ""}):
            return create_demo_app(tempfile.mkdtemp(prefix="ff-observability-"))

    @staticmethod
    def login(client, org="aurora", role="owner"):
        result = client.post("/saas/demo/login", json={"user_id": f"{org}_{role}", "org_id": f"org_{org}"})
        return {"Authorization": "Bearer " + result.json()["token"]}

    def test_persistent_trace_privacy_and_http_metrics(self):
        app = self.make_app()
        with TestClient(app) as client:
            owner = self.login(client)
            answer = client.post("/chat", headers=owner, json={"message": "邀请 private-marker@aurora.example 为 developer"})
            self.assertEqual(answer.status_code, 200)
            request_id = answer.headers["X-Request-ID"]
            self.assertEqual(answer.json()["request_id"], request_id)
            trace = client.get(f"/trace/tools/{request_id}", headers=owner).json()
            self.assertEqual(trace["status"], "ok")
            self.assertTrue(trace["operation_ids"])
            self.assertNotIn("private-marker", json.dumps(trace))
            self.assertIn("memory_write", [s["name"] for s in trace["spans"]])
            for who in (self.login(client, "cedar"), self.login(client, role="developer")):
                self.assertEqual(client.get(f"/trace/tools/{request_id}", headers=who).status_code, 404)
                self.assertEqual(client.get("/monitor", headers=who).json()["sample_count"], 0)
            reopened = BusinessMonitor(app.state.chat_service.monitor.path)
            actor = Actor("aurora_owner", "org_aurora", "owner")
            self.assertEqual(reopened.recent(actor, request_id=request_id)[0]["request_id"], request_id)
            self.assertEqual(client.get("/monitor").status_code, 401)
            with patch.dict("os.environ", {"SAAS_COPILOT_METRICS_TOKEN": "test-operator-token"}):
                self.assertEqual(client.get("/metrics", headers=owner).status_code, 403)
                metrics = client.get("/metrics", headers={"Authorization": "Bearer test-operator-token"}).text
                self.assertIn("flowforge_chat_duration_seconds_count", metrics)
                self.assertNotIn("org_aurora", metrics)
                self.assertNotIn(request_id, metrics)
                self.assertIn('route="/trace/tools/{request_id}"', metrics)
            self.assertEqual(client.get("/ready").status_code, 200)

    def test_rejection_timeout_and_failure_are_distinct(self):
        app = self.make_app()
        chat = app.state.chat_service
        with TestClient(app) as client:
            dev = self.login(client, role="developer")
            response = client.post("/chat", headers=dev, json={"message": "查账单"})
            self.assertEqual(response.json()["status"], "business_rejected")
            self.assertEqual(response.json()["tool_traces"][0]["outcome"], "rejected")
            self.assertEqual(client.get("/monitor", headers=dev).json()["technical_error_rate"], 0)
            with patch.object(chat.memory, "context", side_effect=RuntimeError("private-secret")):
                response = client.post("/chat", headers=dev, json={"message": "查用量"})
            self.assertEqual(response.status_code, 500)
            trace = client.get("/trace/tools/" + response.headers["X-Request-ID"], headers=dev).json()
            self.assertEqual(trace["status"], "error")
            self.assertNotIn("private-secret", response.text + json.dumps(trace))
            async def slow(*args):
                await asyncio.sleep(.2)
            chat.timeout_s = .02
            with patch.object(chat.memory, "context", side_effect=slow):
                response = client.post("/chat", headers=dev, json={"message": "查用量"})
            self.assertEqual(response.status_code, 504)
            trace = client.get("/trace/tools/" + response.headers["X-Request-ID"], headers=dev).json()
            self.assertEqual(trace["status"], "timeout")

    def test_concurrent_context_and_queue_timeout(self):
        async def run():
            chat = self.make_app().state.chat_service
            actors = [Actor(f"{org}_owner", f"org_{org}", "owner") for org in ("aurora", "cedar")]
            results = await asyncio.gather(*(chat.handle(a, "查账单") for a in actors))
            for actor, result in zip(actors, results):
                trace = chat.monitor.recent(actor)[0]
                self.assertEqual(trace["request_id"], result["request_id"])
                self.assertEqual(trace["org_id"], actor.org_id)
                self.assertEqual(len(trace["tool_traces"]), 1)
            chat._limit = asyncio.Semaphore(0)
            chat.timeout_s = .01
            with self.assertRaises(asyncio.TimeoutError):
                await chat.handle(actors[0], "查账单")
            trace = chat.monitor.recent(actors[0])[0]
            self.assertEqual(trace["status"], "timeout")
            self.assertEqual(trace["spans"][0]["name"], "queue")
        asyncio.run(run())

    def test_retention_alerts_provider_usage_and_storage_failure(self):
        with tempfile.TemporaryDirectory() as root:
            monitor = BusinessMonitor(Path(root) / "traces.db", max_traces=5)
            actor = Actor("u", "org", "owner")
            for index in range(7):
                trace = Trace("org", "u", "sensitive-conversation", "llm", status="error")
                trace.started -= 6
                if index == 0:
                    trace.started_at -= 8 * 86400
                token = current_trace.set(trace)
                record_usage(SimpleNamespace(usage=SimpleNamespace(input_tokens=10, output_tokens=2)), "support")
                current_trace.reset(token)
                monitor.finish(trace)
            summary = monitor.summary(actor)
            self.assertEqual(summary["sample_count"], 5)
            self.assertEqual({a["code"] for a in summary["alerts"]}, {"chat_error_rate", "chat_latency"})
            self.assertNotIn("sensitive-conversation", json.dumps(monitor.recent(actor)))
            self.assertIn('kind="input_tokens"} 70.0', monitor.metrics().decode())
            with patch.object(monitor, "connect", side_effect=RuntimeError("down")):
                monitor.finish(Trace("org", "u", "c", "deterministic_demo"))
            self.assertIn("flowforge_trace_storage_errors_total 1.0", monitor.metrics().decode())

    def test_completed_write_is_traceable_if_memory_write_fails(self):
        app = self.make_app()
        chat = app.state.chat_service
        with TestClient(app) as client:
            owner = self.login(client)
            with patch.object(chat.memory, "append", new=AsyncMock(side_effect=RuntimeError("memory down"))):
                response = client.post("/chat", headers=owner, json={"message": "邀请 retry@aurora.example 为 developer"})
            self.assertEqual(response.status_code, 500)
            trace = client.get("/trace/tools/" + response.headers["X-Request-ID"], headers=owner).json()
            self.assertEqual(trace["status"], "error")
            receipt = client.get("/saas/operations/" + trace["operation_ids"][0], headers=owner).json()
            self.assertEqual(receipt["status"], "pending")

    def test_model_tool_usage_and_readiness_failure(self):
        app = self.make_app()
        provider = SimpleNamespace(messages=SimpleNamespace(create=AsyncMock(side_effect=[
            SimpleNamespace(content=[{"type": "tool_use", "id": "call", "name": "get_subscription", "input": {}}], usage={"input_tokens": 10, "output_tokens": 2}),
            SimpleNamespace(content=[{"type": "text", "text": "当前套餐信息"}], usage={"input_tokens": 20, "output_tokens": 3}),
        ])))
        agent = SuccessAgent(provider, "fake-provider")
        async def run(req):
            response = await agent.handle(req)
            return SimpleNamespace(response=response.content, tool_traces=response.tool_traces,
                                   agent_types=[response.agent_type], success=response.success)
        chat = app.state.chat_service
        chat.orchestrator = SimpleNamespace(run=run)
        with TestClient(app) as client:
            owner = self.login(client)
            response = client.post("/chat", headers=owner, json={"message": "查订阅"})
            self.assertEqual(response.json()["status"], "ok")
            trace = client.get("/trace/tools/" + response.headers["X-Request-ID"], headers=owner).json()
            self.assertEqual(len(trace["tool_traces"]), 1)
            self.assertEqual(sum(row["input_tokens"] for row in trace["llm_usage"]), 30)
            self.assertEqual(len([s for s in trace["spans"] if s["name"] == "llm"]), 2)
            with patch.object(provider.messages, "create", side_effect=RuntimeError("provider unavailable")):
                response = client.post("/chat", headers=owner, json={"message": "查订阅"})
            self.assertEqual(response.json()["status"], "degraded")
            with patch.object(chat.knowledge.client, "heartbeat", side_effect=RuntimeError("offline")):
                response = client.get("/ready")
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["checks"]["chroma"], "unavailable")
