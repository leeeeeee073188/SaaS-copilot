"""One chat path for HTTP and evaluation; live LLM is an explicit demo option."""
import asyncio
import json
import re
import time
import uuid
from agents.agent_orchestrator import Request
from core.business_intent import classify_business
from saas.agent_tools import business_tools
from saas.service import BusinessError
from monitor.business_monitor import BusinessMonitor, Trace, current_trace, span, record_tool


class ChatService:
    def __init__(self, service, knowledge, memory, orchestrator=None):
        self.service, self.knowledge, self.memory, self.orchestrator = service, knowledge, memory, orchestrator
        self._limit = asyncio.Semaphore(4)
        self.monitor = BusinessMonitor(str(service.path) + ".traces.db")
        self.timeout_s = 90

    async def handle(self, actor, message, conv_id=None, request_id=None):
        started = time.monotonic()
        conv_id = conv_id or str(uuid.uuid4())
        trace = Trace(actor.org_id, actor.user_id, conv_id, "llm" if self.orchestrator else "deterministic_demo")
        if request_id:
            trace.request_id = request_id
        token = current_trace.set(trace)
        async def admitted():
            with span("queue"):
                await self._limit.acquire()
            try:
                return await self._handle(actor, message, conv_id, started)
            finally:
                self._limit.release()
        try:
            output = await asyncio.wait_for(admitted(), timeout=self.timeout_s)
            if trace.status == "ok":
                trace.status = "degraded" if any(t["outcome"] == "error" for t in trace.tools) else "business_rejected" if any(t["outcome"] == "rejected" for t in trace.tools) else "ok"
            output.update(status=trace.status, tool_traces=trace.tools, tools_used=[t["tool_name"] for t in trace.tools])
            return output
        except asyncio.TimeoutError:
            trace.status = "timeout"
            raise
        except asyncio.CancelledError:
            trace.status = "cancelled"
            raise
        except BusinessError:
            trace.status = "business_rejected"
            raise
        except Exception:
            trace.status = "error"
            raise
        finally:
            current_trace.reset(token)
            await asyncio.to_thread(self.monitor.finish, trace)

    async def _handle(self, actor, message, conv_id, started):
        trace = current_trace.get()
        with span("authorization"):
            await asyncio.to_thread(self.service.read, actor, "entitlements")
        with span("memory_read"):
            history, context = await self.memory.context(actor, conv_id, message)
        with span("intent"):
            decision = classify_business(message, history)
            trace.domain, trace.action = decision["domain"], decision["action"]
        with span("retrieval"):
            evidence = await asyncio.to_thread(self.knowledge.search, message, actor.org_id, 3)
            trace.retrieval = {"count": len(evidence), "sources": [e["source_id"] for e in evidence], "embedding": self.knowledge.embedding}
        req = Request(message=message, user_id=actor.user_id, conv_id=conv_id, history=history, actor=actor,
                      request_id=trace.request_id,
                      context=json.dumps({"memory": context, "evidence": evidence}, ensure_ascii=False),
                      evidence=evidence, **decision)
        req.business_tools = business_tools(self.service, self.knowledge, actor, decision)
        with span("agent"):
            if self.orchestrator:
                result = await self.orchestrator.run(req)
                answer, traces, agents = result.response, result.tool_traces, [a.value for a in result.agent_types]
                if not getattr(result, "success", True):
                    trace.status = "degraded"
            else:
                answer, traces = await self._offline(req)
                agents = [decision["domain"]]
        trace.operation_ids = [op["operation_id"] for op in req.operations if "operation_id" in op]
        with span("memory_write"):
            await self.memory.append(actor, conv_id, message, answer)
        output = {"response": answer, "conv_id": conv_id, "request_id": req.request_id,
                  "intent": decision["domain"], **decision, "agent_type": agents[0] if agents else decision["domain"],
                  "agent_types": agents, "tools_used": [t["tool_name"] for t in traces], "tool_traces": traces,
                  "citations": list({e["source_id"]: e for e in req.evidence}.values()), "operations": req.operations,
                  "knowledge_used": bool(evidence), "latency_ms": round((time.monotonic() - started) * 1000, 1),
                  "engine": "llm" if self.orchestrator else "deterministic_demo", "org_id": actor.org_id}
        return output

    async def _offline(self, req):
        calls = []

        async def call(name, args=None):
            if name not in req.business_tools:
                record_tool(name, "unavailable", "denied", time.monotonic())
                return {"success": False, "status": "denied", "error": "当前角色或任务无此操作权限"}
            data = await req.business_tools[name].handler(req, args or {})
            calls.append({"tool_name": name, "success": data.get("success", False), "status": data.get("status"), "input": args or {}})
            return data

        text = req.message.lower()
        if req.action == "read" and any(w in text for w in ("支持", "功能", "介绍", "怎么", "如何")):
            data = await call("get_plan_catalog" if any(w in text for w in ("starter", "growth", "套餐")) else "get_entitlements")
            answer = "产品规则见下方引用；当前目录或权益：" + json.dumps(data, ensure_ascii=False)
        elif req.domain == "billing" and req.action in {"change", "preview"}:
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
