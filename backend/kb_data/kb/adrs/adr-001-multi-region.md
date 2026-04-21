# ADR 001: Multi-Region Active-Active Architecture

## Status

Accepted

## Context

Our application needs high availability across Azure regions to meet the 99.99% SLA target. We evaluated active-passive vs active-active approaches.

## Decision

We will deploy in active-active mode across **East US 2** and **West Europe** regions using Azure Front Door for traffic distribution.

### Key design choices:
- **Azure Front Door** for global load balancing with health probes
- **Azure Cosmos DB** with multi-region writes for the data layer
- **Azure Service Bus** with geo-disaster recovery pairing
- **Azure Key Vault** per region with synchronized secrets via CI/CD

## Consequences

- Higher infrastructure cost (~40% increase)
- Increased operational complexity
- Requires careful conflict resolution strategy for multi-region writes
- Near-zero RPO and <1 minute RTO
