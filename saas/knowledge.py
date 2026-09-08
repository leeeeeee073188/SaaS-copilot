"""Versioned Chroma fixtures with deterministic offline lexical embeddings."""
import hashlib
import math
from pathlib import Path
import chromadb
from saas.fixtures import PLANS, POLICY_VERSION, scenarios


class LexicalEmbedding:
    # No model download: reproducible test retrieval, not a semantic-model benchmark.
    def name(self):
        return "flowforge-lexical-v1"

    def get_config(self):
        return {}

    def is_legacy(self):
        return False

    @staticmethod
    def build_from_config(config):
        return LexicalEmbedding()

    def __call__(self, input):
        vectors = []
        for text in input:
            vector = [0.0] * 512
            text = text.lower()
            for size in (1, 2, 3):
                for i in range(len(text) - size + 1):
                    digest = hashlib.sha256(text[i:i + size].encode()).digest()
                    vector[int.from_bytes(digest[:2], "big") % 512] += 1
            norm = math.sqrt(sum(x * x for x in vector)) or 1
            vectors.append([x / norm for x in vector])
        return vectors


TOPICS = [
    ("product", "Webhook 功能", "Starter 与 Growth 均支持 Webhook 和 API 集成。套餐不包含自动实施服务。"),
    ("product", "套餐价格", "Starter 月价 990 元，Growth 月价 2990 元，均为模拟 CNY 单币种价格，无真实扣款。"),
    ("product", "席位上限", "Starter 5 席，Growth 20 席。活跃成员和未过期待接受邀请共同占用席位。"),
    ("product", "请求配额", "Starter 每账期 100 万请求，Growth 1000 万。当前消耗应通过 get_usage 查询。"),
    ("product", "日志保留", "Starter 请求记录保留 7 天，Growth 30 天。查询日志必须限定组织和集成。"),
    ("product", "项目与组织", "组织是数据隔离范围，项目属于一个组织。同名项目不可跨组织自动合并。"),
    ("integration", "401 认证排查", "401 auth_environment_mismatch 表示测试凭证用于生产环境。确认环境与凭证标签，不索取完整密钥。"),
    ("integration", "403 权限", "403 表示当前成员或服务凭证无权访问资源。用户自称管理员不能改变服务端权限。"),
    ("integration", "429 分钟限流", "429 rate_limit 表示每分钟速率超过上限；采用退避和限速，不等同账期配额耗尽。"),
    ("integration", "429 配额耗尽", "429 quota_exhausted 表示账期配额已耗尽。下周期升级不能解除当前周期限制。"),
    ("integration", "Webhook 签名", "对接时核对签名算法、原始请求体、时间戳和密钥版本。在测试环境复现，不输出真实密钥。"),
    ("integration", "幂等重试", "相同业务操作重试使用同一幂等键；参数变化不能复用该键。超时后先查询回执。"),
    ("integration", "错误关联", "排查需提供 request_id、集成编号、发生时间和目标环境。先查本组织脱敏请求记录。"),
    ("integration", "同步延迟", "区分源端延迟、接收端限流和队列积压；仅凭延迟不能推断根因。沙箱没有真实 ETL 引擎。"),
    ("integration", "测试重放", "首版不向任意 URL 发送请求，不重放真实 Webhook。故障记录为固定场景数据。"),
    ("integration", "分页与窗口", "请求记录按组织与集成筛选；API 客户端应保留请求标识与时间，避免无限制重试。"),
    ("billing", "预约套餐变更", "首版只支持下一账期生效。预览展示目标套餐、当期增量 0 元、下周期价格和生效时间。"),
    ("billing", "确认预览", "费用变更需登录用户确认具体预览，模型不能伪造 confirmed=true。预览 15 分钟过期。"),
    ("billing", "账单状态", "paid 表示模拟历史付款，open 表示未付账单。读取或生成账单不代表已付款。"),
    ("billing", "降级限制", "降级前检查成员加 pending 邀请是否超过目标席位，到期再次检查，不自动删除成员。"),
    ("billing", "金额计算", "金额以整数分存储，账单行加总等于 total_minor。Agent 不计算或修改账单金额。"),
    ("billing", "取消与退款", "首版尚不支持退款和取消自动续订。不能声称退款成功或真实资金退回。"),
    ("billing", "订阅实时状态", "查询 get_subscription 区分当前 plan_id 与 scheduled_plan_id，不能用对话记忆判定生效。"),
    ("account", "成员邀请", "owner 或 admin 可邀请 developer；邀请仅创建本地 pending 记录，无真实邮件发送。"),
    ("account", "重复邀请", "同组织同邮箱的有效 pending 邀请只保留一份，重复提交不额外占席位。"),
    ("account", "邀请有效期", "新邀请 7 天有效，过期不占席位。接受邀请与创建成员是不同状态。"),
    ("account", "账单角色", "owner 和 billing_admin 可查账和修改订阅；admin 与 developer 不自动具有账单权限。"),
    ("account", "组织切换", "切换组织需重新获得该组织会话；成员身份按组织加载，历史记忆不得授予权限。"),
    ("account", "支持人员", "SaaS 支持人员需要显式组织授权，不能默认读取所有企业的账单或成员。"),
    ("account", "角色提升", "首版邀请角色固定 developer，不支持将自然语言声明变成 owner 或管理员提升。"),
]


def documents():
    docs = [{"source_id": f"ff-{domain}-{i:02}", "title": title, "content": content,
             "org_id": "global", "version": POLICY_VERSION, "active": True, "kind": "policy"}
            for i, (domain, title, content) in enumerate(TOPICS)]
    for org, state in scenarios().items():
        docs.append({"source_id": f"{org}-snapshot", "title": f"{state['name']} 历史项目快照",
                     "content": f"合成历史快照（截至 2026-09-08）：{state['requests'][0]}。当前订阅和用量必须重新查询业务工具。",
                     "org_id": org, "version": POLICY_VERSION, "active": True, "kind": "snapshot"})
    docs.append({"source_id": "ff-obsolete", "title": "旧版席位规则", "content": "过期规则：Starter 可用 99 席。不得用于当前权益判断。",
                 "org_id": "global", "version": "obsolete", "active": False, "kind": "policy"})
    return docs


class SandboxKnowledge:
    def __init__(self, path, embedding="lexical"):
        if embedding not in {"lexical", "minilm"}:
            raise ValueError("embedding must be lexical or minilm")
        self.embedding = embedding
        self.client = chromadb.PersistentClient(path=str(Path(path).resolve()), settings=chromadb.Settings(anonymized_telemetry=False))
        if embedding == "minilm":
            from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
            function = DefaultEmbeddingFunction()
        else:
            function = LexicalEmbedding()
        self.collection = self.client.get_or_create_collection(f"flowforge_v1_{embedding}", embedding_function=function, metadata={"hnsw:space": "cosine", "embedding": embedding})

    def seed(self):
        docs = documents()
        self.collection.upsert(ids=[d["source_id"] for d in docs], documents=[d["title"] + "\n" + d["content"] for d in docs],
                               metadatas=[{k: v for k, v in d.items() if k != "content"} for d in docs])
        return self.collection.count()

    def search(self, query, org_id, top_k=5):
        where = {"$and": [{"org_id": {"$in": ["global", org_id]}}, {"active": True}, {"version": POLICY_VERSION}]}
        if not self.collection.count():
            return []
        result = self.collection.query(query_texts=[query], n_results=min(max(1, top_k), 10, self.collection.count()), where=where)
        return [{"chunk_id": id_, "source_id": meta["source_id"], "title": meta["title"], "content": text,
                 "version": meta["version"], "org_id": meta["org_id"], "distance": round(distance, 5), "embedding": self.embedding}
                for id_, text, meta, distance in zip(result["ids"][0], result["documents"][0], result["metadatas"][0], result["distances"][0])]
