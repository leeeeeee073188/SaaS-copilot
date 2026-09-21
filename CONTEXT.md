# SaaS Copilot and FlowForge Cloud Domain Context

SaaS Copilot is a tool-enabled B2B SaaS service agent for product consultation,
API integration support, subscription and billing management, and enterprise
account management. FlowForge Cloud is the fictional data integration SaaS
whose business capabilities it serves. These definitions describe the target
domain; runtime implementation status is recorded in the project plans.

## Language

### Product and Customers

**SaaS Copilot**: The conversational service that explains product capabilities,
retrieves evidence, and uses registered business tools on a user's behalf.

**FlowForge Cloud**: A fictional API and Webhook integration and operations
product for growing businesses connecting their business systems. Its customer
organization members manage integrations, usage, subscriptions and collaboration.

**Organization**: A customer enterprise that owns its subscriptions, members,
projects and integration resources. It is the customer data isolation scope.
_Avoid_: Using account to mean both an organization and an individual user.

**User**: An individual who may belong to one or more organizations.

**Membership**: A user's role and active access within a specific organization.

**Support assignment**: The explicit scope in which a SaaS support employee may
assist a customer organization; support employment alone does not grant access
to all organizations or authority to change their subscriptions.

**Customer project**: An organization-owned workspace grouping integrations
around a business outcome, such as order fulfillment notifications.

### Product Usage

**Integration**: A project's configured connection to a source or destination,
such as an API or Webhook endpoint.

**Environment**: The testing or production context of an integration and its
credentials; credentials belonging to one environment do not imply access to another.

**Business event**: A business fact exchanged between systems, such as an order
being created. Its identity is distinct from any individual delivery attempt.

**Request record**: An observation of a request's technical outcome within an
integration; a successful request alone does not establish downstream business completion.

**Delivery attempt**: One attempt to deliver a business event to its destination;
multiple attempts may refer to the same event.

**Sync run**: One recorded execution of a configured data synchronization task.

**Service issue**: A technical, reliability or usage problem affecting a
customer project or integration.

**Entitlement**: A capability or limit granted by an organization's effective
subscription or explicit service agreement.

**Usage**: Measured consumption of a named product resource within a defined
period; it is distinct from the entitlement limit.

### Subscription and Enterprise Account Management

**Plan**: A versioned offer specifying capabilities, limits and pricing rules.

**Subscription**: An organization's agreement to a plan for a billing period,
including its effective state and any scheduled change.

**Invoice**: A statement of charges and payment status for an organization and
period. Reading or generating an invoice does not imply payment succeeded.

**Invitation**: A pending offer of membership with a specified organization and
role; it is distinct from an active membership.

**Business operation**: A requested change to a subscription, membership or
other SaaS resource through an authorized business capability.

**Change preview**: A concrete proposed operation with its target, effective
time, expected financial or access impact, and applicable preconditions.

**Operation receipt**: The recorded outcome of a business operation, including
its target, status and resulting resource state.

### Analysis and Knowledge

**Product knowledge**: Versioned documentation describing product behavior,
API contracts and business rules; current customer state is checked separately.

**Delivery**: Implementation planning, configuration, migration, rollout and
enablement for a customer project.

**Support**: Technical diagnosis, integration assistance and issue resolution.

**Success**: Adoption, customer-health and value-realization assistance.

**Renewal**: Assistance with renewal readiness and the organization's intended
subscription continuation or change.
