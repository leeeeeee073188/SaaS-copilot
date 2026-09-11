"""Application factory for the standalone SaaS support workspace."""
import asyncio
import os
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST
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
    monitor = chat_service.monitor

    @asynccontextmanager
    async def lifespan(app):
        try:
            await memory.redis.ping()
            yield
        finally:
            await memory.close()

    app = FastAPI(title="FlowForge + EchoMind sandbox", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                       allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"], expose_headers=["X-Request-ID"])
    app.include_router(build_router(service))
    app.state.saas, app.state.chat_service = service, chat_service

    @app.middleware("http")
    async def observe_http(request: Request, call_next):
        request.state.request_id = str(uuid.uuid4())
        started = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            response = JSONResponse(status_code=500, content={"detail": "服务处理失败，请凭请求编号查询", "request_id": request.state.request_id})
        response.headers["X-Request-ID"] = request.state.request_id
        route = getattr(request.scope.get("route"), "path", "unmatched")
        monitor.http_requests.labels(route, str(response.status_code)).inc()
        monitor.http_latency.labels(route).observe(time.monotonic() - started)
        return response

    def actor(header):
        try:
            return service.actor(header.removeprefix("Bearer "))
        except BusinessError as ex:
            raise HTTPException(ex.status, {"code": ex.code, "message": str(ex)}) from ex

    @app.get("/health")
    def health():
        return {"status": "ok", "demo": True, "engine": "llm" if orchestrator else "deterministic_demo",
                "memory_backend": "redis", "embedding": knowledge.embedding}

    @app.post("/chat")
    async def chat(body: ChatInput, request: Request, authorization: str = Header(default="")):
        who = actor(authorization)
        try:
            return await chat_service.handle(who, body.message, body.conv_id, request.state.request_id)
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
    def traces(authorization: str = Header(default=""), limit: int = Query(default=20, ge=1, le=100)):
        who = actor(authorization)
        return {"traces": monitor.recent(who, limit)}

    @app.get("/trace/tools/{request_id}")
    def trace(request_id: str, authorization: str = Header(default="")):
        rows = monitor.recent(actor(authorization), 1, request_id)
        if not rows:
            raise HTTPException(404, "轨迹不存在")
        return rows[0]

    @app.get("/monitor")
    def summary(authorization: str = Header(default="")):
        return monitor.summary(actor(authorization))

    @app.get("/metrics")
    def metrics(authorization: str = Header(default="")):
        expected = os.getenv("ECHOMIND_METRICS_TOKEN", "")
        if not expected or not secrets.compare_digest(authorization, "Bearer " + expected):
            raise HTTPException(403, "需要独立监控凭证")
        return Response(content=monitor.metrics(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/ready")
    async def ready():
        checks = {}
        async def check(name, fn):
            try:
                await asyncio.wait_for(asyncio.to_thread(fn), timeout=2)
                checks[name] = "ok"
            except Exception:
                checks[name] = "unavailable"
        def sql_check():
            with service.tx() as db:
                db.execute("SELECT 1").fetchone()
        def trace_check():
            with monitor.connect() as db:
                db.execute("SELECT 1 FROM traces LIMIT 1").fetchone()
        await asyncio.gather(check("business_db", sql_check), check("chroma", knowledge.client.heartbeat), check("trace_db", trace_check))
        if memory.redis:
            try:
                await asyncio.wait_for(memory.redis.ping(), timeout=2)
                checks["redis"] = "ok"
            except Exception:
                checks["redis"] = "unavailable"
        healthy = all(value == "ok" for value in checks.values())
        return JSONResponse(status_code=200 if healthy else 503, content={"status": "ready" if healthy else "degraded", "checks": checks})

    return app
