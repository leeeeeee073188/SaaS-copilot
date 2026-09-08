"""One chat path for HTTP and evaluation; live LLM is an explicit demo option."""
import asyncio
import json
import re
import time
import uuid
from agents.agent_orchestrator import Request
from core.business_intent import classify_business
from core.intent_recognizer import IntentCategory, UrgencyLevel
from saas.agent_tools import business_tools


class ChatService:
    def __init__(self, service, knowledge, memory, orchestrator=None):
        self.service, self.knowledge, self.memory, self.orchestrator = service, knowledge, memory, orchestrator
        self._limit = asyncio.Semaphore(4)
        self.traces = []

    async def handle(self, actor, message, conv_id=None):
        started = time.monotonic()
        async with self._limit:
            return await asyncio.wait_for(self._handle(actor, message, conv_id, started), timeout=90)

    async def _handle(self, actor, message, conv_id, started):
        await asyncio.to_thread(self.service.read, actor, "entitlements")
        conv_id = conv_id or str(uuid.uuid4())
        history, context = await self.memory.context(actor, conv_id, message)
        decision = classify_business(message, history)
        evidence = await asyncio.to_thread(self.knowledge.search, message, actor.org_id, 3)
        req = Request(message=message, user_id=actor.user_id, conv_id=conv_id, history=history, actor=actor,
                      context=json.dumps({"memory": context, "evidence": evidence}, ensure_ascii=False),
                      intent=IntentCategory.QUERY, intent_group=decision["domain"], urgency=UrgencyLevel.MEDIUM,
                      evidence=evidence, **decision)
        req.business_tools = business_tools(self.service, self.knowledge, actor, decision)
        if self.orchestrator:
            result = await self.orchestrator.run(req)
            answer, traces, agents = result.response, result.tool_traces, [a.value for a in result.agent_types]
        else:
            answer, traces = await self._offline(req)
            agents = [decision["domain"]]
        await self.memory.append(actor, conv_id, message, answer)
        output = {"response": answer, "conv_id": conv_id, "request_id": req.request_id,
                  "intent": decision["domain"], **decision, "agent_type": agents[0] if agents else decision["domain"],
                  "agent_types": agents, "tools_used": [t["tool_name"] for t in traces], "tool_traces": traces,
                  "citations": list({e["source_id"]: e for e in req.evidence}.values()), "operations": req.operations,
                  "knowledge_used": bool(evidence), "latency_ms": round((time.monotonic() - started) * 1000, 1),
                  "engine": "llm" if self.orchestrator else "deterministic_demo", "org_id": actor.org_id}
        self.traces.append({**output, "actor_id": actor.user_id})
        self.traces = self.traces[-100:]
        return output

    async def _offline(self, req):
        calls = []

        async def call(name, args=None):
            if name not in req.business_tools:
                return {"success": False, "status": "denied", "error": "当前角色或任务无此操作权限"}
            data = await req.business_tools[name].handler(req, args or {})
            calls.append({"tool_name": name, "success": data.get("success", False), "status": data.get("status"), "input": args or {}})
            return data

        text = req.message.lower()
        if req.domain == "billing" and req.action in {"change", "preview"}:
            target = "growth_v1" if "growth" in text else "starter_v1" if "starter" in text else None
            if not target:
                return "请明确目标套餐 Starter 或 Growth；首版只支持下周期生效。", calls
            data = await call("preview_subscription_change", {"target_plan": target})
            answer = "请确认操作卡：下周期生效，当期套餐未变。" if data.get("success") else data["error"]
        elif req.domain == "account" and req.action == "change":
            match = re.search(r"[^\s@<>，。]+@[^\s@<>，。]+\.[a-zA-Z]+", req.message)
            if not match:
                return "请明确邀请邮箱，邀请角色固定 developer。", calls
            data = await call("invite_member", {"email": match.group()})
            answer = "已创建本地待接受邀请，未发送真实邮件。" if data.get("success") else data["error"]
        elif req.domain == "integration":
            match = re.search(r"int_[a-z0-9_]+", text)
            if match:
                data = await call("get_integration_requests", {"integration_id": match.group()})
            else:
                data = await call("list_integrations")
            usage = await call("get_usage")
            answer = "诊断事实：" + json.dumps(data, ensure_ascii=False) + "\n用量：" + json.dumps(usage, ensure_ascii=False)
        elif req.domain == "account":
            data = await call("list_members")
            answer = json.dumps(data, ensure_ascii=False)
        elif req.domain == "billing":
            # Public feature/price questions do not require private billing access.
            name = "get_plan_catalog" if any(w in text for w in ("多少钱", "支持", "价格")) else "list_invoices" if "账单" in text or "invoice" in text else "get_subscription"
            data = await call(name)
            answer = json.dumps(data, ensure_ascii=False)
        else:
            data = await call("get_entitlements")
            answer = json.dumps(data, ensure_ascii=False)
        citations = "\n".join(f"[{e['source_id']}] {e['content']}" for e in req.evidence[:2] if isinstance(e.get("content"), str))
        return "【离线演示，非模型生成】\n" + answer + "\n" + citations, calls
