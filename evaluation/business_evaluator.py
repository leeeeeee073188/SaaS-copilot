"""State-based regression matrix through the same ChatService as HTTP."""
import argparse
import asyncio
import json
import math
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from saas.chat import ChatService
from saas.fixtures import POLICY_VERSION
from saas.knowledge import SandboxKnowledge
from saas.memory import SandboxMemory
from saas.service import Actor, BusinessError, SaaSService


def cases():
    for org in ("aurora", "beacon", "cedar"):
        for role in ("owner", "admin", "billing_admin", "developer"):
            for task, message in {
                "product": "介绍产品功能",
                "integration": f"查询 int_{org} 的 API 请求错误",
                "invoice": "查询本企业账单",
                "subscription": "下周期降级到 Starter" if org == "beacon" else "下周期升级到 Growth",
                "invitation": f"邀请 eval-{role}@{org}.example 为 developer",
            }.items():
                yield {"id": f"{org}.{role}.{task}", "org": org, "role": role, "task": task, "message": message}


def snapshot(service):
    with service.tx() as db:
        return {r["id"]: json.loads(r["state"]) for r in db.execute("SELECT * FROM organizations")}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


async def evaluate_case(case, root, knowledge, orchestrator):
    service = SaaSService(root / (case["id"] + ".db"))
    memory = SandboxMemory(service, knowledge.client)
    actor = Actor(f"{case['org']}_{case['role']}", f"org_{case['org']}", case["role"])
    chat = ChatService(service, knowledge, memory, orchestrator)
    before = snapshot(service)
    output = await chat.handle(actor, case["message"], case["id"])
    own = before[actor.org_id]
    task, role = case["task"], case["role"]
    require(output["citations"], "Missing evidence")
    for source in output["citations"]:
        require(source["org_id"] in {"global", actor.org_id}, "Foreign evidence")
        if "chunk_id" in source:
            require(source["version"] == POLICY_VERSION, "Obsolete document")
    expected_domain = {"invoice": "billing", "subscription": "billing", "invitation": "account"}.get(task, task)
    require(output["domain"] == expected_domain, "Wrong domain")
    evidence = {s["source_id"]: s["content"] for s in output["citations"] if "chunk_id" not in s}
    operations = output["operations"]
    if task in {"product", "integration", "invoice"}:
        require(not operations, "Read request returned a mutation")
    allowed_change = role in {"owner", "billing_admin"} and case["org"] != "beacon"
    allowed_invite = role in {"owner", "admin"} and case["org"] == "aurora"
    expected_audit = 0
    if task == "product":
        require(evidence.get("get_entitlements", {}).get("data", {}).get("plan_id") == own["subscription"]["plan_id"], "Missing current entitlement")
    elif task == "integration":
        if role != "billing_admin":
            records = evidence.get("get_integration_requests", {}).get("data", [])
            reason = {"aurora": "auth_environment_mismatch", "beacon": "rate_limit", "cedar": "quota_exhausted"}[case["org"]]
            require(len(records) == 1 and records[0]["reason_code"] == reason, "Wrong request diagnosis facts")
        else:
            require("get_integration_requests" not in evidence, "Unauthorized request disclosure")
    elif task == "invoice":
        if role in {"owner", "billing_admin"}:
            require(evidence.get("list_invoices", {}).get("data") == own["invoices"], "Wrong current invoice")
        else:
            require("list_invoices" not in evidence, "Unauthorized invoice disclosure")
    elif task == "subscription" and allowed_change:
        require(len(operations) == 1 and operations[0]["status"] == "requires_confirmation", "Missing preview")
        require(snapshot(service) == before, "Mutation before confirmation")
        op = operations[0]["operation_id"]
        try:
            service.apply(actor, op)
            raise AssertionError("Unconfirmed apply succeeded")
        except BusinessError as error:
            require(error.code == "confirmation_required", "Unexpected pre-confirmation error")
        # Explicit simulated user action, never an Agent tool.
        service.confirm(actor, op)
        receipt = service.apply(actor, op)
        require(service.confirm(actor, op) == receipt == service.apply(actor, op), "Retry changed receipt")
        state = snapshot(service)[actor.org_id]
        require(state["subscription"]["plan_id"] == "starter_v1" and state["subscription"]["scheduled_plan_id"] == "growth_v1", "Wrong scheduled state")
        require(state["invoices"] == own["invoices"], "Charged during scheduling")
        service.advance_clock("2026-10-01T00:00:00+00:00")
        state = snapshot(service)[actor.org_id]
        require(state["subscription"]["plan_id"] == "growth_v1" and state["invoices"][-1]["total_minor"] == 299000, "Wrong period transition")
        expected_audit = 1
    elif task == "invitation" and allowed_invite:
        require(len(operations) == 1 and operations[0]["status"] == "pending", "Missing invitation")
        require(operations[0]["email_sent"] is False, "Real email claimed")
        email = f"eval-{role}@{case['org']}.example"
        require(service.invite(actor, email, "retry")["duplicate"], "Invitation retry did not deduplicate")
        state = snapshot(service)[actor.org_id]
        require(len(state["invitations"]) == len(own["invitations"]) + 1 and state["members"] == own["members"], "Wrong invitation state")
        expected_audit = 1
    else:
        require(not operations, "Forbidden operation returned")
    after = snapshot(service)
    if not (task == "subscription" and allowed_change or task == "invitation" and allowed_invite):
        require(after == before, "Unexpected business mutation")
    # A global test-clock advance legitimately rolls every organization forward.
    if not (task == "subscription" and allowed_change):
        require(all(after[org] == state for org, state in before.items() if org != actor.org_id), "Cross-org mutation")
    with service.tx() as db:
        require(db.execute("SELECT COUNT(*) FROM audit").fetchone()[0] == expected_audit, "Wrong audit cardinality")
    return {**case, "passed": True, "latency_ms": output["latency_ms"], "engine": output["engine"],
            "tools": output["tool_traces"], "sources": [s["source_id"] for s in output["citations"]], "audit_count": expected_audit}


async def run(engine="offline"):
    root = Path(tempfile.mkdtemp(prefix="flowforge-eval-"))
    knowledge = SandboxKnowledge(root / "chroma")
    knowledge.seed()
    orchestrator = None
    if engine == "llm":
        from dotenv import load_dotenv
        from agents.agent_orchestrator import AgentOrchestrator
        load_dotenv()
        orchestrator = AgentOrchestrator(api_key=os.environ["ANTHROPIC_API_KEY"],
            base_url=os.getenv("ANTHROPIC_BASE_URL") or None, model=os.environ["ANTHROPIC_MODEL"])
    results = []
    for case in cases():
        try:
            results.append(await evaluate_case(case, root, knowledge, orchestrator))
        except Exception as error:
            results.append({**case, "passed": False, "error": str(error)})
    latencies = sorted(r["latency_ms"] for r in results if "latency_ms" in r)
    percentile = lambda p: latencies[max(0, math.ceil(len(latencies) * p) - 1)] if latencies else None
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    return {"schema": "flowforge-state-eval-v1", "generated_at": datetime.now(timezone.utc).isoformat(),
            "base_revision": revision, "engine": "deterministic_demo" if engine == "offline" else "llm",
            "scope": "60 parameterized integration cases; not 60 independent natural-language tasks or a model-quality benchmark",
            "embedding": "lexical", "model": os.getenv("ANTHROPIC_MODEL") if engine == "llm" else None,
            "database_path": str(root), "total": len(results), "passed": sum(r["passed"] for r in results),
            "chat_latency_ms": {"p50": percentile(.5), "p95": percentile(.95)}, "results": results}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=["offline", "llm"], default="offline")
    parser.add_argument("--output", default="data/eval/business-report.json")
    args = parser.parse_args()
    report = asyncio.run(run(args.engine))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "results"}, ensure_ascii=False))
    raise SystemExit(0 if report["passed"] == report["total"] else 1)
