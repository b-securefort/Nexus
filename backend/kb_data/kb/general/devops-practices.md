# DevOps Practices

Core DevOps concepts and practices adopted across our teams.

## What is DevOps

DevOps is the convergence of Development and Operations — a culture, set of practices, and toolchain that shortens the software delivery cycle, improves deployment frequency, and makes recovery faster.

The DORA metrics define delivery performance:

| Metric | Elite | High | Medium | Low |
|---|---|---|---|---|
| Deployment frequency | Multiple/day | Weekly | Monthly | 6+ months |
| Lead time for change | <1 hour | 1 day–1 week | 1–6 months | 6+ months |
| Change failure rate | <5% | 5–10% | 10–15% | 15–45% |
| MTTR | <1 hour | <1 day | 1–7 days | 6+ months |

## CI/CD

**Continuous Integration (CI)**: every code push triggers an automated pipeline that builds and tests the software. The goal is fast feedback on breakage. A CI pipeline typically runs in under 10 minutes.

**Continuous Delivery (CD)**: the software is always in a releasable state. Deployment to production is a one-click or triggered action.

**Continuous Deployment**: every commit that passes CI is automatically deployed to production with no human gate.

**Typical pipeline stages**:
1. Lint and static analysis
2. Unit tests
3. Build artifact (Docker image, binary, package)
4. Integration tests (against ephemeral or staging environment)
5. Security scan (SAST, dependency vulnerability check)
6. Deploy to staging
7. Smoke / E2E tests
8. Approval gate (for CD; skipped for continuous deployment)
9. Deploy to production
10. Post-deploy health check

**Key principle**: fail fast. Move tests that catch the most bugs earliest in the pipeline.

## Infrastructure as Code (IaC)

IaC manages infrastructure (servers, networks, databases, DNS) through machine-readable configuration files rather than manual processes.

**Benefits**: repeatability, version control, code review, rollback, drift detection.

**Common tools**:

| Tool | Model | Best for |
|---|---|---|
| Terraform / OpenTofu | Declarative, provider-agnostic | Multi-cloud, greenfield |
| Bicep | Declarative, Azure-native | Azure-only, ARM replacement |
| Pulumi | Imperative (real code) | Complex logic in infra |
| Ansible | Procedural, agentless | Configuration management, patching |
| Helm | Templated manifests | Kubernetes application packaging |

**Drift detection**: periodically compare live infrastructure to your IaC state. Drift means someone made a manual change. CI pipelines can run `terraform plan` on a schedule and alert on non-zero diffs.

## GitOps

GitOps extends IaC: Git is the single source of truth for both application code and infrastructure config. A reconciliation agent (ArgoCD, Flux) continuously compares the desired state in Git to the live cluster and converges any differences.

**Pull vs push model**:
- Push (traditional CD): pipeline has credentials and pushes changes to the target.
- Pull (GitOps): the target pulls from Git. No outbound credentials in the pipeline; the cluster only needs access to the Git repo.

GitOps is especially common for Kubernetes workloads.

## Environment Strategy

A typical environment ladder:

```
dev → test → staging → production
```

- **dev**: individual developer sandboxes or shared dev namespace. Short-lived. Break things freely.
- **test**: integrated environment for QA and automated testing. Refreshed frequently.
- **staging** (pre-prod): mirror of production infrastructure. Used for final validation, load testing, and release sign-off.
- **production**: live traffic. Changes arrive here only via the CD pipeline.

**Environment parity**: staging should be as close to production as possible (same SKUs, same secrets injection method, same network topology). The more staging diverges, the more "works in staging, broken in prod" surprises you get.

## Branching Strategy

**Trunk-based development**: teams commit directly to `main` (or very short-lived feature branches). Feature flags hide incomplete work in production. Avoids long-lived merge conflicts. Preferred for high-frequency delivery.

**GitFlow**: explicit `develop`, `release/*`, `hotfix/*` branches. More structure, more merge overhead. Common in teams with less-frequent, versioned releases.

**Our standard**: trunk-based for services; versioned tags for libraries and infrastructure modules.

## Secrets Management

Never commit secrets to Git. Use a secrets manager:

| Platform | Tool |
|---|---|
| Azure | Key Vault (reference in App Settings, CSI driver in AKS) |
| AWS | Secrets Manager / Parameter Store |
| Agnostic | HashiCorp Vault |
| CI/CD | Pipeline secret variables (Azure DevOps Library, GitHub Actions secrets) |

**Principle**: inject secrets at runtime, not build time. Build artifacts should be promotable across environments without rebuilding.

## Observability

Observability = understanding a system's internal state from its external outputs.

Three pillars:

1. **Logs** — event records. Use structured JSON. Include trace IDs for correlation. Ship to a centralised log store.
2. **Metrics** — numeric time-series. Counters (requests_total), gauges (queue_depth), histograms (response_duration_seconds). Expose via Prometheus or OpenTelemetry.
3. **Traces** — end-to-end request flows across services. Distributed tracing (Jaeger, Zipkin, Application Insights) correlates spans from entry to each downstream call.

**Alerting**: alert on symptoms, not causes. "5xx error rate > 1% for 5 minutes" (symptom) is more actionable than "CPU > 80%" (cause). Tune alert thresholds on real data to reduce alert fatigue.

## On-Call and Incident Management

**Runbooks**: documented step-by-step guides for known failure scenarios. Runbooks should be actionable — not just "check logs" but "run `kubectl logs -n <ns> -l app=<svc>` and look for ...". See `kb/runbooks/incident-response.md` for the team incident response runbook.

**Post-mortems** (blameless): after every significant incident, write a post-mortem that covers timeline, root cause, contributing factors, and action items. Focus on system improvement, not blame.

**SLI / SLO / SLA**:
- SLI (Service Level Indicator): the metric you measure (e.g. request success rate).
- SLO (Service Level Objective): the target (e.g. 99.9% success rate over 30 days).
- SLA (Service Level Agreement): the contractual commitment (often stricter language around SLOs).

Error budgets: if your SLO is 99.9%, you have a 0.1% error budget (43.8 min/month). Spend it deliberately (deploy risky changes when budget is healthy; freeze when it's nearly gone).
