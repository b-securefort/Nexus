# Azure Platform Overview

## Core Services We Use

- **Azure Container Apps** — Primary hosting for microservices
- **Azure Front Door** — Global load balancer and CDN
- **Azure Cosmos DB** — Globally distributed NoSQL database
- **Azure Service Bus** — Message broker for async communication
- **Azure Key Vault** — Secrets management
- **Azure Monitor** — Logging, metrics, and alerting
- **Azure DevOps** — CI/CD pipelines and Git repos

## Naming Conventions

```
{team}-{env}-{service}-{region}
Example: arch-prod-api-eus2
```

## Resource Groups

Each environment has its own resource group:
- `team-architect-dev-rg`
- `team-architect-staging-rg`
- `team-architect-prod-rg`

## Tags

All resources must have:
- `environment`: dev/staging/prod
- `team`: team-architect
- `cost-center`: CC-1234
