# V2 implementation record

Branch: `feat/b2b-saas-agent-v2`. Scope: `docs/spec-b2b-saas-v2.md`.

2026-09-11: this branch now runs only the customer-facing workspace. Historical module records
below describe earlier deliveries. Current startup and cleanup scope: [README](../README.md),
[standalone cleanup](standalone-cleanup.md). `ECHOMIND_DEMO` no longer selects a runtime.

Completed and verified on Windows / Python 3.12 on 2026-09-09 (Asia/Shanghai):
34 regression tests passed; 60/60 state-evaluation cases passed; Vite production build passed;
browser business flows passed; 34 Chroma knowledge chunks persisted and re-import verified.
Final evaluation base revision: `2789618`; report: [eval-v2-report.json](./eval-v2-report.json).
The five modules were implemented in order, each verified before proceeding.

## Module 1 — business sandbox

Implemented SQLite-backed organizations, sessions, memberships, plans, invoices, usage,
integration records, subscription previews/confirmation/scheduling, invitations, operation
receipts and audit. Six deterministic service tests pass, including concurrency and isolation.

To keep the prototype compact, organization-owned business data is a JSON aggregate in SQLite;
sessions, operations and audit have separate tables and transactional constraints. This differs
from the fully normalized exploratory design. A short `BEGIN IMMEDIATE` transaction covers seat
checks and mutation, and database uniqueness covers operation IDs and idempotency keys.
Do not claim production-scale concurrency or real payment/email integration.

## Module 2 — knowledge fixtures

Tracked fixtures generate 30 current public documents, 3 org-scoped historical snapshots and
one obsolete negative example. Dedicated Chroma collection uses explicitly labeled deterministic
character n-gram vectors for offline tests, not a trained semantic embedding benchmark. Existing
production knowledge collections and their embedding models are not migrated or overwritten.

Actual persistent data: `data/flowforge/chroma`, collection `flowforge_v1_lexical`,
34 chunks. Seed re-import and database reopen/retrieval have passed. The service API,
permission checks and actual Chroma test passed before Agent integration began.

Windows dependency note: pinned Chroma 0.5.23 requires a native hnswlib build without
a stable CPython 3.12 Windows wheel. Windows now selects Chroma 1.0.20; Linux/Docker
retains 0.5.23. Use the dedicated demo data path and do not point the newer local
client at the old production database/server. The same fixture integration test
is intended to verify both installations; this session verifies Windows only.

## Module 3 — registered tools and shared chat

Added domain/action routing, permission/effect metadata, scoped tool adapters and one ChatService
used by HTTP and evaluation. Existing agents remain in place; business requests reuse their
tool-use loop with bounded routing, per-agent request serialization and request deadlines.
Writes cannot use fallback agents on failure. Model tools cannot confirm operations or advance time.
Mock-provider tool-use, authenticated chat/state refresh and org history isolation tests passed
before UI implementation. Original compression now preserves history on failed summary/archive
and trims without reversing recent messages.

Demo memory stores scoped working history in SQLite (optional Redis), old conversation excerpts
and explicit preferences in Chroma. Archival order, failed archival and cross-org isolation are
tested with a real Chroma database. Excerpts are bounded, not LLM-generated summaries.

## Module 4 — usable SaaS product

Vue adds an isolated workspace with demo identity selection, entitlement/usage/plan panel,
integration requests, subscription/invoice panel, member invitations, chat citations and operation
cards. UI and agents share SaaSService. Production frontend build passed. Browser checks verified:
chat preview → explicit confirmation → scheduled Growth with current Starter unchanged;
pending invitation visible after refresh; developer cannot view private billing data.
Repeated confirmation after a lost submit response returns the original receipt.

## Module 5 — repeatable evaluation

`evaluation/business_evaluator.py` generates 5 task templates × 3 organizations × 4 roles.
Each case gets a fresh SQLite database; cases use the same ChatService, tool handlers and real
Chroma retrieval as HTTP. It checks authoritative facts, evidence scope/version, no premature
subscription writes, period transition/invoice amount, invitation deduplication, permission/seat
rejection, prohibited mutations and audit cardinality. This is 60 **parameterized integration
cases**, not 60 independently authored language tasks. The JSON report records individual traces,
sources, base commit, engine and measured chat latency (excluding seed and simulated user actions).
The default run passed 60/60. It measures deterministic software behavior, not model success rate.

## Run locally (PowerShell, repository root)

```powershell
py -3.12 -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
$env:PYTHONUTF8='1'
.venv/Scripts/python -m saas.seed --output data/flowforge
$env:ECHOMIND_DEMO='1'
$env:ECHOMIND_DEMO_LLM='0'
.venv/Scripts/python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

In a second terminal:

```powershell
cd frontend
npm ci
npm run dev -- --host 127.0.0.1
```

Open `http://127.0.0.1:5173`. Aurora initially has one free seat and an authentication mismatch;
Beacon has a full Growth plan and rate limiting; Cedar has exhausted Starter quota and seats.
All users, prices, invoices and API records are synthetic. Login is intentionally a local demo
identity selector, never suitable as production authentication. No real charge/email occurs.
UI verification has already scheduled Aurora's upgrade and created one pending invitation in the
current local database. Seed preserves these states. To start pristine, seed another directory
and set `ECHOMIND_DEMO_DATA` to that directory; no deletion is necessary.

The mock product stores transactional facts in `flowforge.db`; versioned knowledge is in Chroma.
Historical Chroma snapshots do not automatically mirror subscription changes: tools provide
current business state. `documents.json` and `seed-report.json` make the seed inspectable.

```powershell
.venv/Scripts/python -m unittest discover -s tests -q
.venv/Scripts/python -m evaluation.business_evaluator --output data/eval/business-report.json
```

For model integration, configure `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL` if needed, and an
available `ANTHROPIC_MODEL`; set `ECHOMIND_DEMO_LLM=1` and restart. The evaluator's explicit
`--engine llm` option uses that provider and may incur charges. No real provider was called in
this delivery. `ECHOMIND_DEMO_EMBEDDING=minilm` uses a separate semantic collection and may
download a model; it has not been benchmarked here. Optional `ECHOMIND_DEMO_REDIS_URL` has not
been exercised against a running Redis server in this Windows session.

## Scope and interview claims

The larger research plan remains a roadmap. This implementation prioritizes org isolation,
tool authorization, transactional state changes, explicit confirmation, scoped memory and an
executable evaluation loop without migrating the existing Agent framework. Routing currently
uses conservative rules; no trained classifier/calibrated confidence is claimed. Demo retrieval
uses character n-gram vectors; no hybrid retrieval/reranker improvement is claimed. Multi-agent
coordination reuses the existing agents and is bounded; no distributed workflow recovery is claimed.
Invitation acceptance/revocation, real webhooks/payments, normalized production storage and a
held-out adversarial/model-quality benchmark are follow-up work outside this MVP spec.

A defensible resume statement: implemented a tool-enabled B2B SaaS service agent and stateful
sandbox covering four domains, with organization-scoped retrieval/memory, confirmed subscription
changes, idempotent writes and a 60-case state regression matrix. Add model quality, cost and
latency improvements only after a measured baseline and comparison run.

## Specialist execution update — 2026-09-10

Implemented domain tasks, tool/evidence narrowing, independent invocation state, validated
parallel findings and customer-facing composition. Partial failures remain visible; V2 no longer
falls back to a different business role. See [specialization-v2.md](specialization-v2.md).

Validation: 47 tests passed, including 7 specialization tests with an injected provider and real
sandbox read tools. The offline state evaluator passed 60/60 cases. Logs are in
`data/flowforge/specialization-tests.log` and `data/eval/specialization-business-report.json`.
The report records the base Git revision; changes were uncommitted during this run. No live-model
quality improvement is claimed. Offline demo still bypasses multi-agent execution.
