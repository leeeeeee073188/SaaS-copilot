# 0002: Expand EchoMind to Tool-Enabled B2B SaaS Service

## Status

Accepted product direction, 2026-09-08. The scoped MVP is implemented;
see [implementation record](../implementation-v2.md) for verified capabilities and remaining extensions.
Supersedes [ADR 0001](./0001-internal-saas-operations-domain.md).

2026-09-11 amendment: the user requested standalone development of the current customer-facing
branch and deletion of unrelated internal-analysis code. The former retention of delivery,
adoption and renewal analysis below is historical. Only product, integration, billing and account
domains remain; there is no alternate internal-analysis runtime. See [cleanup record](../standalone-cleanup.md).

## Decision

EchoMind will support product consultation, API integration support,
subscription and billing management, and organization account management
through registered business tools. Customer organization members are primary
users; SaaS support staff act only within an explicitly assigned organization
scope. Delivery, adoption and renewal analysis remain supported scenarios.

FlowForge Cloud remains the fictional B2B data integration SaaS used to
demonstrate these capabilities. Its proposed sandbox will own persistent
organizations, memberships, subscriptions, invoices, integration records and
operation receipts. Agent answers, RAG documents and conversational memories
are not authoritative records of current business state.

The first implementation will extend the existing FastAPI application with a
small business module and a separate SQLite database. Product UI actions and
Agent tools will call the same domain services. EchoMind retains its current
orchestrator and tool-use implementation; capability registration extends the
existing tool specifications instead of introducing a second Agent framework.

## Consequences

The former decision to omit identity, authorization and action state is no
longer sufficient. A minimal server-resolved organization membership,
operation-level authorization, concrete change previews, applicable user
confirmation, idempotency and execution receipts are required for sandbox
mutations. Tool descriptions and LLM decisions do not grant permissions.

The sandbox changes actual local business records but does not charge money,
send invitations to real recipients, rotate real credentials or operate real
customer infrastructure. These are product-demo boundaries, not a restriction
against implementing business operations. Future real-provider adapters must
reuse the domain contract while adding provider-specific authorization,
reconciliation and operational controls.

We considered a document-only mock and a complete external SaaS stack. The
first cannot validate state-changing tasks; the second adds deployment and
integration work before the core Agent behavior is demonstrated. A small
stateful sandbox makes tool outcomes and evaluation reproducible while
limiting changes to the current project.

Detailed target design: [V2 plan](../b2b-saas-agent-plan-v2-2026-09-08.md) and
[FlowForge sandbox specification](../flowforge-sandbox-spec-2026-09-08.md).
