"""Adapters attach trusted actors and retain structured evidence and receipts."""
import asyncio
import re
import time
from dataclasses import replace
from agents.tools import make_tool
from saas.service import BusinessError, PERMISSIONS
from monitor.business_monitor import record_tool
from saas.specialization import relevant_evidence


def business_tools(service, knowledge, actor, decision):
    tools = {}

    def register(name, domain, effect, permission, description, properties, required, fn):
        if permission and permission not in PERMISSIONS.get(actor.role, set()):
            return
        if effect in {"prepare", "write"} and decision["action"] not in {"change", "preview"}:
            return
        if effect == "write" and decision["action"] != "change":
            return
        if domain not in decision["domains"] and domain != "product":
            return

        async def handler(req, args):
            started, status = time.monotonic(), "error"
            try:
                await asyncio.to_thread(service.read, actor, "entitlements")
                data = await asyncio.wait_for(asyncio.to_thread(fn, req, args), timeout=10)
                if name == "search_product_knowledge":
                    if req.specialist_task:
                        data["results"] = [item for item in data["results"]
                                           if relevant_evidence(item, req.domains, req.business_tools)]
                    req.evidence.extend(item for item in data["results"] if item not in req.evidence)
                elif effect in {"prepare", "write"}:
                    req.operations.append(data)
                else:
                    data = {**data, "source_id": name}
                    req.evidence.append({"source_id": name, "content": data, "org_id": actor.org_id})
                status = "ok"
                return {**data, "success": True}
            except BusinessError as ex:
                status = ex.code
                return {"success": False, "status": ex.code, "error": str(ex)}
            except asyncio.TimeoutError:
                status = "unknown" if effect == "write" else "timeout"
                return {"success": False, "status": status, "error": "超时；写操作请先查询回执"}
            except asyncio.CancelledError:
                status = "unknown" if effect == "write" else "cancelled"
                raise
            finally:
                record_tool(name, effect, status, started)

        spec = make_tool(name, description, properties, handler, required)
        tools[name] = replace(spec, domain=domain, effect=effect, permission=permission)

    string = {"type": "string"}
    register("search_product_knowledge", "product", "read", "", "检索本组织可见产品规则，返回引用来源。",
             {"query": string}, ["query"], lambda req, a: {"results": knowledge.search(a["query"], actor.org_id), "status": "ok"})
    for name, resource, domain, permission in [
        ("get_plan_catalog", "plans", "product", ""), ("get_entitlements", "entitlements", "product", ""),
        ("get_usage", "usage", "integration", ""), ("list_integrations", "integrations", "integration", "integration.read"),
        ("get_subscription", "subscription", "billing", "billing.read"), ("list_invoices", "invoices", "billing", "billing.read"),
        ("list_members", "members", "account", ""),
    ]:
        register(name, domain, "read", permission, f"读取本组织当前 {resource}。", {}, [],
                 lambda req, a, resource=resource: service.read(actor, resource))
    register("get_integration_requests", "integration", "read", "integration.read", "读取本组织集成的脱敏请求记录。",
             {"integration_id": string}, ["integration_id"], lambda req, a: service.read(actor, "requests", a["integration_id"]))
    register("preview_subscription_change", "billing", "prepare", "subscription.change", "预览下周期套餐变更；当前订阅不变，需确认操作卡。",
             {"target_plan": {"type": "string", "enum": ["starter_v1", "growth_v1"]}}, ["target_plan"], lambda req, a: service.preview(actor, a["target_plan"]))
    register("apply_subscription_change", "billing", "write", "subscription.change", "提交用户已在界面确认的操作；不能自行确认。",
             {"operation_id": string}, ["operation_id"], lambda req, a: service.apply(actor, a["operation_id"]))
    register("get_operation", "product", "read", "", "查询当前用户操作的状态或回执。",
             {"operation_id": string}, ["operation_id"], lambda req, a: service.operation(actor, a["operation_id"]))

    def invite(req, args):
        email = args["email"].lower()
        if re.search(r"(为|作为|as).*(管理员|admin|owner|billing)", req.message, re.I):
            raise BusinessError("unsupported_role", "首版仅支持 developer 邀请，请明确角色")
        explicit = re.search(r"邀请|invite", req.message, re.I) and email in req.message.lower()
        if decision["action"] != "change" or not explicit:
            raise BusinessError("clarification_required", "请明确要求邀请该邮箱为 developer")
        return service.invite(actor, email, "chat:" + service._hash(req.request_id + email))

    register("invite_member", "account", "write", "member.invite", "明确邀请请求中完整邮箱对应的 developer；创建本地 pending 邀请。",
             {"email": string}, ["email"], invite)
    return tools
