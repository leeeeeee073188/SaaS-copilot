# Domain Docs

Engineering skills must read the root `CONTEXT.md` before exploring the
codebase and read relevant decisions under `docs/adr/`.

## Layout

This is a single-context repository:

```text
/
├── CONTEXT.md
└── docs/adr/
```

## Vocabulary

Use the canonical terms defined in `CONTEXT.md` in issue titles,
specifications, tests and code. If a required concept is missing, record the
gap through the domain-modeling workflow.

## ADR conflicts

If proposed work contradicts an existing ADR, surface the conflict explicitly
instead of silently overriding the decision.
