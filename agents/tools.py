"""SaaS 运营分析 Agent 的确定性工具与工具白名单。

工具只分析当前请求、知识库证据和用户明确提供的数据，不执行生产变更、
客户触达、工单创建或合同操作。真实外部系统接入应在权限和审计边界内另行实现。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, TYPE_CHECKING, Union

if TYPE_CHECKING:
    from agents.agent_orchestrator import Request


AgentToolHandler = Callable[["Request", Dict[str, Any]], Union[Any, Awaitable[Any]]]


@dataclass(frozen=True)
class AgentToolSpec:
    name: str
    description: str
    input_schema: Dict[str, Any]
    handler: AgentToolHandler


def make_tool(
    name: str,
    description: str,
    properties: Dict[str, Any],
    handler: AgentToolHandler,
    required: Optional[List[str]] = None,
) -> AgentToolSpec:
    return AgentToolSpec(
        name=name,
        description=description,
        input_schema={
            "type": "object",
            "properties": properties,
            "required": required or [],
            "additionalProperties": False,
        },
        handler=handler,
    )


def inspect_request_context(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
    """返回脱敏后的当前请求快照，不查询外部业务系统。"""
    return {
        "request_id": req.request_id,
        "intent": req.intent.value if req.intent else None,
        "intent_group": req.intent_group,
        "urgency": req.urgency.name if req.urgency else None,
        "intent_confidence": round(req.intent_confidence, 4),
        "entities": req.entities or {},
        "context_available": bool(req.context),
        "focus": str(args.get("focus", "cross_domain"))[:60],
    }


def suggest_required_fields(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
    """按 SaaS 分析领域给出下一轮最少需要补充的字段。"""
    domain = str(args.get("domain") or req.intent_group or "triage").lower()
    fields_by_domain = {
        "delivery": ["客户项目或项目编号", "目标环境", "计划上线时间", "当前阶段"],
        "implementation": ["客户项目或项目编号", "目标环境", "计划上线时间", "当前阶段"],
        "support": ["发生时间", "环境", "错误码或 request_id", "影响范围", "最近变更"],
        "integration": ["集成类型", "环境", "错误码或 request_id", "影响范围"],
        "reliability": ["发生时间", "影响范围", "持续时长", "已验证现象"],
        "success": ["客户或项目标识", "观察周期", "使用/采用指标", "目标功能或价值目标"],
        "adoption": ["客户或项目标识", "观察周期", "使用/采用指标", "目标功能或价值目标"],
        "renewal": ["续费日期", "使用趋势", "价值证明", "未闭环问题", "关键干系人反馈"],
    }
    requested = fields_by_domain.get(domain, ["客户项目背景", "希望分析的问题", "已知事实或数据"])
    return {
        "domain": domain,
        "required_fields": requested,
        "known_entities": req.entities or {},
        "external_lookup_performed": False,
    }


def build_delivery_checklist(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
    stage = str(args.get("stage", "planning"))[:40]
    target_environment = str(args.get("target_environment", "unknown"))[:80]
    return {
        "stage": stage,
        "target_environment": target_environment,
        "checklist": [
            "确认范围、负责人、里程碑和验收指标",
            "确认目标环境、权限、网络和依赖服务",
            "完成数据迁移方案、回滚方案和验证样本",
            "完成集成联调、容量与安全检查",
            "完成用户培训、上线窗口和上线后观察计划",
        ],
        "production_change_executed": False,
    }


def assess_launch_readiness(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
    signals = {
        "acceptance_defined": bool(args.get("acceptance_defined", False)),
        "rollback_ready": bool(args.get("rollback_ready", False)),
        "owners_confirmed": bool(args.get("owners_confirmed", False)),
        "monitoring_ready": bool(args.get("monitoring_ready", False)),
    }
    missing = [name for name, ready in signals.items() if not ready]
    return {
        "signals": signals,
        "missing_readiness_signals": missing,
        "readiness": "ready_for_review" if not missing else "gaps_present",
        "final_approval_granted": False,
    }


def lookup_error_code(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
    code = str(args.get("error_code", "")).upper().strip()
    mapping = {
        "400": ("请求格式或参数不符合接口契约", ["核对 API 版本和字段类型", "检查必填字段与 Content-Type"]),
        "401": ("认证失败", ["确认 Token/API Key 的环境与有效期", "核对时间戳、签名和请求头"]),
        "403": ("权限或策略拒绝", ["核对服务账号权限", "检查 IP 白名单、租户和资源范围"]),
        "404": ("路径或资源不存在", ["核对环境、接口路径和资源标识", "确认资源是否已创建"]),
        "409": ("资源状态冲突或幂等冲突", ["检查幂等键与资源当前状态", "确认是否存在并发写入"]),
        "429": ("请求频率或配额受限", ["检查配额与速率限制", "采用退避重试并避免重试风暴"]),
        "500": ("服务端处理异常", ["记录 request_id、时间和环境", "检查依赖、参数与服务端日志"]),
    }
    meaning, steps = mapping.get(code, ("未识别的错误码", ["补充完整错误、request_id、时间、环境和复现步骤"]))
    return {
        "error_code": code,
        "meaning": meaning,
        "next_steps": steps,
        "server_log_checked": False,
    }


def build_diagnostic_plan(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
    integration_type = str(args.get("integration_type", "service"))[:80]
    environment = str(args.get("environment", "unknown"))[:80]
    reproduced = bool(args.get("reproduced", False))
    steps = [
        "明确已知事实、发生时间、影响范围和最近变更",
        "用最小请求确认 DNS、网络、证书、认证和权限",
        "核对接口版本、字段契约、幂等与重试策略",
        "记录 request_id、响应码、延迟和可脱敏日志",
    ]
    if reproduced:
        steps.append("在非生产环境复现并逐项验证根因假设")
    return {
        "integration_type": integration_type,
        "environment": environment,
        "reproduced": reproduced,
        "diagnostic_steps": steps,
        "configuration_changed": False,
    }


def assess_adoption_signals(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
    trend = str(args.get("usage_trend", "unknown"))[:40]
    blockers = [str(item)[:120] for item in args.get("blockers", [])][:10]
    return {
        "usage_trend": trend,
        "blockers": blockers,
        "analysis_dimensions": ["活跃范围", "核心功能采用", "使用深度", "关键角色覆盖", "价值目标进展"],
        "recommended_evidence": ["按周/月使用趋势", "目标功能采用率", "关键用户反馈", "未解决服务问题"],
        "customer_health_conclusion": "insufficient_evidence",
    }


def check_entitlement_context(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "plan": str(args.get("plan", "unknown"))[:80],
        "requested_capability": str(args.get("capability", "unknown"))[:120],
        "verification_fields": ["合同/套餐名称", "租户或客户标识", "功能名称", "当前配额与使用量"],
        "entitlement_verified": False,
        "reason": "当前工具不连接合同、计费或权限系统，只整理核验上下文",
    }


def assess_renewal_risk(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
    usage_trend = str(args.get("usage_trend", "unknown"))[:40]
    unresolved_issues = max(0, int(args.get("unresolved_issues", 0) or 0))
    value_evidence = bool(args.get("value_evidence", False))
    risk_signals: List[str] = []
    if usage_trend in {"declining", "下降", "down"}:
        risk_signals.append("使用趋势下降")
    if unresolved_issues:
        risk_signals.append(f"存在 {unresolved_issues} 个未闭环问题")
    if not value_evidence:
        risk_signals.append("缺少可复用的价值证明")
    return {
        "risk_signals": risk_signals,
        "risk_level": "high" if len(risk_signals) >= 2 else "needs_review",
        "recommended_actions": ["确认续费时间线和决策人", "闭环高影响问题", "补齐量化价值证明", "制定采用提升计划"],
        "renewal_outcome_predicted": False,
    }


def create_risk_escalation_summary(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "request_id": req.request_id,
        "reason": str(args.get("reason", "需要领域负责人进一步判断"))[:160],
        "intent": req.intent.value if req.intent else "unknown",
        "urgency": req.urgency.name if req.urgency else "UNKNOWN",
        "entities": req.entities or {},
        "recommended_owner": str(args.get("recommended_owner", "domain_owner"))[:80],
        "external_notification_sent": False,
        "production_change_executed": False,
    }


def build_shared_rag_tools(tool_manager: Any) -> Dict[str, AgentToolSpec]:
    async def search_knowledge_base(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
        query = str(args.get("query") or req.message or "").strip()
        top_k = min(10, max(1, int(args.get("top_k", 5) or 5)))
        if not query:
            return {"success": False, "error": "query 不能为空", "results": []}
        if tool_manager is None:
            return {"success": False, "error": "RAG 工具未初始化", "results": []}
        result = await tool_manager.search_with_rewrite("knowledge_search", query, top_k=top_k)
        if not getattr(result, "success", False):
            return {
                "success": False,
                "query": query,
                "error": getattr(result, "error", "知识库检索失败"),
                "results": [],
                "cached": bool(getattr(result, "cached", False)),
                "reranked": False,
            }
        return {
            "success": True,
            "query": query,
            "top_k": top_k,
            "results": result.data,
            "cached": bool(getattr(result, "cached", False)),
            "reranked": bool(getattr(result, "reranked", False)),
        }

    return {
        "search_knowledge_base": make_tool(
            "search_knowledge_base",
            "检索 EchoMind 内部 SaaS 交付、集成、可靠性、客户成功和续费知识库。",
            {
                "query": {"type": "string", "description": "业务问题或检索关键词"},
                "top_k": {"type": "integer", "description": "返回结果条数，1-10"},
            },
            search_knowledge_base,
            required=["query"],
        )
    }


def triage_tools() -> Dict[str, AgentToolSpec]:
    return {
        "inspect_request_context": make_tool(
            "inspect_request_context",
            "查看当前请求的意图、紧急度、实体和上下文可用性。",
            {"focus": {"type": "string", "description": "需要关注的分析领域"}},
            inspect_request_context,
        ),
        "suggest_required_fields": make_tool(
            "suggest_required_fields",
            "按交付、支持、成功或续费领域建议最少补充字段。",
            {"domain": {"type": "string", "description": "delivery/support/success/renewal"}},
            suggest_required_fields,
        ),
    }


def delivery_tools() -> Dict[str, AgentToolSpec]:
    return {
        "build_delivery_checklist": make_tool(
            "build_delivery_checklist",
            "生成 SaaS 实施、迁移和上线检查清单，不执行生产变更。",
            {
                "stage": {"type": "string", "description": "planning/configuration/migration/launch"},
                "target_environment": {"type": "string", "description": "目标环境"},
            },
            build_delivery_checklist,
            required=["stage", "target_environment"],
        ),
        "assess_launch_readiness": make_tool(
            "assess_launch_readiness",
            "根据验收、回滚、负责人和监控四项信号检查上线准备度。",
            {
                "acceptance_defined": {"type": "boolean"},
                "rollback_ready": {"type": "boolean"},
                "owners_confirmed": {"type": "boolean"},
                "monitoring_ready": {"type": "boolean"},
            },
            assess_launch_readiness,
            required=["acceptance_defined", "rollback_ready", "owners_confirmed", "monitoring_ready"],
        ),
    }


def support_tools() -> Dict[str, AgentToolSpec]:
    return {
        "lookup_error_code": make_tool(
            "lookup_error_code",
            "解释常见 API/HTTP 错误码和低风险排查方向，不读取服务端日志。",
            {"error_code": {"type": "string", "description": "例如 401、403、429、500"}},
            lookup_error_code,
            required=["error_code"],
        ),
        "build_diagnostic_plan": make_tool(
            "build_diagnostic_plan",
            "为 API、Webhook、SSO、SDK 或同步问题生成可验证的排障顺序。",
            {
                "integration_type": {"type": "string"},
                "environment": {"type": "string"},
                "reproduced": {"type": "boolean"},
            },
            build_diagnostic_plan,
            required=["integration_type", "environment", "reproduced"],
        ),
    }


def success_tools() -> Dict[str, AgentToolSpec]:
    return {
        "assess_adoption_signals": make_tool(
            "assess_adoption_signals",
            "整理采用和客户健康分析维度；没有数据时不生成虚假健康分。",
            {
                "usage_trend": {"type": "string", "description": "growing/stable/declining/unknown"},
                "blockers": {"type": "array", "items": {"type": "string"}},
            },
            assess_adoption_signals,
            required=["usage_trend", "blockers"],
        ),
        "check_entitlement_context": make_tool(
            "check_entitlement_context",
            "整理套餐权益和配额核验字段，不连接合同或权限系统。",
            {
                "plan": {"type": "string"},
                "capability": {"type": "string"},
            },
            check_entitlement_context,
            required=["plan", "capability"],
        ),
    }


def renewal_tools() -> Dict[str, AgentToolSpec]:
    return {
        "assess_renewal_risk": make_tool(
            "assess_renewal_risk",
            "依据明确提供的使用趋势、未闭环问题和价值证明整理续费风险信号。",
            {
                "usage_trend": {"type": "string"},
                "unresolved_issues": {"type": "integer"},
                "value_evidence": {"type": "boolean"},
            },
            assess_renewal_risk,
            required=["usage_trend", "unresolved_issues", "value_evidence"],
        )
    }


def escalation_tools() -> Dict[str, AgentToolSpec]:
    return {
        "create_risk_escalation_summary": make_tool(
            "create_risk_escalation_summary",
            "生成供领域负责人审阅的风险升级摘要，不发送通知、不创建真实工单。",
            {
                "reason": {"type": "string"},
                "recommended_owner": {"type": "string"},
            },
            create_risk_escalation_summary,
            required=["reason"],
        )
    }

