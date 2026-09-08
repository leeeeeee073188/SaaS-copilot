"""Explicit sandbox entrypoint with the existing Agent runtime as an option."""
import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field
from saas.chat import ChatService
from saas.knowledge import SandboxKnowledge
from saas.memory import SandboxMemory
from saas.router import build_router
from saas.service import BusinessError, SaaSService


class ChatInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=6000)
    conv_id: str | None = Field(default=None, max_length=128)


def create_demo_app(path=None, orchestrator=None):
    path = Path(path or os.getenv("ECHOMIND_DEMO_DATA", "data/flowforge"))
    service = SaaSService(path / "flowforge.db")
    knowledge = SandboxKnowledge(path / "chroma", os.getenv("ECHOMIND_DEMO_EMBEDDING", "lexical"))
    knowledge.seed()
    memory = SandboxMemory(service, knowledge.client, os.getenv("ECHOMIND_DEMO_REDIS_URL"))
    if orchestrator is None and os.getenv("ECHOMIND_DEMO_LLM") == "1":
        from agents.agent_orchestrator import AgentOrchestrator
        key = os.getenv("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ECHOMIND_DEMO_LLM=1 requires ANTHROPIC_API_KEY")
        orchestrator = AgentOrchestrator(api_key=key, base_url=os.getenv("ANTHROPIC_BASE_URL") or None,
                                         model=os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"))
    chat_service = ChatService(service, knowledge, memory, orchestrator)

    @asynccontextmanager
    async def lifespan(app):
        yield
        await memory.close()

    app = FastAPI(title="FlowForge + EchoMind sandbox", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                       allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"])
    app.include_router(build_router(service))
    app.state.saas, app.state.chat_service = service, chat_service

    def actor(header):
        try:
            return service.actor(header.removeprefix("Bearer "))
        except BusinessError as ex:
            raise HTTPException(ex.status, {"code": ex.code, "message": str(ex)}) from ex

    @app.get("/health")
    def health():
        return {"status": "ok", "demo": True, "engine": "llm" if orchestrator else "deterministic_demo",
                "memory_backend": "redis" if memory.redis else "sqlite", "embedding": knowledge.embedding}

    @app.post("/chat")
    async def chat(body: ChatInput, authorization: str = Header(default="")):
        who = actor(authorization)
        try:
            return await chat_service.handle(who, body.message, body.conv_id)
        except BusinessError as ex:
            raise HTTPException(ex.status, {"code": ex.code, "message": str(ex)}) from ex
        except asyncio.TimeoutError:
            raise HTTPException(504, "请求超时；写操作请先查询回执")

    @app.get("/knowledge/stats")
    def stats():
        return {"total_chunks": knowledge.collection.count(), "collection": knowledge.collection.name}

    @app.post("/search")
    def search(query: str, top_k: int = 5, authorization: str = Header(default="")):
        return {"results": knowledge.search(query, actor(authorization).org_id, top_k)}

    @app.get("/trace/tools")
    def traces(authorization: str = Header(default="")):
        who = actor(authorization)
        return {"traces": [t for t in chat_service.traces if t["org_id"] == who.org_id and t["actor_id"] == who.user_id][-20:]}

    return app
