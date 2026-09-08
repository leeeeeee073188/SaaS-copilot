"""
亮点：多轮对话记忆管理

三级记忆架构，模拟人类记忆机制：
  1. 工作记忆（Redis）—— 当前会话的最近 N 条消息，毫秒级读写
  2. 情景记忆（ChromaDB）—— 跨会话的历史对话，按语义相似度检索
  3. 用户画像（ChromaDB）—— 从对话中提炼的长期偏好和实体

关键设计：
  - 上下文构建时三级记忆融合，按重要性 + 时效性排序
  - 工作记忆超过阈值时自动压缩（LLM 摘要），防止 context 爆炸
  - 所有 Embedding 通过 Anthropic API 生成，无本地模型
"""
import hashlib
import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

import chromadb
import redis.asyncio as redis
from anthropic import AsyncAnthropic

from core.llm_utils import extract_text_content

logger = logging.getLogger(__name__)


class MsgRole(Enum):
    USER      = "user"
    ASSISTANT = "assistant"
    SYSTEM    = "system"


@dataclass
class Message:
    role:       MsgRole
    content:    str
    timestamp:  datetime = field(default_factory=datetime.now)
    metadata:   Dict[str, Any] = field(default_factory=dict)


@dataclass
class MemoryContext:
    """传给 Agent 的完整上下文。"""
    recent_messages:  List[Message]   # 工作记忆：最近对话
    relevant_history: List[str]       # 情景记忆：语义相关的历史片段
    user_profile:     Dict[str, Any]  # 用户画像：偏好、常用实体
    summary:          str             # 当前会话摘要（压缩后）

    @staticmethod
    def _clean(text: str) -> str:
        """移除 Unicode 代理字符，防止编码错误。"""
        return text.encode("utf-8", errors="ignore").decode("utf-8")

    def to_prompt_text(self) -> str:
        """将记忆上下文格式化为 LLM 可用的文本。"""
        parts = []
        if self.summary:
            parts.append(f"[会话摘要]\n{self._clean(self.summary)}")
        if self.relevant_history:
            parts.append("[相关历史]\n" + "\n".join(f"- {self._clean(h)}" for h in self.relevant_history[:3]))
        if self.user_profile:
            parts.append(f"[用户画像]\n{json.dumps(self.user_profile, ensure_ascii=True)}")
        if self.recent_messages:
            parts.append("[最近对话]")
            for m in self.recent_messages:
                parts.append(f"{m.role.value}: {self._clean(m.content)}")
        return "\n\n".join(parts)


class MemoryManager:
    """
    三级记忆管理器。

    工作记忆存 Redis（TTL 24h），情景记忆和用户画像存 ChromaDB（持久化）。
    """

    WORKING_MAX   = 20    # 工作记忆最大条数，超过则触发压缩
    COMPRESS_AT   = 15    # 达到此条数时压缩，保留摘要 + 最近 5 条
    HISTORY_TOP_K = 5     # 情景记忆检索返回条数

    def __init__(
        self,
        redis_url:    str = "redis://localhost:6379/0",
        chroma_host:  str = "localhost",
        chroma_port:  int = 8000,
        chroma_path:  str = "./data/chroma",
        api_key:      str = "",
        base_url:     Optional[str] = None,
        model:        str = "claude-3-5-sonnet-20241022",
    ):
        kwargs: Dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = AsyncAnthropic(**kwargs)
        self._model  = model

        self._redis = redis.from_url(redis_url, decode_responses=True)
        # 同一用户的画像更新串行执行，避免并发 LLM 调用以完成顺序覆盖画像。
        # 不同用户使用不同锁，仍可并发更新。
        self._profile_locks: Dict[str, asyncio.Lock] = {}

        # ChromaDB：优先连接独立服务（docker compose 模式），连不上则降级为本地嵌入式
        try:
            # HttpClient 默认也会初始化 ChromaDB telemetry；显式关闭避免 posthog 兼容性错误日志。
            chroma = chromadb.HttpClient(
                host=chroma_host,
                port=chroma_port,
                settings=chromadb.Settings(anonymized_telemetry=False),
            )
            chroma.heartbeat()  # 测试连接
            logger.info(f"ChromaDB 已连接: {chroma_host}:{chroma_port}")
        except Exception:
            logger.info(f"ChromaDB 服务不可用，使用本地嵌入式模式: {chroma_path}")
            chroma = chromadb.PersistentClient(
                path=chroma_path,
                settings=chromadb.Settings(anonymized_telemetry=False),
            )

        # 情景记忆：存储历史对话片段
        self._episodic = chroma.get_or_create_collection("episodic")
        # 用户画像：存储提炼出的偏好和实体
        self._profile  = chroma.get_or_create_collection("user_profile")

    # ── 写入 ──────────────────────────────────────────────────────────────────

    async def add_message(
        self,
        user_id: str,
        conv_id: str,
        role:    MsgRole,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """将一条消息写入工作记忆，超阈值时自动压缩。"""
        user_id = self._safe_text(user_id)
        conv_id = self._safe_text(conv_id)
        clean_metadata = {
            self._safe_text(k): self._safe_metadata_value(v)
            for k, v in (metadata or {}).items()
        }
        msg = Message(role=role, content=self._safe_text(content), metadata=clean_metadata)
        key = self._wm_key(user_id, conv_id)

        # 追加到 Redis 列表（左推，最新在前）
        await self._redis.lpush(key, json.dumps({
            "role":      msg.role.value,
            "content":   msg.content,
            "ts":        msg.timestamp.isoformat(),
            "metadata":  msg.metadata,
        }))
        await self._redis.expire(key, 86400)  # 24h TTL

        # 超过压缩阈值时触发压缩
        if await self._redis.llen(key) >= self.COMPRESS_AT:
            await self._compress(user_id, conv_id)

    async def update_profile(self, user_id: str, conv_id: str) -> None:
        """
        从当前工作记忆中提炼用户偏好，更新用户画像。
        用 LLM 提炼偏好，然后存入 ChromaDB（ChromaDB 内置 embedding，不依赖外部 API）。
        """
        user_id = self._safe_text(user_id)
        conv_id = self._safe_text(conv_id)
        lock = self._profile_locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            await self._update_profile_locked(user_id, conv_id)

    async def _update_profile_locked(self, user_id: str, conv_id: str) -> None:
        """在单用户锁内生成并覆盖该用户唯一的画像快照。"""
        messages = await self._get_working_memory(user_id, conv_id)
        if not messages:
            return

        old_profile = await self._get_profile(user_id)
        profile_for_llm = self._redact_profile_for_llm(old_profile)
        text = self._safe_text("\n".join(f"{m.role.value}: {m.content}" for m in messages[-10:]))
        source_ts = messages[-1].timestamp.isoformat()
        prompt = f"""根据已有用户画像和最近对话，提取画像的字段级变更建议，只返回 JSON。
已有画像:
{json.dumps(profile_for_llm, ensure_ascii=False)}

对话:
{text}

返回格式: {{"changes": [{{"operation": "add|replace|remove", "section": "preferences|entities", "key": "字段名", "value": "字段值", "evidence": "对话依据", "confidence": 0.95, "explicit": true}}]}}
confidence 必须是 0 到 1 之间的数字；evidence 应说明最近对话中的依据。
没有变化时返回 {{"changes": []}}。不要返回密码、令牌、API Key 或其他凭证。"""
        prompt = self._safe_text(prompt)

        try:
            resp = await self._client.messages.create(
                model=self._model, max_tokens=512, temperature=0.0,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = extract_text_content(resp.content)
            s, e = raw.find("{"), raw.rfind("}") + 1
            extracted = json.loads(raw[s:e])
            if isinstance(extracted, dict) and isinstance(extracted.get("changes"), list):
                profile_data = self._merge_profile_changes(old_profile, extracted["changes"])
            else:
                logger.warning("忽略不符合字段级变更协议的用户画像响应: %s", user_id)
                return

            doc_id = self._profile_doc_id(user_id)
            doc_text = self._safe_text(json.dumps(profile_data, ensure_ascii=False))

            # 直接传 documents，让 ChromaDB 内置模型生成 embedding（不依赖 Voyage API）
            await asyncio.to_thread(
                self._profile.upsert,
                ids=[doc_id],
                documents=[doc_text],
                metadatas=[{"user_id": user_id, "conv_id": conv_id,
                            "source_ts": source_ts, "updated_at": datetime.now().isoformat()}],
            )
            await self._delete_legacy_profile_docs(user_id, doc_id)
            logger.info(f"用户画像已更新: {user_id}")
        except Exception as ex:
            logger.warning(f"更新用户画像失败: {ex}")

    @staticmethod
    def _merge_profile_changes(old_profile: Any, changes: List[Any]) -> Dict[str, Any]:
        """将 LLM 提议的增量变更应用到画像；未提及字段保持不变。"""
        profile = json.loads(json.dumps(old_profile)) if isinstance(old_profile, dict) else {}
        profile.setdefault("profile_version", 2)
        updated_at = datetime.now().isoformat()
        legacy_preferences = profile.get("preferences")
        if isinstance(legacy_preferences, list):
            profile["preferences"] = {
                f"legacy_{hashlib.sha256(json.dumps(value, ensure_ascii=False).encode('utf-8')).hexdigest()[:12]}": {
                    "value": value,
                    "source": "legacy_profile",
                    "confidence": 0.7,
                    "updated_at": str(profile.get("updated_at") or updated_at),
                    "expires_at": None,
                }
                for value in legacy_preferences
            }
        elif not isinstance(legacy_preferences, dict):
            profile["preferences"] = {}
        if not isinstance(profile.get("entities"), dict):
            profile["entities"] = {}

        for change in changes:
            if not isinstance(change, dict):
                continue
            operation = change.get("operation")
            section = change.get("section")
            key = change.get("key")
            if section not in ("preferences", "entities") or not isinstance(key, str) or not key.strip():
                continue
            key = key.strip()
            if operation != "remove" and MemoryManager._is_sensitive_profile_change(key, change.get("value")):
                continue
            confidence = change.get("confidence", 0.0)
            valid_confidence = (
                isinstance(confidence, (int, float))
                and not isinstance(confidence, bool)
                and 0.0 <= confidence <= 1.0
            )
            if operation == "add":
                if not valid_confidence or confidence < 0.7 or key in profile[section]:
                    continue
            if operation == "replace":
                if change.get("explicit") is not True or not valid_confidence or confidence < 0.8:
                    continue
                if key not in profile[section]:
                    continue
            if operation == "remove":
                if change.get("explicit") is not True or not valid_confidence or confidence < 0.9:
                    continue
                profile[section].pop(key, None)
                continue
            if operation in ("add", "replace"):
                profile[section][key] = {
                    "value": change.get("value"),
                    "source": str(change.get("evidence") or ""),
                    "confidence": confidence,
                    "updated_at": updated_at,
                    "expires_at": None,
                }
        profile["updated_at"] = updated_at
        return profile

    @staticmethod
    def _is_sensitive_profile_change(key: str, value: Any) -> bool:
        """阻止凭证字段及典型密钥值进入长期用户画像。"""
        normalized_key = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", key.lower()).strip("_")
        blocked_keys = (
            "password", "passwd", "secret", "token", "api_key", "apikey",
            "credential", "private_key", "access_key", "密码", "密钥", "令牌", "凭证",
        )
        if any(term in normalized_key for term in blocked_keys):
            return True
        serialized_value = json.dumps(value, ensure_ascii=False) if value is not None else ""
        secret_patterns = (
            r"\bsk-[A-Za-z0-9_-]{8,}\b",
            r"\bAKIA[A-Z0-9]{12,}\b",
            r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b",
        )
        return any(re.search(pattern, serialized_value) for pattern in secret_patterns)

    @staticmethod
    def _redact_profile_for_llm(profile: Any) -> Dict[str, Any]:
        """生成仅供语义判断的画像副本，过滤可能遗留的凭证。"""
        if not isinstance(profile, dict):
            return {}
        safe_profile = {
            key: profile[key]
            for key in ("profile_version", "updated_at")
            if key in profile
        }
        for section in ("preferences", "entities"):
            values = profile.get(section)
            if isinstance(values, dict):
                safe_profile[section] = {
                    key: value
                    for key, value in values.items()
                    if not MemoryManager._is_sensitive_profile_change(
                        str(key), value.get("value") if isinstance(value, dict) else value
                    )
                }
            elif isinstance(values, list):
                safe_profile[section] = [
                    value for value in values
                    if not MemoryManager._is_sensitive_profile_change("", value)
                ]
        return safe_profile

    # ── 读取 ──────────────────────────────────────────────────────────────────

    async def get_context(self, user_id: str, conv_id: str, query: str = "") -> MemoryContext:
        """
        构建完整的记忆上下文。

        query 用于从情景记忆中检索语义相关的历史片段。
        """
        # 1. 工作记忆（当前会话最近消息）
        user_id = self._safe_text(user_id)
        conv_id = self._safe_text(conv_id)
        query = self._safe_text(query)

        recent = await self._get_working_memory(user_id, conv_id)

        # 2. 情景记忆（跨会话语义检索）
        history = await self._search_episodic(user_id, query or (recent[-1].content if recent else ""))

        # 3. 用户画像
        profile = await self._get_profile(user_id)

        # 4. 会话摘要（如果已压缩过）
        summary = await self._redis.get(self._summary_key(user_id, conv_id)) or ""

        return MemoryContext(
            recent_messages=recent,
            relevant_history=history,
            user_profile=profile,
            summary=summary,
        )

    # ── 压缩（防止 context 爆炸）─────────────────────────────────────────────

    async def _compress(self, user_id: str, conv_id: str) -> None:
        """
        工作记忆压缩：
          1. 用 LLM 对旧消息生成摘要
          2. 摘要存 Redis（覆盖旧摘要）
          3. 旧消息存入情景记忆（ChromaDB）供跨会话检索
          4. 工作记忆只保留最近 5 条
        """
        messages = await self._get_working_memory(user_id, conv_id)
        if len(messages) < self.COMPRESS_AT:
            return

        to_compress = messages[:-5]   # 保留最近 5 条
        keep        = messages[-5:]

        # LLM 摘要
        text = self._safe_text("\n".join(f"{m.role.value}: {m.content}" for m in to_compress))
        prompt = self._safe_text(f"用 2-3 句话总结以下对话的关键信息：\n{text}")
        try:
            resp = await self._client.messages.create(
                model=self._model, max_tokens=256, temperature=0.0,
                messages=[{"role": "user", "content": prompt}],
            )
            summary = self._safe_text(extract_text_content(resp.content)).strip()
        except Exception:
            summary = f"对话包含 {len(to_compress)} 条消息（摘要生成失败）"

        # 存摘要到 Redis
        skey = self._summary_key(user_id, conv_id)
        old_summary = await self._redis.get(skey) or ""
        new_summary = self._safe_text(f"{old_summary}\n{summary}").strip()
        await self._redis.setex(skey, 86400, new_summary)

        # 旧消息存入情景记忆
        await self._store_episodic(user_id, conv_id, text, summary)

        # 重置工作记忆为最近 5 条
        key = self._wm_key(user_id, conv_id)
        await self._redis.delete(key)
        for m in reversed(keep):
            await self._redis.lpush(key, json.dumps({
                "role": m.role.value, "content": m.content,
                "ts": m.timestamp.isoformat(), "metadata": m.metadata,
            }))
        await self._redis.expire(key, 86400)
        logger.info(f"工作记忆压缩完成: {user_id}/{conv_id}，摘要 {len(summary)} 字")

    # ── 内部辅助 ──────────────────────────────────────────────────────────────

    async def _get_working_memory(self, user_id: str, conv_id: str) -> List[Message]:
        key  = self._wm_key(user_id, conv_id)
        raws = await self._redis.lrange(key, 0, self.WORKING_MAX - 1)
        msgs = []
        for raw in reversed(raws):  # Redis lpush 最新在前，reversed 还原时序
            d = json.loads(raw)
            msgs.append(Message(
                role=MsgRole(d["role"]),
                content=d["content"],
                timestamp=datetime.fromisoformat(d["ts"]),
                metadata=d.get("metadata", {}),
            ))
        return msgs

    async def _search_episodic(self, user_id: str, query: str) -> List[str]:
        """语义检索情景记忆。ChromaDB 内置 embedding，不依赖外部 API。"""
        query_text = self._safe_text(query).strip()
        if not query_text:
            return []
        try:
            # 直接传 query_texts，ChromaDB 内置模型自动生成向量做匹配
            results = await asyncio.to_thread(
                self._episodic.query,
                query_texts=[query_text],
                n_results=self.HISTORY_TOP_K,
                where={"user_id": self._safe_text(user_id)},
            )
            docs = results["documents"][0] if results["documents"] else []
            return [self._safe_text(doc) for doc in docs if isinstance(doc, str) and doc.strip()]
        except Exception as ex:
            logger.warning(f"情景记忆检索失败: {ex}")
            return []

    async def _store_episodic(self, user_id: str, conv_id: str, text: str, summary: str) -> None:
        """将压缩后的对话片段存入情景记忆。ChromaDB 内置 embedding，不依赖外部 API。"""
        try:
            user_id = self._safe_text(user_id)
            conv_id = self._safe_text(conv_id)
            text = self._safe_text(text)
            summary = self._safe_text(summary)
            doc_id = hashlib.md5(f"{user_id}{conv_id}{time.time()}".encode()).hexdigest()
            # 直接传 documents，ChromaDB 内置模型自动生成 embedding
            await asyncio.to_thread(
                self._episodic.add,
                ids=[doc_id],
                documents=[summary],
                metadatas=[{"user_id": user_id, "conv_id": conv_id,
                            "ts": datetime.now().isoformat(), "full_text": self._safe_text(text[:500])}],
            )
        except Exception as ex:
            logger.warning(f"存储情景记忆失败: {ex}")

    async def _get_profile(self, user_id: str) -> Dict[str, Any]:
        """获取用户唯一画像；兼容旧的按会话画像并按时间选择最新记录。"""
        try:
            canonical = await asyncio.to_thread(
                self._profile.get,
                ids=[self._profile_doc_id(user_id)],
            )
            if canonical.get("documents"):
                return json.loads(canonical["documents"][0])

            # 兼容升级前的 `{user_id}_profile_{conv_id}` 多记录结构。
            legacy = await asyncio.to_thread(self._profile.get, where={"user_id": user_id})
            documents = legacy.get("documents") or []
            metadatas = legacy.get("metadatas") or []
            if documents:
                newest_index = max(
                    range(len(documents)),
                    key=lambda index: self._profile_timestamp(
                        metadatas[index] if index < len(metadatas) else {}
                    ),
                )
                return json.loads(documents[newest_index])
        except Exception:
            pass
        return {}

    async def _delete_legacy_profile_docs(self, user_id: str, canonical_id: str) -> None:
        """在写入唯一画像后清理该用户遗留的按会话画像记录。"""
        try:
            records = await asyncio.to_thread(self._profile.get, where={"user_id": user_id})
            legacy_ids = [doc_id for doc_id in (records.get("ids") or []) if doc_id != canonical_id]
            if legacy_ids:
                await asyncio.to_thread(self._profile.delete, ids=legacy_ids)
        except Exception as ex:
            logger.debug("清理旧用户画像失败: %s", ex)

    async def close(self) -> None:
        """关闭异步 Redis 连接。"""
        await self._redis.aclose()

    @staticmethod
    def _wm_key(user_id: str, conv_id: str) -> str:
        return f"wm:{user_id}:{conv_id}"

    @staticmethod
    def _summary_key(user_id: str, conv_id: str) -> str:
        return f"summary:{user_id}:{conv_id}"

    @staticmethod
    def _profile_doc_id(user_id: str) -> str:
        """生成稳定且不暴露原始用户标识的 ChromaDB 画像文档 ID。"""
        digest = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
        return f"user_profile:{digest}"

    @staticmethod
    def _profile_timestamp(metadata: Any) -> str:
        if not isinstance(metadata, dict):
            return ""
        return str(metadata.get("source_ts") or metadata.get("updated_at") or metadata.get("ts") or "")

    @staticmethod
    def _safe_text(value: Any) -> str:
        """转成 ChromaDB 可接受的普通 UTF-8 字符串。"""
        if value is None:
            return ""
        if not isinstance(value, str):
            value = str(value)
        return value.encode("utf-8", errors="ignore").decode("utf-8")

    @classmethod
    def _safe_metadata_value(cls, value: Any) -> Any:
        """递归清洗 metadata，避免 Redis/ChromaDB 后续读写遇到非法 UTF-8。"""
        if isinstance(value, str):
            return cls._safe_text(value)
        if isinstance(value, dict):
            return {cls._safe_text(k): cls._safe_metadata_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [cls._safe_metadata_value(v) for v in value]
        return value
