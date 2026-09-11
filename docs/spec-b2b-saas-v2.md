# EchoMind B2B SaaS V2 implementation spec

Branch: `feat/b2b-saas-agent-v2`. Product decisions: ADR 0002 and the V2 design.
This local spec is explicitly requested by the user; no remote issue publishing is required.

## Scope and order

Each module must pass its checks before the next module starts. Runtime code stays small;
comments explain non-obvious constraints. Real payments, outbound invitations, production
integrations, framework migration and immediate prorated upgrades remain out of scope.

1. **Business sandbox**: SQLite organizations, memberships, two plans, three scenarios,
   subscription/invoice/usage/request data; server-issued demo sessions; org permissions;
   subscription preview/confirmation/scheduling and developer invitation with transactional
   idempotency, audit and operation receipts. Demo clock advances scheduled changes.
   Check permissions, cross-org IDs, stale previews, repeated writes, seat limits and clock.
2. **Knowledge fixtures**: versioned synthetic product documents and organization snapshots,
   persisted in a dedicated Chroma collection; stable chunk IDs, org/version filtering,
   deterministic re-import and source metadata. Check real DB count and retrieval plus isolation.
   Fixture source is tracked; generated SQLite/Chroma files remain ignored.
3. **Agent integration**: registered business tools with permissions/effects, domain/action
   routing, trusted actor context, no model-supplied confirmation; shared chat service and
   org-scoped memory, bounded collaboration and full-request timing. Repair memory compression
   order/failed archival; tool failures never fabricate success. Check mocked tool-use and API.
   2026-09-11: working memory uses Redis exclusively with 24-hour TTL and optimistic concurrency
   checks; SQLite remains the business/trace store. See `docs/redis-working-memory.md`.
4. **Product UI**: demo session/org selection, four business panels, conversation, citations,
   concrete preview confirmation and state refresh; preserve legacy analysis mode outside demo.
   Check production build and local API/UI flow.
5. **Evaluation and delivery**: repeatable isolated state assertions and trace report; documented
   seed/import/run commands, full regression suite. Live model calls require configured provider;
   deterministic tests must not depend on paid model output. Record exactly which checks ran.

## Business contract

- This branch runs only the synthetic FlowForge workspace (2026-09-11 scope update).
  `ECHOMIND_DEMO_LLM` selects deterministic/model execution; no internal-analysis mode remains.
  Demo identity routes are sandbox authentication, not production identity management.
- Authorization derives from a server session token and org membership, never body user_id/role.
- owner: all operations; admin: integration read/member invite; billing_admin: billing read/change;
  developer: integration read; all members: public knowledge, entitlements/usage/member summary.
- Tools expose read/prepare/write metadata; execution rechecks permissions. Skills grant no rights.
- A subscription preview binds actor/org/parameters/resource version/policy version/expiry.
  Confirm via authenticated operation endpoint; submit uses that exact preview. Current plan
  remains unchanged until period end; receipt says scheduled. Confirmation cannot change arguments.
- Invitation is developer-only, local pending state, reserves one seat. Explicit exact invitation
  requests may execute; otherwise the agent asks for missing recipient/role. No real email is sent.
- State mutation, success receipt and audit commit together; same key+parameters returns original
  receipt; changed parameters conflict. Queries and operations always filter org. Clock controls
  are unavailable to Agent tools and protected by a separate demo control credential.
- Public docs + authorized org docs may be retrieved; current billing/member facts come from tools.
  Memory never grants authorization or supplies authoritative current billing state.

## API additions

`/saas/demo/login`, `/saas/session`, `/saas/plans`, `/saas/me/{entitlements,usage,subscription,invoices,members}`,
`/saas/integrations/{id}/requests`, `/saas/subscription/change-previews`,
`/saas/operations/{id}/confirm`, `/saas/subscription/changes`, `/saas/invitations`,
`/saas/operations/{id}`; separately protected `/saas/demo/advance-clock`.

## Completion evidence

### Specialist execution (2026-09-10)

- Keep primary/supporting routing and parallel read/explain requests. Split work by domain:
  Triage owns product consultation, Support owns integration diagnostics, Success owns billing/account.
- Intersect authorized request tools with each specialist's domain and common read tools.
  Assign mutation requests only to their primary domain. No parallel writes or broader fallback tools.
- Preserve shared conversational memory for references, but filter knowledge evidence by domain;
  each invocation accumulates evidence/receipts separately and merges them back even on failure.
- Parallel specialists return validated summary/evidence_ids/missing/next_steps; unknown sources or
  invalid shapes fail that specialist. Composer addresses SaaS users and preserves incomplete work.
- Verify distinct real tool execution, denied tools, receipt propagation, request isolation,
  partial failure and deterministic composition fallback with an injected fake provider.
  Offline state evaluation is regression evidence, not a measurement of live multi-agent quality.

Module results and deviations are recorded in `docs/implementation-v2.md`. Tests must verify
database state and prohibited side effects, not just success text. Chroma must actually contain
the synthetic documents and return source IDs. No claimed live-model quality scores without a run.
