# 0001: Position SaaS Copilot as an Internal SaaS Operations Analysis Agent

## Status

Superseded by [ADR 0002](./0002-tool-enabled-b2b-saas-service.md) on 2026-09-08.

This document records the original analysis-only scope. The user subsequently
expanded the target product to tool-enabled B2B SaaS service and management.
The historical restrictions below are not the current product direction;
runtime code may still reflect them until the new plan is implemented.

## Decision

SaaS Copilot will be presented as an internal question-answering and analysis
agent for SaaS employees working in customer delivery, customer success,
technical support, and renewal operations. The shared business background is
the fictional data integration product FlowForge Cloud.

The existing RAG, memory, intent recognition, multi-agent orchestration,
monitoring, and evaluation infrastructure remains in place. Business
semantics move from consumer customer service to internal customer-project,
service-issue, and customer-operation questions.

The system may recommend actions and draft analysis, but it does not claim to
execute production changes, customer notifications, refunds, or other
external operations.

## Rationale

This keeps the implementation small while making the project distinct from a
generic consumer support chatbot. The current architecture already supports
domain routing and multi-agent synthesis; changing the business vocabulary and
knowledge examples provides the desired positioning without introducing
identity, RBAC, approval, or task-state infrastructure.

## Rejected Alternatives

- Department-specific permission and context modeling: outside the scope of a
  question-answering and analysis product.
- Full workflow execution and approval state machine: unnecessary for the
  current demo and would expand the implementation surface.
- A generic industry-neutral knowledge base: less coherent for demonstrations
  than a single fictional SaaS product.
