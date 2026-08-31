# EchoMind Domain Context

## Product

EchoMind is an internal question-answering and analysis agent for SaaS
companies. It supports employees who work on customer delivery, customer
success, technical support, and renewal operations.

The system answers internal business questions about customer projects,
service issues, and customer operations. It retrieves evidence, analyzes the
request, and proposes next steps. It does not execute production changes or
replace an organization's permission system.

## Canonical Terms

- **Customer project**: the delivery and adoption context for one SaaS customer.
- **Service issue**: a technical, integration, reliability, or usage problem
  affecting a customer project.
- **Customer operation**: activities related to adoption, account health,
  value realization, and renewal readiness.
- **Delivery**: implementation planning, configuration, migration, rollout,
  and enablement.
- **Support**: technical and integration diagnosis, impact analysis, and
  troubleshooting guidance.
- **Success**: customer health, adoption improvement, and value-oriented
  follow-up recommendations.
- **Renewal**: analysis of renewal readiness, usage signals, risks, and
  recommended interventions.

## Agent Vocabulary

- `TriageAgent` selects the relevant analysis capabilities and synthesizes
  results.
- `DeliveryAgent` handles implementation and rollout analysis.
- `SupportAgent` handles technical, integration, and reliability analysis.
- `SuccessAgent` handles adoption, entitlement, and customer-health analysis.
- `RenewalAgent` handles renewal-readiness analysis.

The user does not need a department-specific identity or permission context to
use these capabilities. Routing is based on the question, its entities, and
the available knowledge.

## Answer Types

- **Fact lookup**: retrieve a documented product, project, or service fact.
- **Issue analysis**: explain likely causes, impact, and risks.
- **Recommendation**: propose delivery, support, success, or renewal actions.
- **Cross-domain synthesis**: combine several specialist analyses into one
  conclusion with evidence and prioritized recommendations.
