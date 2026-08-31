"""
亮点：面向 SaaS 客户运营与交付的多 Agent 路由与编排

核心问题：多 Agent 情况下如何做 Routing？

路由策略（三层决策）：
  1. 意图路由 —— 根据 IntentCategory 直接映射到专属 Agent
  2. 性能路由 —— 同类 Agent 有多个时，选成功率最高、延迟最低的
  3. 降级路由 —— 专属 Agent 不可用时，自动降级到 TriageAgent

并行协作：
  - 复杂问题（如"集成故障 + SLA 风险 + 续费风险"）可同时派发给多个 Agent
  - 结果由 Orchestrator 合并后返回

升级机制：
  - Agent 置信度低于阈值 → 标记为需要进一步专家判断
"""
import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from anthropic import AsyncAnthropic

from core.intent_recognizer import IntentCategory, IntentRecognizer, UrgencyLevel
from core.llm_utils import extract_text_content

logger = logging.getLogger(__name__)


# ── 数据结构 ──────────────────────────────────────────────────────────────────

class AgentType(Enum):
    TRIAGE    = "triage"     # 分诊与结果汇总
    GENERAL   = "triage"     # 兼容旧代码中的通用 Agent 名称
    DELIVERY  = "delivery"   # 客户交付与实施
    SUPPORT   = "support"    # 技术支持、集成与可靠性
    TECHNICAL = "support"    # 兼容旧代码中的技术 Agent 名称
    SUCCESS   = "success"    # 客户成功与权益分析
    BILLING   = "success"    # 兼容旧代码中的账单 Agent 名称
    RENEWAL   = "renewal"    # 续费运营分析
    ESCALATION = "escalation" # 风险升级建议（不执行外部操作）


@dataclass
class AgentStats:
    """Agent 运行时统计，供 Monitor 和路由决策使用。"""
    total:     int   = 0
    success:   int   = 0
    total_ms:  float = 0.0
    monitor_penalty: float = 0.0

    @property
    def success_rate(self) -> float:
        return self.success / self.total if self.total else 1.0

    @property
    def avg_ms(self) -> float:
        return self.total_ms / self.total if self.total else 0.0

    def routing_score(self) -> float:
        """路由评分：成功率高、延迟低的 Agent 得分高。"""
        latency_score = 1.0 / (1.0 + self.avg_ms / 1000)
        base_score = self.success_rate * 0.7 + latency_score * 0.3
        return base_score * max(0.0, 1.0 - self.monitor_penalty)


@dataclass
class AgentResponse:
    agent_type:  AgentType
    content:     str
    success:     bool
    confidence:  float = 1.0
    latency_ms:  float = 0.0
    escalate:    bool  = False   # 是否需要升级


@dataclass
class Request:
    message:     str
    user_id:     str
    conv_id:     str
    context:     str = ""        # 来自 MemoryManager 的格式化上下文
    history:     Optional[List[Dict[str, str]]] = None  # 对话历史，传给意图识别
    entities:    Dict[str, List[str]] = field(default_factory=dict)
    intent:      Optional[IntentCategory] = None
    intent_group: Optional[str] = None
    urgency:     Optional[UrgencyLevel]   = None
    intent_confidence: float = 1.0
    request_id:  str = field(default_factory=lambda: str(uuid.uuid4())[:8])


@dataclass
class OrchestratorResult:
    request_id:  str
    response:    str
    agent_type:  AgentType
    intent:      Optional[IntentCategory]
    escalated:   bool  = False
    latency_ms:  float = 0.0
    agent_types: List[AgentType] = field(default_factory=list)
    primary_agent: Optional[AgentType] = None
    supporting_agents: List[AgentType] = field(default_factory=list)
    routing_reason: str = ""
    routing_confidence: float = 0.0


@dataclass
class RoutingDecision:
    """一次请求的结构化路由决策。"""
    primary_agent: AgentType
    supporting_agents: List[AgentType] = field(default_factory=list)
    reason: str = ""
    confidence: float = 0.0

    @property
    def agent_types(self) -> List[AgentType]:
        return [self.primary_agent] + self.supporting_agents

    @property
    def multi_agent(self) -> bool:
        return bool(self.supporting_agents)


# ── 基础 Agent ────────────────────────────────────────────────────────────────

class BaseAgent:
    """所有 Agent 的基类，封装 LLM 调用和统计。"""

    agent_type: AgentType
    system_prompt: str

    def __init__(self, client: AsyncAnthropic, model: str, skill_manager: Optional[Any] = None):
        self._client = client
        self._model  = model
        self._skill_manager = skill_manager
        self.stats   = AgentStats()

    async def handle(self, req: Request) -> AgentResponse:
        t0 = time.monotonic()
        self.stats.total += 1
        try:
            content = await self._call_llm(req)
            ms = (time.monotonic() - t0) * 1000
            self.stats.success += 1
            self.stats.total_ms += ms
            escalate = self._needs_escalation(content)
            return AgentResponse(
                agent_type=self.agent_type,
                content=content,
                success=True,
                latency_ms=ms,
                escalate=escalate,
            )
        except Exception as ex:
            ms = (time.monotonic() - t0) * 1000
            self.stats.total_ms += ms
            logger.error(f"{self.agent_type.value} 处理失败: {ex}")
            return AgentResponse(
                agent_type=self.agent_type,
                content="抱歉，处理您的请求时出现问题，请稍后重试。",
                success=False,
                latency_ms=ms,
            )

    async def _call_llm(self, req: Request) -> str:
        def _clean(s: str) -> str:
            return s.encode("utf-8", errors="ignore").decode("utf-8")

        messages = []
        if req.context:
            messages.append({"role": "user", "content": f"[背景信息]\n{_clean(req.context)}"})
            messages.append({"role": "assistant", "content": "好的，我已了解背景信息。"})
        if req.entities:
            entities_text = json.dumps(req.entities, ensure_ascii=False)
            messages.append({"role": "user", "content": f"[结构化实体]\n{_clean(entities_text)}"})
            messages.append({"role": "assistant", "content": "好的，我会结合这些结构化实体处理。"})
        messages.append({"role": "user", "content": _clean(req.message)})

        resp = await self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=self._build_system_prompt(req),
            messages=messages,
        )
        return extract_text_content(resp.content)

    def _build_system_prompt(self, req: Request) -> str:
        """把动态加载的 Skills 拼入 system prompt，让业务规则随请求生效。"""
        if self._skill_manager is None:
            return self.system_prompt
        skill_prompt = self._skill_manager.prompt_for(req.message, self.agent_type.value)
        if not skill_prompt:
            return self.system_prompt
        return f"{self.system_prompt}\n\n[动态 Skills]\n{skill_prompt}"

    def _needs_escalation(self, content: str) -> bool:
        """检测 Agent 是否建议升级（简单关键词检测）。"""
        keywords = ["建议升级", "升级处理", "重大风险", "escalate", "specialist", "无法判断"]
        return any(kw in content for kw in keywords)


class TriageAgent(BaseAgent):
    agent_type    = AgentType.TRIAGE
    system_prompt = (
        "你是 EchoMind 的客户运营与交付分诊 Agent。"
        "面向 SaaS 企业内部员工，识别客户项目、服务问题和客户经营问题，"
        "用证据组织清晰回答；复杂问题要指出涉及的分析领域，但不要声称执行了外部操作。"
    )


class DeliveryAgent(BaseAgent):
    agent_type    = AgentType.DELIVERY
    system_prompt = (
        "你是 SaaS 客户交付与实施分析 Agent。专注于 FlowForge Cloud 的上线计划、"
        "环境配置、数据迁移、验收、培训和交付风险。输出可执行的检查清单与下一步建议，"
        "不要编造项目状态，也不要声称已经修改配置。"
    )


class SupportAgent(BaseAgent):
    agent_type    = AgentType.SUPPORT
    system_prompt = (
        "你是 SaaS 技术支持与可靠性分析 Agent。专注于 API、Webhook、SSO、SDK、"
        "数据同步、错误码、故障影响、可用性和 SLA 风险。先区分事实与假设，"
        "给出可验证的排查路径，不要声称已修复生产系统。"
    )


class SuccessAgent(BaseAgent):
    agent_type    = AgentType.SUCCESS
    system_prompt = (
        "你是 SaaS 客户成功分析 Agent。专注于客户健康度、功能采用、使用阻力、"
        "套餐权益、配额和价值实现。结合知识库证据给出客户跟进建议，避免编造客户数据。"
    )


class RenewalAgent(BaseAgent):
    agent_type    = AgentType.RENEWAL
    system_prompt = (
        "你是 SaaS 续费运营分析 Agent。专注于续费准备度、使用趋势、价值证明、"
        "风险信号和续费跟进建议。输出风险依据、优先级和建议动作，不直接承诺续费结果。"
    )


# Backwards-compatible class names for integrations importing the old modules.
GeneralAgent = TriageAgent
TechnicalAgent = SupportAgent
BillingAgent = SuccessAgent


# ── 编排器 ────────────────────────────────────────────────────────────────────

class AgentOrchestrator:
    """
    多 Agent 编排器。

    路由逻辑（三层）：
      1. 意图 → Agent 类型映射
      2. 同类多实例时按 routing_score() 选最优
      3. 专属 Agent 失败时降级到 TriageAgent
    """

    # 意图 → Agent 类型的静态映射（路由表）
    _INTENT_ROUTING: Dict[IntentCategory, AgentType] = {
        IntentCategory.IMPLEMENTATION_PLAN: AgentType.DELIVERY,
        IntentCategory.ENVIRONMENT_SETUP: AgentType.DELIVERY,
        IntentCategory.DATA_MIGRATION: AgentType.DELIVERY,
        IntentCategory.LAUNCH_VALIDATION: AgentType.DELIVERY,
        IntentCategory.INTEGRATION_API: AgentType.SUPPORT,
        IntentCategory.INTEGRATION_WEBHOOK: AgentType.SUPPORT,
        IntentCategory.INTEGRATION_SSO: AgentType.SUPPORT,
        IntentCategory.INTEGRATION_SYNC: AgentType.SUPPORT,
        IntentCategory.RELIABILITY_INCIDENT: AgentType.SUPPORT,
        IntentCategory.RELIABILITY_PERFORMANCE: AgentType.SUPPORT,
        IntentCategory.RELIABILITY_SLA: AgentType.SUPPORT,
        IntentCategory.SUCCESS_ENTITLEMENT: AgentType.SUCCESS,
        IntentCategory.SUCCESS_QUOTA: AgentType.SUCCESS,
        IntentCategory.SUCCESS_ADOPTION: AgentType.SUCCESS,
        IntentCategory.SUCCESS_HEALTH: AgentType.SUCCESS,
        IntentCategory.RENEWAL_RISK: AgentType.RENEWAL,
        IntentCategory.RENEWAL_READINESS: AgentType.RENEWAL,
        IntentCategory.ESCALATION_RISK: AgentType.ESCALATION,
        IntentCategory.CROSS_DOMAIN_ANALYSIS: AgentType.TRIAGE,
        IntentCategory.IMPLEMENTATION: AgentType.DELIVERY,
        IntentCategory.INTEGRATION: AgentType.SUPPORT,
        IntentCategory.RELIABILITY: AgentType.SUPPORT,
        IntentCategory.ENTITLEMENT: AgentType.SUCCESS,
        IntentCategory.ADOPTION: AgentType.SUCCESS,
        IntentCategory.TECHNICAL:  AgentType.SUPPORT,
        IntentCategory.TECHNICAL_LOGIN: AgentType.SUPPORT,
        IntentCategory.TECHNICAL_CRASH: AgentType.SUPPORT,
        IntentCategory.BILLING:    AgentType.SUCCESS,
        IntentCategory.REFUND:     AgentType.SUCCESS,
        IntentCategory.INVOICE:    AgentType.SUCCESS,
        IntentCategory.PAYMENT_ISSUE: AgentType.SUCCESS,
        IntentCategory.ACCOUNT:    AgentType.SUCCESS,
        IntentCategory.ACCOUNT_SECURITY: AgentType.SUCCESS,
        IntentCategory.ESCALATION: AgentType.ESCALATION,
        IntentCategory.HUMAN_HANDOFF: AgentType.ESCALATION,
        # 其余意图 → GENERAL（默认）
    }

    def __init__(
        self,
        api_key:  str,
        base_url: Optional[str] = None,
        model:    str = "claude-3-5-sonnet-20241022",
        skill_manager: Optional[Any] = None,
    ):
        kwargs: Dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        client = AsyncAnthropic(**kwargs)

        self._intent_recognizer = IntentRecognizer(api_key=api_key, base_url=base_url, model=model)
        self._skill_manager = skill_manager

        # Agent 池：每种类型可有多个实例（水平扩展）
        self._pool: Dict[AgentType, List[BaseAgent]] = {
            AgentType.TRIAGE:   [TriageAgent(client, model, skill_manager)],
            AgentType.DELIVERY: [DeliveryAgent(client, model, skill_manager)],
            AgentType.SUPPORT:  [SupportAgent(client, model, skill_manager)],
            AgentType.SUCCESS:  [SuccessAgent(client, model, skill_manager)],
            AgentType.RENEWAL:  [RenewalAgent(client, model, skill_manager)],
        }

    def set_skill_manager(self, skill_manager: Optional[Any]) -> None:
        """更新 SkillManager 引用，供运行时重载或测试替换使用。"""
        self._skill_manager = skill_manager
        for agents in self._pool.values():
            for agent in agents:
                agent._skill_manager = skill_manager

    async def recognize_intent(
        self,
        message: str,
        history: Optional[List[Dict[str, str]]] = None,
    ):
        """对外暴露意图识别，供 API 层先判断是否需要 RAG 等前置能力。"""
        return await self._intent_recognizer.recognize(message, history=history)

    # ── 主入口 ────────────────────────────────────────────────────────────────

    async def run(self, req: Request) -> OrchestratorResult:
        """
        处理一次请求的完整流程：
          意图识别 → 路由选 Agent → 执行 → 检查升级 → 返回结果
        """
        t0 = time.monotonic()

        # 1. 意图识别（如果调用方已识别则跳过）
        if req.intent is None:
            intent_result = await self._intent_recognizer.recognize(req.message, history=req.history)
            req.intent  = intent_result.intent
            req.intent_group = intent_result.intent_group
            req.urgency = intent_result.urgency
            req.intent_confidence = intent_result.confidence

        if self._needs_clarification(req):
            return OrchestratorResult(
                request_id=req.request_id,
                response="我还不能确定您要分析的是交付实施、技术集成、服务可靠性、客户成功，还是续费运营问题。请补充客户项目、服务问题或客户经营背景。",
                agent_type=AgentType.TRIAGE,
                intent=req.intent,
                escalated=False,
                latency_ms=(time.monotonic() - t0) * 1000,
                agent_types=[AgentType.TRIAGE],
                primary_agent=AgentType.TRIAGE,
                routing_reason="低置信度 OTHER 意图，先澄清用户需求",
                routing_confidence=req.intent_confidence,
            )

        # 复杂问题自动并行协作，例如同一句同时涉及集成故障、SLA 和续费风险。
        decision = self._route_decision(req)
        if decision.multi_agent:
            return await self.run_parallel(req, decision)

        # 2. 执行主 Agent（含降级）
        response = await self._execute(req, decision.primary_agent)

        # 4. 升级检查
        escalated = False
        if response.escalate or req.urgency == UrgencyLevel.CRITICAL or req.intent in (
            IntentCategory.ESCALATION,
            IntentCategory.HUMAN_HANDOFF,
        ):
            escalated = True
            logger.warning(f"请求 {req.request_id} 触发升级: urgency={req.urgency}")
            # 这里只标记风险；外部通知或生产变更不属于分析 Agent 的职责。

        return OrchestratorResult(
            request_id=req.request_id,
            response=response.content,
            agent_type=response.agent_type,
            intent=req.intent,
            escalated=escalated,
            latency_ms=(time.monotonic() - t0) * 1000,
            agent_types=[response.agent_type],
            primary_agent=decision.primary_agent,
            supporting_agents=[],
            routing_reason=decision.reason,
            routing_confidence=decision.confidence,
        )

    async def run_parallel(self, req: Request, decision: RoutingDecision) -> OrchestratorResult:
        """
        并行派发给多个 Agent，合并结果。
        适用于复杂问题（如同时涉及技术和账单）。
        """
        t0 = time.monotonic()
        agent_types = decision.agent_types
        tasks = [self._execute(req, at) for at in agent_types]
        responses = await asyncio.gather(*tasks, return_exceptions=True)

        # 合并：主 Agent 在前，辅助 Agent 在后。
        parts = []
        for r in responses:
            if isinstance(r, AgentResponse) and r.success:
                role = "主处理" if r.agent_type == decision.primary_agent else "辅助处理"
                parts.append(f"[{r.agent_type.value} - {role}]\n{r.content}")

        combined = "\n\n".join(parts) if parts else "抱歉，所有 Agent 均处理失败。"
        escalated = any(isinstance(r, AgentResponse) and r.escalate for r in responses)

        return OrchestratorResult(
            request_id=req.request_id,
            response=combined,
            agent_type=decision.primary_agent,
            intent=req.intent,
            escalated=escalated,
            latency_ms=(time.monotonic() - t0) * 1000,
            agent_types=[
                r.agent_type for r in responses
                if isinstance(r, AgentResponse) and r.success
            ] or agent_types,
            primary_agent=decision.primary_agent,
            supporting_agents=decision.supporting_agents,
            routing_reason=decision.reason,
            routing_confidence=decision.confidence,
        )

    # ── 路由逻辑 ──────────────────────────────────────────────────────────────

    def _route(self, intent: Optional[IntentCategory], urgency: Optional[UrgencyLevel]) -> AgentType:
        """
        三层路由决策：
          1. 意图映射
          2. 紧急度覆盖（CRITICAL 直接升级）
          3. 默认 GENERAL
        """
        if urgency == UrgencyLevel.CRITICAL:
            return AgentType.ESCALATION

        if intent and intent in self._INTENT_ROUTING:
            target = self._INTENT_ROUTING[intent]
            # 如果目标类型有可用实例则使用，否则降级
            if target in self._pool and self._pool[target]:
                return target

        return AgentType.TRIAGE

    def _route_decision(self, req: Request) -> RoutingDecision:
        """
        结构化路由决策。

        先处理紧急/升级风险，再用领域分数决定主 Agent 和辅助 Agent。
        这样可以表达“主处理 + 辅助诊断”，避免关键词命中后无主次地拼接。
        """
        if req.urgency == UrgencyLevel.CRITICAL:
            return RoutingDecision(
                primary_agent=AgentType.ESCALATION,
                reason="紧急度为 CRITICAL，触发升级路由",
                confidence=1.0,
            )

        if req.intent in (IntentCategory.ESCALATION, IntentCategory.HUMAN_HANDOFF):
            return RoutingDecision(
                primary_agent=AgentType.ESCALATION,
                reason=f"意图为 {req.intent.value if req.intent else 'unknown'}，触发升级路由",
                confidence=max(req.intent_confidence, 0.8),
            )

        scores = self._domain_scores(req)
        available_scores = {
            agent_type: score
            for agent_type, score in scores.items()
            if agent_type == AgentType.TRIAGE or self._pool.get(agent_type)
        }
        if not available_scores:
            return RoutingDecision(
                primary_agent=AgentType.TRIAGE,
                reason="无可用专属 Agent，降级到 TriageAgent",
                confidence=0.1,
            )

        ordered = sorted(available_scores.items(), key=lambda item: item[1], reverse=True)
        primary_agent, primary_score = ordered[0]
        supporting_agents = [
            agent_type
            for agent_type, score in ordered[1:]
            if agent_type != AgentType.TRIAGE and score >= 0.45 and score >= primary_score * 0.55
        ]
        # Explicit cross-domain signals can justify collaboration even when a
        # single keyword gives a specialist a low numeric score.
        for agent_type in self._collaboration_targets(req):
            if agent_type != primary_agent and agent_type in available_scores and agent_type not in supporting_agents:
                supporting_agents.append(agent_type)

        reason = self._routing_reason(req, available_scores, primary_agent, supporting_agents)
        return RoutingDecision(
            primary_agent=primary_agent,
            supporting_agents=supporting_agents,
            reason=reason,
            confidence=round(min(primary_score, 1.0), 3),
        )

    def _domain_scores(self, req: Request) -> Dict[AgentType, float]:
        """按意图、关键词和实体为各领域 Agent 打分。"""
        msg = req.message.lower()
        scores = {
            AgentType.TRIAGE: 0.1,
            AgentType.DELIVERY: 0.0,
            AgentType.SUPPORT: 0.0,
            AgentType.SUCCESS: 0.0,
            AgentType.RENEWAL: 0.0,
        }

        if req.intent in (
            IntentCategory.QUERY,
            IntentCategory.REQUEST,
            IntentCategory.COMPLAINT,
            IntentCategory.GREETING,
            IntentCategory.FEEDBACK,
            IntentCategory.OTHER,
            IntentCategory.CROSS_DOMAIN_ANALYSIS,
            IntentCategory.ESCALATION_RISK,
        ):
            scores[AgentType.TRIAGE] += 0.55

        if req.intent in (
            IntentCategory.IMPLEMENTATION,
            IntentCategory.LOGISTICS,
            IntentCategory.IMPLEMENTATION_PLAN,
            IntentCategory.ENVIRONMENT_SETUP,
            IntentCategory.DATA_MIGRATION,
            IntentCategory.LAUNCH_VALIDATION,
        ):
            scores[AgentType.DELIVERY] += 0.75

        if req.intent in (
            IntentCategory.INTEGRATION,
            IntentCategory.RELIABILITY,
            IntentCategory.TECHNICAL,
            IntentCategory.TECHNICAL_LOGIN,
            IntentCategory.TECHNICAL_CRASH,
            IntentCategory.INTEGRATION_API,
            IntentCategory.INTEGRATION_WEBHOOK,
            IntentCategory.INTEGRATION_SSO,
            IntentCategory.INTEGRATION_SYNC,
            IntentCategory.RELIABILITY_INCIDENT,
            IntentCategory.RELIABILITY_PERFORMANCE,
            IntentCategory.RELIABILITY_SLA,
        ):
            scores[AgentType.SUPPORT] += 0.75

        if req.intent in (
            IntentCategory.ENTITLEMENT,
            IntentCategory.ADOPTION,
            IntentCategory.BILLING,
            IntentCategory.ACCOUNT,
            IntentCategory.ACCOUNT_SECURITY,
            IntentCategory.REFUND,
            IntentCategory.INVOICE,
            IntentCategory.PAYMENT_ISSUE,
            IntentCategory.SUCCESS_ENTITLEMENT,
            IntentCategory.SUCCESS_QUOTA,
            IntentCategory.SUCCESS_ADOPTION,
            IntentCategory.SUCCESS_HEALTH,
        ):
            scores[AgentType.SUCCESS] += 0.75

        if req.intent in (IntentCategory.RENEWAL_RISK, IntentCategory.RENEWAL_READINESS):
            scores[AgentType.RENEWAL] += 0.75

        delivery_kws = ["上线", "实施", "迁移", "验收", "培训", "配置", "rollout", "migration"]
        support_kws = ["api", "webhook", "sso", "sdk", "同步", "故障", "报错", "error", "500", "401", "超时", "sla"]
        success_kws = ["客户健康", "活跃", "使用量", "采用", "套餐", "配额", "权益", "价值", "推广"]
        renewal_kws = ["续费", "续约", "到期", "流失", "renewal", "renew"]

        delivery_hits = sum(1 for kw in delivery_kws if kw in msg)
        support_hits = sum(1 for kw in support_kws if kw in msg)
        success_hits = sum(1 for kw in success_kws if kw in msg)
        renewal_hits = sum(1 for kw in renewal_kws if kw in msg)

        scores[AgentType.DELIVERY] += min(0.45, delivery_hits * 0.18)
        scores[AgentType.SUPPORT] += min(0.45, support_hits * 0.18)
        scores[AgentType.SUCCESS] += min(0.45, success_hits * 0.18)
        scores[AgentType.RENEWAL] += min(0.55, renewal_hits * 0.22)

        entities = req.entities or {}
        if entities.get("error_code"):
            scores[AgentType.SUPPORT] += 0.2
        if entities.get("integration"):
            scores[AgentType.SUPPORT] += 0.15
        if entities.get("project_id"):
            scores[AgentType.DELIVERY] += 0.1
        if entities.get("usage_metric"):
            scores[AgentType.SUCCESS] += 0.15

        return {agent_type: round(score, 3) for agent_type, score in scores.items()}

    @staticmethod
    def _routing_reason(
        req: Request,
        scores: Dict[AgentType, float],
        primary_agent: AgentType,
        supporting_agents: List[AgentType],
    ) -> str:
        score_text = ", ".join(
            f"{agent_type.value}={score:.2f}"
            for agent_type, score in sorted(scores.items(), key=lambda item: item[1], reverse=True)
        )
        support_text = ", ".join(agent.value for agent in supporting_agents) or "none"
        intent = req.intent.value if req.intent else "unknown"
        return (
            f"intent={intent}, group={req.intent_group or 'unknown'}, "
            f"primary={primary_agent.value}, supporting={support_text}, scores=[{score_text}]"
        )

    def _collaboration_targets(self, req: Request) -> List[AgentType]:
        """
        判断是否需要多个 Agent 并行协作。

        意图识别通常只返回一个主意图；这里用领域关键词补充检测复合问题，
        例如"登录报错且被重复扣款"需要技术和账单 Agent 同时处理。
        """
        msg = req.message.lower()
        targets: List[AgentType] = []

        delivery_kws = ["上线", "实施", "迁移", "验收", "培训", "rollout", "migration"]
        support_kws = ["api", "webhook", "sso", "sdk", "同步", "故障", "报错", "error", "500", "401", "超时", "sla"]
        success_kws = ["客户健康", "活跃", "使用量", "采用", "套餐", "配额", "权益", "价值", "推广"]
        renewal_kws = ["续费", "续约", "到期", "流失", "renewal", "renew"]

        if req.intent in (
            IntentCategory.IMPLEMENTATION, IntentCategory.LOGISTICS,
            IntentCategory.IMPLEMENTATION_PLAN, IntentCategory.ENVIRONMENT_SETUP,
            IntentCategory.DATA_MIGRATION, IntentCategory.LAUNCH_VALIDATION,
        ) or any(kw in msg for kw in delivery_kws):
            targets.append(AgentType.DELIVERY)
        if req.intent in (
            IntentCategory.INTEGRATION,
            IntentCategory.RELIABILITY,
            IntentCategory.TECHNICAL,
            IntentCategory.TECHNICAL_LOGIN,
            IntentCategory.TECHNICAL_CRASH,
            IntentCategory.INTEGRATION_API,
            IntentCategory.INTEGRATION_WEBHOOK,
            IntentCategory.INTEGRATION_SSO,
            IntentCategory.INTEGRATION_SYNC,
            IntentCategory.RELIABILITY_INCIDENT,
            IntentCategory.RELIABILITY_PERFORMANCE,
            IntentCategory.RELIABILITY_SLA,
        ) or any(kw in msg for kw in support_kws):
            targets.append(AgentType.SUPPORT)
        if req.intent in (
            IntentCategory.ENTITLEMENT,
            IntentCategory.ADOPTION,
            IntentCategory.BILLING,
            IntentCategory.ACCOUNT,
            IntentCategory.ACCOUNT_SECURITY,
            IntentCategory.REFUND,
            IntentCategory.INVOICE,
            IntentCategory.PAYMENT_ISSUE,
            IntentCategory.SUCCESS_ENTITLEMENT,
            IntentCategory.SUCCESS_QUOTA,
            IntentCategory.SUCCESS_ADOPTION,
            IntentCategory.SUCCESS_HEALTH,
        ) or any(kw in msg for kw in success_kws):
            targets.append(AgentType.SUCCESS)
        if req.intent in (IntentCategory.RENEWAL_RISK, IntentCategory.RENEWAL_READINESS) or any(kw in msg for kw in renewal_kws):
            targets.append(AgentType.RENEWAL)

        # 保持顺序去重，并只返回当前有实例的 Agent 类型。
        deduped = list(dict.fromkeys(targets))
        return [agent_type for agent_type in deduped if self._pool.get(agent_type)]

    @staticmethod
    def _needs_clarification(req: Request) -> bool:
        """低置信度且无明确意图时，先追问，避免误路由。"""
        if req.intent != IntentCategory.OTHER:
            return False
        text = (req.message or "").strip()
        if len(text) <= 2:
            return False
        return req.intent_confidence < 0.5

    def _best_agent(self, agent_type: AgentType) -> Optional[BaseAgent]:
        """
        性能路由：从同类 Agent 中选 routing_score() 最高的。
        这是"基于在线表现动态调整路由"的核心。
        """
        agents = self._pool.get(agent_type, [])
        if not agents:
            return None
        return max(agents, key=lambda a: a.stats.routing_score())

    async def _execute(self, req: Request, agent_type: AgentType) -> AgentResponse:
        """执行 Agent，失败时降级到 TriageAgent。"""
        agent = self._best_agent(agent_type)
        if agent is None:
            agent = self._best_agent(AgentType.TRIAGE)
        if agent is None:
            return AgentResponse(
                agent_type=AgentType.TRIAGE,
                content="服务暂时不可用，请稍后重试。",
                success=False,
            )

        response = await agent.handle(req)

        # 专属 Agent 失败时降级到 TriageAgent
        if not response.success and agent_type != AgentType.TRIAGE:
            logger.warning(f"{agent_type.value} 失败，降级到 TriageAgent")
            fallback = self._best_agent(AgentType.TRIAGE)
            if fallback:
                response = await fallback.handle(req)

        return response

    # ── 统计（供 Monitor 读取）────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        result = {}
        for agent_type, agents in self._pool.items():
            for i, agent in enumerate(agents):
                key = f"{agent_type.value}_{i}"
                result[key] = {
                    "total":        agent.stats.total,
                    "success_rate": round(agent.stats.success_rate, 3),
                    "avg_ms":       round(agent.stats.avg_ms, 1),
                    "monitor_penalty": round(agent.stats.monitor_penalty, 3),
                    "routing_score": round(agent.stats.routing_score(), 3),
                }
        return result

    def update_routing_penalties(self, penalties: Dict[str, float]) -> None:
        """
        接收 Monitor 的在线表现反馈，动态调整路由惩罚项。

        penalties 的 key 使用 get_stats() 中的 agent key，例如 technical_0。
        """
        for agent_type, agents in self._pool.items():
            for i, agent in enumerate(agents):
                key = f"{agent_type.value}_{i}"
                penalty = penalties.get(key, 0.0)
                agent.stats.monitor_penalty = min(max(penalty, 0.0), 0.9)
