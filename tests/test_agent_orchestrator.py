import asyncio
import unittest

from agents.agent_orchestrator import (
    AgentProfile,
    AgentResponse,
    AgentType,
    DeliveryAgent,
    EscalationAgent,
    RenewalAgent,
    Request,
    ResponseComposer,
    SuccessAgent,
    SupportAgent,
    TriageAgent,
)
from agents.tools import build_shared_rag_tools
from core.intent_recognizer import IntentCategory, UrgencyLevel


class FakeClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

        class Messages:
            async def create(inner, **kwargs):
                self.calls.append(kwargs)
                if self.error:
                    raise self.error
                return self.response

        self.messages = Messages()


def make_request(**kwargs):
    values = {
        "message": "Webhook 返回 401，影响下周上线，而且客户使用量持续下降",
        "user_id": "analyst-1",
        "conv_id": "project-1",
        "intent": IntentCategory.INTEGRATION_WEBHOOK,
        "intent_group": "integration",
        "urgency": UrgencyLevel.HIGH,
        "intent_confidence": 0.94,
        "entities": {"error_code": ["401"], "project_id": ["proj-1"], "usage_metric": ["活跃下降"]},
    }
    values.update(kwargs)
    return Request(**values)


class AgentOrchestratorTests(unittest.TestCase):
    def test_profiles_and_tool_scopes_match_saas_domains(self):
        self.assertIsInstance(TriageAgent.profile, AgentProfile)
        self.assertNotEqual(DeliveryAgent.profile.role, SupportAgent.profile.role)
        self.assertIn("build_delivery_checklist", DeliveryAgent.profile.tool_scope)
        self.assertIn("lookup_error_code", SupportAgent.profile.tool_scope)
        self.assertIn("assess_adoption_signals", SuccessAgent.profile.tool_scope)
        self.assertIn("assess_renewal_risk", RenewalAgent.profile.tool_scope)

        scopes = {
            "triage": set(TriageAgent(FakeClient(), "test-model").get_tools()),
            "delivery": set(DeliveryAgent(FakeClient(), "test-model").get_tools()),
            "support": set(SupportAgent(FakeClient(), "test-model").get_tools()),
            "success": set(SuccessAgent(FakeClient(), "test-model").get_tools()),
            "renewal": set(RenewalAgent(FakeClient(), "test-model").get_tools()),
        }
        self.assertEqual(scopes["delivery"], {"build_delivery_checklist", "assess_launch_readiness"})
        self.assertEqual(scopes["support"], {"lookup_error_code", "build_diagnostic_plan"})
        self.assertFalse(scopes["delivery"] & scopes["support"])

    def test_role_packets_are_domain_specific(self):
        req = make_request()
        packets = [
            TriageAgent(FakeClient(), "test-model")._build_role_packet(req),
            DeliveryAgent(FakeClient(), "test-model")._build_role_packet(req),
            SupportAgent(FakeClient(), "test-model")._build_role_packet(req),
            SuccessAgent(FakeClient(), "test-model")._build_role_packet(req),
            RenewalAgent(FakeClient(), "test-model")._build_role_packet(req),
        ]
        self.assertEqual(len(set(packets)), 5)
        self.assertIn("delivery_fields", packets[1])
        self.assertIn("diagnostic_fields", packets[2])
        self.assertIn("success_fields", packets[3])
        self.assertIn("renewal_fields", packets[4])

    def test_escalation_is_non_llm_and_does_not_claim_external_action(self):
        client = FakeClient()
        result = asyncio.run(EscalationAgent(client, "test-model").handle(make_request(
            intent=IntentCategory.ESCALATION_RISK,
            urgency=UrgencyLevel.CRITICAL,
        )))
        self.assertTrue(result.success)
        self.assertTrue(result.escalate)
        self.assertIn("未发送外部通知", result.content)
        self.assertEqual(client.calls, [])

    def test_shared_rag_tool_is_available_to_all_agents(self):
        class RagManager:
            async def search_with_rewrite(self, tool_name, query, top_k=5):
                return type("Result", (), {
                    "success": True,
                    "data": [{"title": "上线指南", "content": "确认回滚与验收指标"}],
                    "cached": True,
                    "reranked": True,
                })()

        shared = build_shared_rag_tools(RagManager())
        for cls in (TriageAgent, DeliveryAgent, SupportAgent, SuccessAgent, RenewalAgent, EscalationAgent):
            agent = cls(FakeClient(), "test-model")
            agent.set_shared_tools(shared)
            self.assertIn("search_knowledge_base", agent.get_tools())

    def test_tool_use_round_trip_records_trace(self):
        class ToolUseBlock:
            type = "tool_use"
            id = "toolu_1"
            name = "lookup_error_code"
            input = {"error_code": "401"}

        class TextBlock:
            type = "text"
            text = "401 表示认证失败，请核对 Token 环境和有效期。"

        class ToolClient:
            def __init__(self):
                self.calls = []
                self.responses = [
                    type("Response", (), {"content": [ToolUseBlock()]})(),
                    type("Response", (), {"content": [TextBlock()]})(),
                ]

            class Messages:
                def __init__(self, owner):
                    self.owner = owner

                async def create(self, **kwargs):
                    self.owner.calls.append(kwargs)
                    return self.owner.responses.pop(0)

            @property
            def messages(self):
                return self.Messages(self)

        client = ToolClient()
        response = asyncio.run(SupportAgent(client, "test-model").handle(make_request()))
        self.assertTrue(response.success)
        self.assertEqual(response.tools_used, ["lookup_error_code"])
        self.assertEqual(response.tool_traces[0]["tool_name"], "lookup_error_code")
        self.assertTrue(response.tool_traces[0]["success"])
        self.assertIn("tool_result", str(client.calls[1]["messages"]))

    def test_composer_fallback_preserves_cross_domain_results(self):
        composer = ResponseComposer(FakeClient(error=RuntimeError("provider down")), "test-model")
        content = asyncio.run(composer.compose(make_request(), [
            AgentResponse(AgentType.SUPPORT, "先核对 401 的认证环境。", True),
            AgentResponse(AgentType.DELIVERY, "补齐上线回滚和验收方案。", True),
        ]))
        self.assertTrue(content.startswith("先核对"))
        self.assertIn("补充分析", content)
        self.assertIn("回滚", content)


if __name__ == "__main__":
    unittest.main()
