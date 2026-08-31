"""
亮点：端到端意图识别

三路融合策略：
  1. LLM 语义理解（权重 70%）—— 主力，理解复杂语义和上下文
  2. Embedding 向量相似度（权重 20%）—— 快速匹配常见表达
  3. 关键词模式匹配（权重 10%）—— 零延迟兜底

三路结果通过加权投票合并，置信度低于阈值时降级为 OTHER。
LLM 和 Embedding 并行调用，不串行等待。
"""
import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from anthropic import AsyncAnthropic

from core.llm_utils import extract_text_content

logger = logging.getLogger(__name__)


class IntentCategory(Enum):
    QUERY      = "query"       # 查询信息
    COMPLAINT  = "complaint"   # 内部服务问题反馈
    REQUEST    = "request"     # 请求分析或建议
    GREETING   = "greeting"    # 问候
    ESCALATION = "escalation"  # 风险升级建议
    TECHNICAL  = "technical"   # 技术支持（兼容旧值）
    BILLING    = "billing"     # 客户经营（兼容旧值）
    ACCOUNT    = "account"     # 客户资料（兼容旧值）
    FEEDBACK   = "feedback"    # 正向反馈
    IMPLEMENTATION = "implementation"  # 客户交付与实施
    INTEGRATION = "integration"        # API、Webhook、SSO、数据同步
    RELIABILITY = "reliability"        # 故障、影响和稳定性
    ENTITLEMENT = "entitlement"        # 套餐、配额和服务权益
    ADOPTION = "adoption"              # 使用推广、健康度和续费准备
    # 19 business intents, normalized into six intent groups below.
    IMPLEMENTATION_PLAN = "implementation_plan"
    ENVIRONMENT_SETUP = "environment_setup"
    DATA_MIGRATION = "data_migration"
    LAUNCH_VALIDATION = "launch_validation"
    INTEGRATION_API = "integration_api"
    INTEGRATION_WEBHOOK = "integration_webhook"
    INTEGRATION_SSO = "integration_sso"
    INTEGRATION_SYNC = "integration_sync"
    RELIABILITY_INCIDENT = "reliability_incident"
    RELIABILITY_PERFORMANCE = "reliability_performance"
    RELIABILITY_SLA = "reliability_sla"
    SUCCESS_ENTITLEMENT = "success_entitlement"
    SUCCESS_QUOTA = "success_quota"
    SUCCESS_ADOPTION = "success_adoption"
    SUCCESS_HEALTH = "success_health"
    RENEWAL_RISK = "renewal_risk"
    RENEWAL_READINESS = "renewal_readiness"
    ESCALATION_RISK = "escalation_risk"
    CROSS_DOMAIN_ANALYSIS = "cross_domain_analysis"
    # Legacy values remain parseable for clients that send old evaluation data.
    ORDER_STATUS = "order_status"
    LOGISTICS = "logistics"
    REFUND = "refund"
    INVOICE = "invoice"
    PAYMENT_ISSUE = "payment_issue"
    ACCOUNT_SECURITY = "account_security"
    TECHNICAL_LOGIN = "technical_login"
    TECHNICAL_CRASH = "technical_crash"
    HUMAN_HANDOFF = "human_handoff"
    OTHER      = "other"


class UrgencyLevel(Enum):
    LOW      = 1
    MEDIUM   = 2
    HIGH     = 3
    CRITICAL = 4


@dataclass
class IntentResult:
    intent:     IntentCategory
    confidence: float
    urgency:    UrgencyLevel
    intent_group: str
    entities:   Dict[str, List[str]]   # 从消息中提取的实体
    reasoning:  str
    latency_ms: float
    source_scores: Dict[str, float] = field(default_factory=dict)


# ── Few-shot 模板（同时用于 LLM 示例和 Embedding 匹配）────────────────────────
_TEMPLATES: Dict[IntentCategory, List[str]] = {
    IntentCategory.QUERY:      ["FlowForge Cloud 的数据同步策略是什么？", "客户项目当前上线阶段是什么？", "SLA 规则在哪里？"],
    IntentCategory.COMPLAINT:  ["客户反复反馈同一个集成问题", "这个交付问题影响了客户体验"],
    IntentCategory.REQUEST:    ["帮我分析这个客户项目的风险", "请给出下一步处理建议"],
    IntentCategory.GREETING:   ["你好", "嗨，有人吗", "早上好"],
    IntentCategory.ESCALATION: ["这个客户事件是否需要升级？", "SLA 风险是否需要管理层关注？"],
    IntentCategory.TECHNICAL:  ["集成接口出现错误", "客户环境连接失败", "数据同步异常"],
    IntentCategory.BILLING:    ["客户套餐配额如何解释？", "客户的服务权益是什么？"],
    IntentCategory.ACCOUNT:    ["客户项目资料有哪些？", "如何更新客户联系人信息？"],
    IntentCategory.FEEDBACK:   ["客户对上线体验反馈很好", "客户认可这次实施方案"],
    IntentCategory.IMPLEMENTATION: ["新客户如何制定上线计划？", "数据迁移前需要检查什么？"],
    IntentCategory.INTEGRATION: ["Webhook 数据同步失败怎么排查？", "SSO 接入需要哪些配置？"],
    IntentCategory.RELIABILITY: ["这个错误会影响多少客户？", "如何判断事件严重度和 SLA 风险？"],
    IntentCategory.ENTITLEMENT: ["客户套餐包含多少 API 配额？", "这个 SLA 是否覆盖当前客户？"],
    IntentCategory.ADOPTION: ["客户使用量下降应该如何跟进？", "续费前如何判断客户健康度？"],
    IntentCategory.IMPLEMENTATION_PLAN: ["新客户上线计划如何安排？"],
    IntentCategory.ENVIRONMENT_SETUP: ["生产环境上线前需要检查什么？"],
    IntentCategory.DATA_MIGRATION: ["客户数据迁移需要注意什么？"],
    IntentCategory.LAUNCH_VALIDATION: ["上线验收需要哪些指标？"],
    IntentCategory.INTEGRATION_API: ["FlowForge API 接入失败怎么分析？"],
    IntentCategory.INTEGRATION_WEBHOOK: ["Webhook 签名校验失败怎么排查？"],
    IntentCategory.INTEGRATION_SSO: ["SSO 集成需要哪些配置？"],
    IntentCategory.INTEGRATION_SYNC: ["数据同步延迟的原因是什么？"],
    IntentCategory.RELIABILITY_INCIDENT: ["服务故障影响了哪些客户？"],
    IntentCategory.RELIABILITY_PERFORMANCE: ["任务延迟升高如何定位？"],
    IntentCategory.RELIABILITY_SLA: ["这个事件是否有 SLA 风险？"],
    IntentCategory.SUCCESS_ENTITLEMENT: ["客户套餐包含哪些功能？"],
    IntentCategory.SUCCESS_QUOTA: ["客户 API 配额还剩多少？"],
    IntentCategory.SUCCESS_ADOPTION: ["客户核心功能采用率偏低怎么办？"],
    IntentCategory.SUCCESS_HEALTH: ["如何判断客户健康度？"],
    IntentCategory.RENEWAL_RISK: ["客户有哪些续费流失风险？"],
    IntentCategory.RENEWAL_READINESS: ["续费前需要准备哪些价值证明？"],
    IntentCategory.ESCALATION_RISK: ["这个问题是否需要升级？"],
    IntentCategory.CROSS_DOMAIN_ANALYSIS: ["请综合分析上线、故障和续费风险"],
}

_SPECIFIC_INTENTS = {
    IntentCategory.IMPLEMENTATION_PLAN, IntentCategory.ENVIRONMENT_SETUP,
    IntentCategory.DATA_MIGRATION, IntentCategory.LAUNCH_VALIDATION,
    IntentCategory.INTEGRATION_API, IntentCategory.INTEGRATION_WEBHOOK,
    IntentCategory.INTEGRATION_SSO, IntentCategory.INTEGRATION_SYNC,
    IntentCategory.RELIABILITY_INCIDENT, IntentCategory.RELIABILITY_PERFORMANCE,
    IntentCategory.RELIABILITY_SLA, IntentCategory.SUCCESS_ENTITLEMENT,
    IntentCategory.SUCCESS_QUOTA, IntentCategory.SUCCESS_ADOPTION,
    IntentCategory.SUCCESS_HEALTH, IntentCategory.RENEWAL_RISK,
    IntentCategory.RENEWAL_READINESS, IntentCategory.ESCALATION_RISK,
    IntentCategory.CROSS_DOMAIN_ANALYSIS,
}

_GENERIC_INTENTS = {
    IntentCategory.QUERY,
    IntentCategory.TECHNICAL,
    IntentCategory.BILLING,
    IntentCategory.ESCALATION,
}

_INTENT_GROUPS: Dict[IntentCategory, IntentCategory] = {
    IntentCategory.IMPLEMENTATION_PLAN: IntentCategory.IMPLEMENTATION,
    IntentCategory.ENVIRONMENT_SETUP: IntentCategory.IMPLEMENTATION,
    IntentCategory.DATA_MIGRATION: IntentCategory.IMPLEMENTATION,
    IntentCategory.LAUNCH_VALIDATION: IntentCategory.IMPLEMENTATION,
    IntentCategory.INTEGRATION_API: IntentCategory.INTEGRATION,
    IntentCategory.INTEGRATION_WEBHOOK: IntentCategory.INTEGRATION,
    IntentCategory.INTEGRATION_SSO: IntentCategory.INTEGRATION,
    IntentCategory.INTEGRATION_SYNC: IntentCategory.INTEGRATION,
    IntentCategory.RELIABILITY_INCIDENT: IntentCategory.RELIABILITY,
    IntentCategory.RELIABILITY_PERFORMANCE: IntentCategory.RELIABILITY,
    IntentCategory.RELIABILITY_SLA: IntentCategory.RELIABILITY,
    IntentCategory.SUCCESS_ENTITLEMENT: IntentCategory.ENTITLEMENT,
    IntentCategory.SUCCESS_QUOTA: IntentCategory.ENTITLEMENT,
    IntentCategory.SUCCESS_ADOPTION: IntentCategory.ADOPTION,
    IntentCategory.SUCCESS_HEALTH: IntentCategory.ADOPTION,
    IntentCategory.RENEWAL_RISK: IntentCategory.ADOPTION,
    IntentCategory.RENEWAL_READINESS: IntentCategory.ADOPTION,
    IntentCategory.ESCALATION_RISK: IntentCategory.ESCALATION,
    IntentCategory.CROSS_DOMAIN_ANALYSIS: IntentCategory.ESCALATION,
    IntentCategory.TECHNICAL_LOGIN: IntentCategory.RELIABILITY,
    IntentCategory.TECHNICAL_CRASH: IntentCategory.RELIABILITY,
    IntentCategory.ACCOUNT_SECURITY: IntentCategory.ENTITLEMENT,
    IntentCategory.ORDER_STATUS: IntentCategory.QUERY,
    IntentCategory.LOGISTICS: IntentCategory.IMPLEMENTATION,
    IntentCategory.REFUND: IntentCategory.ENTITLEMENT,
    IntentCategory.INVOICE: IntentCategory.ENTITLEMENT,
    IntentCategory.PAYMENT_ISSUE: IntentCategory.ENTITLEMENT,
    IntentCategory.HUMAN_HANDOFF: IntentCategory.ESCALATION,
}

# 紧急关键词
_URGENCY_KEYWORDS = {
    UrgencyLevel.CRITICAL: ["紧急", "emergency", "urgent", "asap", "立刻"],
    UrgencyLevel.HIGH:     ["今天", "马上", "尽快", "hurry", "now"],
    UrgencyLevel.MEDIUM:   ["这周", "soon", "快点"],
}


def _cosine(a: List[float], b: List[float]) -> float:
    """纯 Python 余弦相似度，不依赖 numpy。"""
    dot = sum(x * y for x, y in zip(a, b))
    na  = sum(x * x for x in a) ** 0.5
    nb  = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


class IntentRecognizer:
    """
    端到端意图识别器。

    初始化时不加载任何本地模型，所有 AI 能力通过 Anthropic API 调用。
    模板 Embedding 在首次请求时懒加载并缓存，后续复用。
    """

    def __init__(
        self,
        api_key: str,
        base_url: Optional[str] = None,
        model: str = "claude-3-5-sonnet-20241022",
        confidence_threshold: float = 0.5,
    ):
        kwargs: Dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self.client    = AsyncAnthropic(**kwargs)
        self.model     = model
        self.threshold = confidence_threshold
        # 第三方兼容 API（如 DeepSeek）通常不支持 Embedding，禁用该策略。
        # 官方 Anthropic SDK 当前没有 embeddings 资源，因此下面会使用稳定的
        # 本地字符 n-gram 向量作为轻量兜底，保证三路融合链路真实可跑。
        self._embedding_enabled = not bool(base_url)

        self._tpl_embeddings: Dict[IntentCategory, List[List[float]]] = {}
        self._cache: Dict[str, IntentResult] = {}
        self.cache_hits   = 0
        self.cache_misses = 0

    # ── 公开接口 ──────────────────────────────────────────────────────────────

    async def recognize(
        self,
        message: str,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> IntentResult:
        """
        识别用户意图。

        history 格式：[{"role": "user"/"assistant", "content": "..."}]
        """
        key = self._cache_key(message, history)
        if key in self._cache:
            self.cache_hits += 1
            return self._cache[key]
        self.cache_misses += 1

        t0 = time.monotonic()

        # LLM 和 Embedding 并行（Embedding 不可用时跳过）
        llm_task = asyncio.create_task(self._llm_recognize(message, history))
        emb_task = asyncio.create_task(self._embedding_recognize(message)) if self._embedding_enabled else None
        pat      = self._pattern_recognize(message)

        if emb_task:
            llm, emb = await asyncio.gather(llm_task, emb_task)
        else:
            llm = await llm_task
            emb = {"intent": IntentCategory.OTHER, "confidence": 0.0}

        intent, confidence, source_scores = self._vote(llm, emb, pat)
        entities = self._extract_entities(message)
        urgency  = self._urgency(message, intent)

        result = IntentResult(
            intent=intent,
            confidence=confidence,
            urgency=urgency,
            intent_group=self._intent_group(intent),
            entities=entities,
            reasoning=llm.get("reasoning", ""),
            latency_ms=(time.monotonic() - t0) * 1000,
            source_scores=source_scores,
        )

        # LRU 缓存
        if len(self._cache) >= 1000:
            for k in list(self._cache)[:500]:
                del self._cache[k]
        self._cache[key] = result
        return result

    def learn(self, message: str, correct: IntentCategory) -> None:
        """在线学习：将纠正样本加入模板，清除对应 Embedding 缓存。"""
        tpls = _TEMPLATES.setdefault(correct, [])
        if message not in tpls:
            tpls.append(message)
            self._tpl_embeddings.pop(correct, None)  # 下次重新计算
            logger.info(f"学习新样本 → {correct.value}: {message[:40]}")

    # ── 三路识别策略 ──────────────────────────────────────────────────────────

    async def _llm_recognize(
        self,
        message: str,
        history: Optional[List[Dict[str, str]]],
    ) -> Dict[str, Any]:
        """策略 1：LLM 语义理解（Few-shot + 上下文）。"""
        message = self._clean_text(message)
        # 构建 Few-shot 示例
        examples = "\n".join(
            f'  消息: "{t}" → 意图: {cat.value}'
            for cat, tpls in _TEMPLATES.items()
            for t in tpls[:1]  # 每类取 1 条，控制 prompt 长度
        )
        # 最近 3 轮对话上下文
        ctx = ""
        if history:
            ctx = "\n最近对话:\n" + "\n".join(
                f"  {self._clean_text(m.get('role', 'user'))}: {self._clean_text(m.get('content', ''))}"
                for m in history[-3:]
            )

        prompt = f"""你是 SaaS 客户运营与交付问题分析专家。根据示例判断内部业务问题意图，返回 JSON。
如果用户问题能匹配细粒度业务意图，请优先返回细粒度意图，而不是宽泛大类。
优先区分 implementation、integration、reliability、entitlement、adoption。

示例:
{examples}

{ctx}
用户消息: "{message}"

返回格式（仅 JSON，不要其他文字）:
{{"intent": "<意图值>", "confidence": <0-1>, "reasoning": "<一句话说明>"}}

可选意图: {", ".join(c.value for c in IntentCategory)}"""
        prompt = self._clean_text(prompt)

        try:
            resp = await self.client.messages.create(
                model=self.model,
                max_tokens=256,
                temperature=0.1,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = extract_text_content(resp.content)
            s, e = raw.find("{"), raw.rfind("}") + 1
            data = json.loads(raw[s:e])
            try:
                data["intent"] = IntentCategory(data["intent"])
            except ValueError:
                data["intent"] = IntentCategory.OTHER
            return data
        except Exception as ex:
            logger.warning(f"LLM 识别失败: {ex}")
            return {"intent": IntentCategory.OTHER, "confidence": 0.0, "reasoning": "LLM 失败", "failed": True}

    async def _embedding_recognize(self, message: str) -> Dict[str, Any]:
        """策略 2：Embedding 向量相似度匹配。"""
        try:
            await self._load_template_embeddings()
            msg_vec = await self._embed_text(message)

            best_cat, best_score = IntentCategory.OTHER, 0.0
            for cat, vecs in self._tpl_embeddings.items():
                score = max(_cosine(msg_vec, v) for v in vecs)
                if score > best_score:
                    best_score, best_cat = score, cat

            return {"intent": best_cat, "confidence": best_score}
        except Exception as ex:
            logger.warning(f"Embedding 识别失败: {ex}")
            return {"intent": IntentCategory.OTHER, "confidence": 0.0}

    def _pattern_recognize(self, message: str) -> Dict[str, Any]:
        """策略 3：关键词模式匹配（同步，零延迟兜底）。"""
        msg = message.lower()
        specific_patterns = {
            IntentCategory.IMPLEMENTATION_PLAN: ["上线计划", "上线前", "实施计划", "实施步骤", "交付计划"],
            IntentCategory.ENVIRONMENT_SETUP: ["环境准备", "环境配置", "生产环境", "测试环境"],
            IntentCategory.DATA_MIGRATION: ["数据迁移", "迁移数据", "迁移校验"],
            IntentCategory.LAUNCH_VALIDATION: ["上线验收", "上线进度", "验收指标", "灰度验证"],
            IntentCategory.INTEGRATION_API: ["api 接入", "api 集成", "sdk 接入"],
            IntentCategory.INTEGRATION_WEBHOOK: ["webhook", "回调签名", "webhook 数据同步"],
            IntentCategory.INTEGRATION_SSO: ["sso", "单点登录"],
            IntentCategory.INTEGRATION_SYNC: ["数据同步", "同步延迟", "同步失败"],
            IntentCategory.RELIABILITY_INCIDENT: ["服务故障", "故障影响", "影响范围", "事件严重度"],
            IntentCategory.RELIABILITY_PERFORMANCE: ["性能", "延迟升高", "响应变慢", "吞吐"],
            IntentCategory.RELIABILITY_SLA: ["sla", "服务等级", "违约风险", "SLA 风险"],
            IntentCategory.SUCCESS_ENTITLEMENT: ["套餐", "服务权益", "功能范围"],
            IntentCategory.SUCCESS_QUOTA: ["配额", "额度", "调用量上限", "api 配额", "quota"],
            IntentCategory.SUCCESS_ADOPTION: ["功能采用", "使用推广", "采用率", "使用量", "adoption"],
            IntentCategory.SUCCESS_HEALTH: ["客户健康度", "客户健康", "健康分"],
            IntentCategory.RENEWAL_RISK: ["续费风险", "续约风险", "流失信号", "churn"],
            IntentCategory.RENEWAL_READINESS: ["续费准备", "续费前", "续费", "价值证明", "renewal"],
            IntentCategory.ESCALATION_RISK: ["升级风险", "需要升级", "管理层关注"],
            IntentCategory.CROSS_DOMAIN_ANALYSIS: ["综合分析", "整体分析", "跨领域"],
        }
        generic_patterns = {
            IntentCategory.ESCALATION: ["升级", "重大风险", "管理层关注", "escalate"],
            IntentCategory.COMPLAINT:  ["客户反馈", "体验问题", "不满意", "horrible"],
            IntentCategory.QUERY:      ["?", "？", "怎么", "什么", "如何", "status"],
            IntentCategory.REQUEST:    ["分析", "建议", "帮我", "需要", "please", "help"],
            IntentCategory.GREETING:   ["你好", "嗨", "hello", "hi"],
            IntentCategory.TECHNICAL:  ["技术支持", "报错", "error", "故障", "support"],
            IntentCategory.BILLING:    ["套餐", "配额", "续费", "订阅", "billing"],
            IntentCategory.ACCOUNT:    ["客户资料", "项目资料", "联系人", "account"],
        }

        best_cat, best_score = self._best_pattern_match(msg, specific_patterns)
        if best_cat != IntentCategory.OTHER:
            return {"intent": best_cat, "confidence": best_score}

        best_cat, best_score = self._best_pattern_match(msg, generic_patterns)
        return {"intent": best_cat, "confidence": best_score}

    # ── 投票合并 ──────────────────────────────────────────────────────────────

    def _vote(self, llm: Dict, emb: Dict, pat: Dict) -> tuple[IntentCategory, float, Dict[str, float]]:
        """加权投票。返回最终意图、融合置信度和各路来源得分。"""
        source_scores = {
            "llm": float(llm.get("confidence", 0.0) or 0.0),
            "embedding": float(emb.get("confidence", 0.0) or 0.0),
            "pattern": float(pat.get("confidence", 0.0) or 0.0),
        }
        if llm.get("failed"):
            if emb.get("intent") != IntentCategory.OTHER and emb.get("confidence", 0.0) > 0:
                return emb["intent"], source_scores["embedding"], source_scores
            if pat.get("intent") != IntentCategory.OTHER and pat.get("confidence", 0.0) > 0:
                return pat["intent"], source_scores["pattern"], source_scores
            return IntentCategory.OTHER, 0.0, source_scores

        if self._embedding_enabled:
            weights = [(llm, 0.7), (emb, 0.2), (pat, 0.1)]
        else:
            weights = [(llm, 0.85), (pat, 0.15)]
        scores: Dict[IntentCategory, float] = {}
        for result, w in weights:
            cat  = result.get("intent", IntentCategory.OTHER)
            conf = result.get("confidence", 0.0)
            scores[cat] = scores.get(cat, 0.0) + w * conf

        best = max(scores, key=scores.get)  # type: ignore
        best_score = scores[best]
        pat_intent = pat.get("intent", IntentCategory.OTHER)
        pat_conf = float(pat.get("confidence", 0.0) or 0.0)
        if best in _GENERIC_INTENTS and pat_intent in _SPECIFIC_INTENTS and pat_conf >= 0.5 and best_score < 0.8:
            source_scores["refined_by_pattern"] = pat_conf
            return pat_intent, max(best_score, pat_conf), source_scores
        if best_score < self.threshold:
            return IntentCategory.OTHER, best_score, source_scores
        return best, best_score, source_scores

    # ── 实体提取 ──────────────────────────────────────────────────────────────

    def _extract_entities(self, message: str) -> Dict[str, List[str]]:
        """用规则提取高价值实体，避免每次识别都额外调用 LLM。"""
        message = self._clean_text(message)
        return {
            "order_id": self._unique(re.findall(r"(?:订单号?|order(?:_id)?|#)\s*[:：#]?\s*([A-Za-z0-9_-]{4,32})", message, re.I)),
            "customer": self._unique(re.findall(r"(?:客户|customer)\s*[:：#]?\s*([A-Za-z0-9_-]{2,32})", message, re.I)),
            "project_id": self._unique(re.findall(r"(?:项目|project(?:_id)?)\s*[:：#]?\s*([A-Za-z0-9_-]{2,32})", message, re.I)),
            "environment": self._unique(re.findall(r"\b(production|staging|测试环境|生产环境|预发布)\b", message, re.I)),
            "integration": self._unique(re.findall(r"\b(webhook|api|sso|sdk|数据同步)\b", message, re.I)),
            "sla_plan": self._unique(re.findall(r"\b(sla|premium|standard|enterprise)\b", message, re.I)),
            "usage_metric": self._unique(re.findall(r"(?:使用量|活跃用户|调用量|usage)\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?%?)", message, re.I)),
            "requested_action": self._unique(re.findall(r"(?:建议|需要|请|帮我)\s*([^，。！？!?]{2,40})", message)),
            "product": self._unique(re.findall(r"\b(FlowForge Cloud|FlowForge)\b", message, re.I)),
            "date": self._unique(re.findall(r"(今天|明天|昨天|本周|这周|下周|\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?)", message)),
            "amount": self._unique(re.findall(r"((?:¥|￥)\s*\d+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?\s*(?:元|块|rmb|cny|usd|美元))", message, re.I)),
            "error_code": self._unique(re.findall(r"\b([45]\d{2}|[A-Z][A-Z0-9_-]{2,16})\b", message)),
        }

    # ── 辅助 ──────────────────────────────────────────────────────────────────

    async def _load_template_embeddings(self) -> None:
        """懒加载所有模板的 Embedding（只在首次调用时执行）。"""
        missing = [cat for cat in _TEMPLATES if cat not in self._tpl_embeddings]
        if not missing:
            return

        all_texts = [t for cat in missing for t in _TEMPLATES[cat]]
        vecs = [await self._embed_text(text) for text in all_texts]
        idx = 0
        for cat in missing:
            n = len(_TEMPLATES[cat])
            self._tpl_embeddings[cat] = vecs[idx: idx + n]
            idx += n

    async def _embed_text(self, text: str) -> List[float]:
        """
        生成文本向量。

        如果未来接入的官方/兼容客户端提供 embeddings.create，会优先使用远端向量；
        当前 Anthropic SDK 没有该资源时，退化为字符 n-gram 哈希向量。这样不会因为
        Embedding 服务缺失导致三路融合中断。
        """
        embeddings = getattr(self.client, "embeddings", None)
        if embeddings is not None:
            try:
                resp = await embeddings.create(model="voyage-3-lite", input=[text])
                return list(resp.data[0].embedding)
            except Exception as ex:
                logger.warning(f"远端 Embedding 失败，使用本地向量兜底: {ex}")

        return self._local_embedding(text)

    @staticmethod
    def _local_embedding(text: str, dims: int = 256) -> List[float]:
        """稳定的字符 n-gram 哈希向量，用于无远端 Embedding 时的语义近似匹配。"""
        normalized = text.lower().strip()
        vec = [0.0] * dims
        tokens = set()
        for n in (1, 2, 3):
            if len(normalized) >= n:
                tokens.update(normalized[i:i + n] for i in range(len(normalized) - n + 1))
        if not tokens:
            tokens.add(normalized)

        for token in tokens:
            digest = hashlib.md5(token.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "big") % dims
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[idx] += sign
        return vec

    def _urgency(self, message: str, intent: IntentCategory) -> UrgencyLevel:
        msg = message.lower()
        for level, kws in _URGENCY_KEYWORDS.items():
            if any(kw in msg for kw in kws):
                return level
        if intent in (IntentCategory.ESCALATION, IntentCategory.HUMAN_HANDOFF):
            return UrgencyLevel.HIGH
        if intent == IntentCategory.COMPLAINT:
            return UrgencyLevel.MEDIUM
        return UrgencyLevel.LOW

    def _cache_key(self, message: str, history: Optional[List[Dict[str, str]]] = None) -> str:
        payload = {"message": self._clean_text(message)[:200]}
        if history:
            payload["history"] = [
                {
                    "role": self._clean_text(item.get("role", ""))[:20],
                    "content": self._clean_text(item.get("content", ""))[:160],
                }
                for item in history[-3:]
            ]
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _unique(values: List[str]) -> List[str]:
        return list(dict.fromkeys(value.strip() for value in values if value and value.strip()))

    @staticmethod
    def _best_pattern_match(
        message: str,
        patterns: Dict[IntentCategory, List[str]],
    ) -> tuple[IntentCategory, float]:
        best_cat, best_score = IntentCategory.OTHER, 0.0
        for cat, kws in patterns.items():
            hits = sum(1 for kw in kws if kw in message)
            if not hits:
                continue
            # 单个明确业务关键词就给可用置信度；多个关键词命中时提高置信度。
            score = min(1.0, 0.5 + 0.25 * (hits - 1))
            if score > best_score:
                best_score, best_cat = score, cat
        return best_cat, best_score

    @staticmethod
    def _intent_group(intent: IntentCategory) -> str:
        return _INTENT_GROUPS.get(intent, intent).value

    @staticmethod
    def _clean_text(value: Any) -> str:
        """移除 Unicode 代理字符，避免 HTTP 客户端编码 prompt 时崩溃。"""
        if value is None:
            return ""
        if not isinstance(value, str):
            value = str(value)
        return value.encode("utf-8", errors="ignore").decode("utf-8")

    @property
    def cache_stats(self) -> Dict[str, Any]:
        total = self.cache_hits + self.cache_misses
        return {
            "size": len(self._cache),
            "hits": self.cache_hits,
            "misses": self.cache_misses,
            "hit_rate": self.cache_hits / total if total else 0.0,
        }
