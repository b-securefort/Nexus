# Cloud Fundamentals

Core concepts that apply across cloud providers (Azure, AWS, GCP) and hybrid environments.

## Shared Responsibility Model

Cloud providers and customers share security and operational responsibilities. The split depends on the service model.

| Responsibility | On-prem | IaaS | PaaS | SaaS |
|---|---|---|---|---|
| Physical hardware | You | Provider | Provider | Provider |
| Hypervisor/OS | You | You | Provider | Provider |
| Runtime/middleware | You | You | Provider | Provider |
| Application code | You | You | You | Provider |
| Data | You | You | You | You |
| Access/identity | You | You | You | You |

**Key takeaway**: even in SaaS, you own your data and who can access it. The cloud provider owns the infrastructure below the application.

## Regions and Availability Zones

**Region**: a geographic area containing one or more data centres. Examples: East US, West Europe, Southeast Asia. Regions are independent failure domains for most catastrophic events (power grid, natural disaster).

**Availability Zone (AZ)**: one or more physically separate data centres within a region, with independent power, cooling, and networking. Connected by low-latency links. AZ failures are isolated — a fire in AZ-1 does not affect AZ-2.

**Zone-redundant vs zone-specific deployment**:
- Zone-redundant (recommended for production): the service spans multiple AZs automatically. No manual placement.
- Zone-specific (pinned): resource lives in a specific AZ. Needed for proximity to other zone-pinned resources, or for cost (egress is cheaper within an AZ).

**Region pairs**: some providers pair regions for disaster recovery. Certain services (e.g. Azure geo-redundant storage) replicate asynchronously to the paired region.

**Latency guidance**: users in Southeast Asia routing to East US see ~180 ms RTT. For latency-sensitive workloads, deploy to the region closest to users or use a CDN/edge layer.

## Service Models

**IaaS (Infrastructure as a Service)**: raw compute, storage, networking. You manage OS, middleware, runtime. Examples: VMs, VNets, managed disks.

**PaaS (Platform as a Service)**: managed runtime. You deploy code or containers; the platform manages patching, scaling, HA. Examples: Azure App Service, AWS Lambda, Azure SQL.

**SaaS (Software as a Service)**: fully managed application. You configure and use it. Examples: Microsoft 365, Salesforce, GitHub.

**Serverless / FaaS**: compute scales to zero. You pay per invocation and duration. No server management. Examples: Azure Functions, AWS Lambda. Best for event-driven, bursty, short-lived workloads.

**CaaS (Containers as a Service)**: managed Kubernetes. You deploy container images; the platform manages the control plane. Examples: AKS, EKS, GKE.

## Scalability Patterns

**Vertical scaling (scale up)**: increase the resources of a single instance (more CPU/RAM). Simple but has an upper bound and typically requires downtime or restart.

**Horizontal scaling (scale out)**: add more instances behind a load balancer. No upper bound; enables zero-downtime scaling. Requires stateless application design (session state in a shared store, not in memory).

**Autoscaling**: automatically add or remove instances based on a metric (CPU, memory, queue depth, HTTP latency). Configure a minimum (always-on floor) and maximum (cost/capacity ceiling).

**Scale-to-zero**: instances drop to zero when idle. Cold start latency is the trade-off. Acceptable for batch processing; risky for interactive APIs.

## High Availability and Disaster Recovery

**RTO (Recovery Time Objective)**: maximum acceptable downtime after a failure before the business is significantly impacted.

**RPO (Recovery Point Objective)**: maximum acceptable data loss, expressed as time (e.g. RPO of 1 hour means you can tolerate losing the last hour of transactions).

**HA patterns**:
- Active-active: all instances serve traffic. No failover delay; higher cost.
- Active-passive: one instance active, one hot standby. Failover requires DNS switch or load balancer update.

**DR tiers**:
- Backup & restore: lowest cost, highest RTO/RPO (hours to days).
- Pilot light: minimal "skeleton" infrastructure always running; full provisioning on fail-over (minutes to hours).
- Warm standby: scaled-down but running copy; scale up on failover (minutes).
- Multi-site active-active: full capacity in multiple sites simultaneously (seconds or less).

## Networking in the Cloud

**VNet / VPC**: isolated virtual network. Subnets segment the VNet. Resources within a VNet can communicate by default; cross-VNet requires peering or VPN.

**Peering**: connects two VNets. Traffic stays on the provider backbone (low latency, no encryption overhead). Non-transitive — if A peers B and B peers C, A cannot reach C through B without explicit A-C peering or a transit hub.

**Private Endpoints / PrivateLink**: attaches a PaaS resource (storage, database, Key Vault) to a private IP inside your VNet. Traffic never leaves the provider backbone. Prevents data exfiltration paths via service endpoints.

**Egress and ingress costs**: most providers charge for data leaving a region (egress). Intra-region, cross-AZ traffic is often cheaper but not always free. Factor egress costs into service placement decisions, especially for high-throughput workloads.

## Cost Management

**Reserved Instances / Savings Plans**: commit to a resource type for 1 or 3 years for a 30-70% discount over pay-as-you-go. Best for predictable baseline load.

**Spot / Preemptible VMs**: unused capacity sold at 60-90% discount. Can be reclaimed with 2-minute warning. Only suitable for fault-tolerant batch workloads.

**Right-sizing**: cloud VMs are frequently over-provisioned. Monitor actual CPU/memory utilisation and downsize when average CPU < 20% over 30 days.

**Tagging**: apply consistent tags (`environment`, `team`, `cost-centre`, `workload`) so cost allocation reports show which team is spending what. See the team tagging policy in `kb/platform/`.

**Dev/test environments**: shut down or scale-to-zero outside business hours. A dev VM running 24/7 instead of 9–5 costs 3× more than necessary.

## Identity and Access in the Cloud

**IAM (Identity and Access Management)**: controls who can do what to which resources.

**Key principles**:
- Least privilege: grant only the permissions required for the task.
- No standing admin: use just-in-time (JIT) privileged access; elevate for the task, then drop.
- Service identities: use managed identities (no secrets) rather than service principals with passwords where possible.

**Role types**: built-in roles cover most use cases (Reader, Contributor, Owner). Custom roles for fine-grained permissions (e.g. "can list and read secrets but not create").

**Scope**: permissions can be applied at management group, subscription, resource group, or individual resource level. Grant at the narrowest scope that meets the requirement.

## Multi-Cloud Considerations

Running workloads across multiple cloud providers increases resilience against provider outages but significantly increases operational complexity:

- Different IAM models, networking primitives, observability stacks.
- Data egress costs for cross-cloud replication.
- Skill split across teams.

**When multi-cloud makes sense**: regulatory requirement to avoid vendor lock-in, best-of-breed service selection (e.g. Azure AI + AWS S3), acquired company running on a different cloud.

**When it doesn't**: using multi-cloud as a hedge against provider failure without a tested failover runbook gives an illusion of resilience.

Our current platform is primarily Azure. See `kb/platform/azure-overview.md` for Azure-specific platform standards.
