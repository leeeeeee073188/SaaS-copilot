"""Bounded, tenant-scoped traces and process-local Prometheus metrics."""
import hashlib
import json
import logging
import math
import sqlite3
import time
import uuid
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path

from prometheus_client import CollectorRegistry, Counter as MetricCounter, Histogram, generate_latest

logger = logging.getLogger(__name__)
current_trace = ContextVar("business_trace", default=None)


@dataclass
class Trace:
    org_id: str
    actor_id: str
    conv_id: str
    engine: str
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    started_at: float = field(default_factory=time.time)
    started: float = field(default_factory=time.monotonic)
    domain: str = "unknown"
    action: str = "unknown"
    status: str = "ok"
    spans: list = field(default_factory=list)
    tools: list = field(default_factory=list)
    usage: list = field(default_factory=list)
    retrieval: dict = field(default_factory=dict)
    operation_ids: list = field(default_factory=list)

    def payload(self):
        return {"request_id": self.request_id, "org_id": self.org_id, "actor_id": self.actor_id,
                "conversation_ref": hashlib.sha256(self.conv_id.encode()).hexdigest(),
                "engine": self.engine, "domain": self.domain, "action": self.action, "status": self.status,
                "started_at": self.started_at, "latency_ms": round((time.monotonic() - self.started) * 1000, 2),
                "spans": self.spans, "tool_traces": self.tools, "llm_usage": self.usage,
                "retrieval": self.retrieval, "operation_ids": self.operation_ids}


@contextmanager
def span(name):
    trace, started = current_trace.get(), time.monotonic()
    status = "ok"
    try:
        yield
    except BaseException:
        status = "error"
        raise
    finally:
        if trace is not None:
            trace.spans.append({"name": name, "status": status,
                                "offset_ms": round((started - trace.started) * 1000, 2),
                                "latency_ms": round((time.monotonic() - started) * 1000, 2)})


def record_tool(name, effect, status, started):
    trace = current_trace.get()
    if trace is not None:
        outcome = "error" if status in {"error", "timeout", "unknown", "cancelled"} else "ok" if status == "ok" else "rejected"
        trace.tools.append({"tool_name": name, "effect": effect, "status": status, "outcome": outcome,
                            "success": outcome == "ok", "latency_ms": round((time.monotonic() - started) * 1000, 2)})


def record_usage(response, agent):
    trace = current_trace.get()
    usage = getattr(response, "usage", None)
    if trace is None or usage is None:
        return
    def value(name):
        item = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
        return item if isinstance(item, int) and not isinstance(item, bool) and item >= 0 else None
    trace.usage.append({"agent": agent, **{key: value(key) for key in
                       ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")}})


class BusinessMonitor:
    def __init__(self, path, retention_days=7, max_traces=10000):
        self.path = str(path)
        self.retention_days, self.max_traces = retention_days, max_traces
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS traces(request_id TEXT PRIMARY KEY, org_id TEXT, actor_id TEXT, started_at REAL, payload TEXT)")
            db.execute("CREATE INDEX IF NOT EXISTS trace_scope ON traces(org_id, actor_id, started_at)")
        self.registry = CollectorRegistry()
        self.requests = MetricCounter("flowforge_chat_requests_total", "Completed chat attempts, not resolved customer issues", ["engine", "domain", "status"], registry=self.registry)
        self.latency = Histogram("flowforge_chat_duration_seconds", "One observation per completed chat attempt", ["engine"], registry=self.registry,
                                 buckets=(.01, .05, .1, .5, 1, 3, 5, 10, 30, 60, 90))
        self.stages = Histogram("flowforge_chat_stage_seconds", "Measured chat stage duration", ["stage"], registry=self.registry)
        self.tools = MetricCounter("flowforge_tool_calls_total", "Tool outcomes include business rejection", ["tool", "outcome"], registry=self.registry)
        self.tool_latency = Histogram("flowforge_tool_duration_seconds", "Tool duration", ["tool"], registry=self.registry)
        self.tokens = MetricCounter("flowforge_llm_tokens_total", "Provider-reported tokens only", ["kind"], registry=self.registry)
        self.storage_errors = MetricCounter("flowforge_trace_storage_errors_total", "Trace persistence failures", registry=self.registry)
        self.http_requests = MetricCounter("flowforge_http_requests_total", "HTTP responses by route template", ["route", "status"], registry=self.registry)
        self.http_latency = Histogram("flowforge_http_duration_seconds", "HTTP response construction duration", ["route"], registry=self.registry)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=2)
        try:
            with db:
                yield db
        finally:
            db.close()

    def finish(self, trace):
        payload = trace.payload()
        self.requests.labels(trace.engine, trace.domain, trace.status).inc()
        self.latency.labels(trace.engine).observe(payload["latency_ms"] / 1000)
        for item in trace.spans:
            self.stages.labels(item["name"]).observe(item["latency_ms"] / 1000)
        for item in trace.tools:
            self.tools.labels(item["tool_name"], item["outcome"]).inc()
            self.tool_latency.labels(item["tool_name"]).observe(item["latency_ms"] / 1000)
        for usage in trace.usage:
            for kind, count in usage.items():
                if kind != "agent" and count is not None:
                    self.tokens.labels(kind).inc(count)
        try:
            with self.connect() as db:
                db.execute("INSERT OR REPLACE INTO traces VALUES (?,?,?,?,?)", (trace.request_id, trace.org_id, trace.actor_id, trace.started_at, json.dumps(payload)))
                db.execute("DELETE FROM traces WHERE started_at < ?", (time.time() - self.retention_days * 86400,))
                db.execute("DELETE FROM traces WHERE request_id IN (SELECT request_id FROM traces ORDER BY started_at DESC LIMIT -1 OFFSET ?)", (self.max_traces,))
        except Exception:
            self.storage_errors.inc()
            logger.error("trace_storage_failed request_id=%s", trace.request_id)
        # No prompts, replies, tool arguments, tokens or exception messages in the log.
        logger.info("chat_completed %s", json.dumps({k: payload[k] for k in ("request_id", "engine", "domain", "status", "latency_ms")}))

    def recent(self, actor, limit=20, request_id=None, since=0):
        query = "SELECT payload FROM traces WHERE org_id=? AND actor_id=? AND started_at>=?"
        args = [actor.org_id, actor.user_id, since]
        if request_id:
            query += " AND request_id=?"
            args.append(request_id)
        with self.connect() as db:
            rows = db.execute(query + " ORDER BY started_at DESC LIMIT ?", (*args, limit)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def summary(self, actor):
        rows = self.recent(actor, self.max_traces, since=time.time() - 86400)
        counts = Counter(r["status"] for r in rows)
        latencies = sorted(r["latency_ms"] for r in rows)
        tool_counts = Counter(t["outcome"] for r in rows for t in r["tool_traces"])
        technical = sum(counts[s] for s in ("error", "timeout", "degraded"))
        p95 = latencies[max(0, math.ceil(len(latencies) * .95) - 1)] if rows else None
        alerts = []
        if len(rows) >= 5:
            if technical / len(rows) > .2:
                alerts.append({"code": "chat_error_rate", "severity": "error", "message": "技术失败占比超过 20%"})
            if p95 > 5000:
                alerts.append({"code": "chat_latency", "severity": "warning", "message": "处理耗时 P95 超过 5 秒"})
        return {"scope": "current_org_and_user", "window_hours": 24, "sample_count": len(rows),
                "retention_days": self.retention_days, "capacity": self.max_traces, "statuses": dict(counts),
                "engines": dict(Counter(r["engine"] for r in rows)),
                "technical_error_rate": technical / len(rows) if rows else None,
                "latency_ms": {"p50": latencies[(len(latencies) - 1) // 2] if rows else None, "p95": p95},
                "tool_outcomes": dict(tool_counts), "alerts": alerts, "minimum_alert_samples": 5}

    def metrics(self):
        return generate_latest(self.registry)
