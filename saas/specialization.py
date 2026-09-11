"""V2 specialist contracts; task scope can only narrow authorized tools."""
import json
from dataclasses import replace


DOMAIN_AGENTS = {"product": "triage", "integration": "support", "billing": "success", "account": "success"}
TASKS = {
    "product": "解释产品功能、配置步骤和适用边界；不诊断请求故障或代查账单。",
    "integration": "核验集成、脱敏请求记录与用量，区分认证错误、分钟限流和账期配额；给出排查步骤，不替订阅领域判断升级生效。",
    "billing": "核验当前订阅、账单和套餐规则，区分当前权益与下周期变更；不能把预约升级说成即时解除限制。",
    "account": "核验成员、席位和角色边界；仅在明确邀请请求下创建 developer 邀请，不提升角色。",
}
COMMON_TOOLS = {"search_product_knowledge", "get_plan_catalog", "get_entitlements", "get_operation"}


def relevant_evidence(item, domains, tools):
    source = item.get("source_id", "")
    if source.startswith("ff-"):
        return source.split("-")[1] in {*domains, "product"}
    return source in tools or source.endswith("-snapshot")


def specialize(req, agent_type):
    requested = list(dict.fromkeys([req.domain, *req.domains]))
    if req.action in {"change", "preview"}:
        requested = [req.domain]  # Only the primary domain owns mutations.
    domains = [d for d in requested if DOMAIN_AGENTS.get(d) == agent_type]
    if not domains:
        raise ValueError("Agent has no assigned business domain")
    tools = {name: tool for name, tool in req.business_tools.items()
             if (tool.domain in domains or name in COMMON_TOOLS)
             and (tool.effect == "read" or (not req.specialist_output
                  and req.action in {"change", "preview"}
                  and (tool.effect != "write" or req.action == "change")))}
    evidence = [item for item in req.evidence if relevant_evidence(item, domains, tools)]
    try:
        context = json.loads(req.context)
    except (ValueError, TypeError):
        context = {"memory": req.context}
    if not isinstance(context, dict):
        context = {"memory": context}
    context = {**context, "evidence": evidence}
    return replace(req, domain=domains[0], domains=domains, business_tools=tools,
                   context=json.dumps(context, ensure_ascii=False), evidence=list(evidence), operations=[],
                   specialist_task="\n".join(f"{d}: {TASKS[d]}" for d in domains))


def specialist_prompt(req):
    output = (
        '只输出 JSON：{"summary":"本领域结论", "evidence_ids":["实际来源ID"], '
        '"missing":["未解决问题或缺少参数"], "next_steps":["下一步"]}。'
        "四个字段必须齐全；证据仅引用本次输入或工具实际返回的 source_id，禁止猜测来源。"
        if req.specialist_output else "直接面向产品企业用户简洁回答，列出事实、引用、缺少信息和下一步。"
    )
    return (
        f"\n[本次专业任务]\n{req.specialist_task}\n"
        f"负责领域：{', '.join(req.domains)}。只解决以上子任务；原问题其他部分由其他领域处理。"
        "共同历史仅供理解指代，不代表已核验事实或操作授权；不重复回答其他领域。"
        "缺工具或证据就说明限制，不凭常识补造当前状态。\n[输出契约]\n" + output
    )


def parse_findings(content, evidence):
    result = json.loads(content)
    if not isinstance(result, dict) or set(result) != {"summary", "evidence_ids", "missing", "next_steps"}:
        raise ValueError("Invalid specialist output fields")
    if not isinstance(result["summary"], str) or not result["summary"].strip():
        raise ValueError("Missing specialist summary")
    for key in ("evidence_ids", "missing", "next_steps"):
        if not isinstance(result[key], list) or any(not isinstance(v, str) for v in result[key]):
            raise ValueError("Invalid specialist output type")
    known = {item["source_id"] for item in evidence}
    if not set(result["evidence_ids"]) <= known:
        raise ValueError("Unknown specialist evidence source")
    return result


def render_findings(result):
    parts = [result["summary"]]
    parts.extend(f"[{source}]" for source in result["evidence_ids"])
    parts.extend(f"待核实：{item}" for item in result["missing"])
    parts.extend(f"下一步：{item}" for item in result["next_steps"])
    return "\n".join(parts)
