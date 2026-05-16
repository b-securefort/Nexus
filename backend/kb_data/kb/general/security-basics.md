# Security Basics

Foundational security concepts and controls relevant to any IT team.

## Zero Trust Model

Traditional security drew a hard perimeter ("castle and moat"): trust everything inside the network, block everything outside. Zero Trust replaces this assumption with: **never trust, always verify** — regardless of network location.

**Core principles**:
1. Verify explicitly — always authenticate and authorise based on all available data points (identity, location, device, service, workload, data).
2. Use least privilege access — limit user access with just-in-time and just-enough-access.
3. Assume breach — minimise blast radius and segment access. Encrypt everything. Use analytics to detect threats.

**Practical implications**:
- MFA is mandatory, not optional — even for internal resources.
- Device health is checked before granting access (Conditional Access / device compliance).
- East-west traffic inside the data centre is inspected, not trusted by default.
- Micro-segmentation prevents a compromised host from reaching unrelated services on the same subnet.

## Authentication vs Authorisation

**Authentication (AuthN)**: proving identity ("who are you?"). Factors: something you know (password), something you have (OTP, hardware key), something you are (biometric).

**Authorisation (AuthZ)**: determining what an authenticated identity can do ("what are you allowed to do?"). Implemented via RBAC, ABAC, or ACLs after identity is confirmed.

**MFA (Multi-Factor Authentication)**: requires at least two distinct factors. Eliminates the vast majority of credential-based attacks. TOTP apps (Microsoft Authenticator, Google Authenticator) and FIDO2 hardware keys are preferred. SMS-based OTP is better than password-only but vulnerable to SIM-swapping.

## Identity and Access Management (IAM)

**Principle of least privilege**: grant the minimum access required for a task, nothing more. Review and revoke unused permissions regularly.

**RBAC (Role-Based Access Control)**: permissions are attached to roles, and users are assigned to roles. Simpler to manage than per-user ACLs at scale.

**ABAC (Attribute-Based Access Control)**: access decisions based on attributes of the user, resource, and environment (e.g. "allow access if user.department == data.owner AND time < 18:00"). More flexible, more complex.

**Service accounts and managed identities**: non-human identities used by applications and services. Best practice: use cloud-managed identities (Azure Managed Identity, AWS IAM Role for EC2/ECS) that rotate credentials automatically. Avoid long-lived service account passwords or access keys.

**Privileged Access Management (PAM)**: controls for administrator and root-level access. Key controls:
- Just-in-time (JIT) elevation: admin rights granted for a bounded window, auto-expired.
- Session recording: privileged sessions are recorded for audit.
- Credential vaulting: admin credentials stored in a vault, rotated automatically, retrieved via checkout workflow.

## Encryption

**Encryption at rest**: data stored on disk, in databases, or in object storage is encrypted. Cloud services typically offer transparent encryption by default (AES-256). You control the keys (customer-managed key / CMK) or delegate to the provider (provider-managed key / PMK). CMK gives you the ability to revoke access by deleting the key — use for highly sensitive data.

**Encryption in transit**: data moving over a network is encrypted. Use TLS 1.2+ for all internal and external service communication. Deprecate TLS 1.0/1.1. Enforce HSTS for web services. Use mutual TLS (mTLS) for service-to-service communication in zero-trust environments.

**Key management**:
- Never hardcode secrets in application code or IaC templates.
- Use a key management service (Azure Key Vault, AWS KMS, HashiCorp Vault).
- Rotate keys on a schedule and on suspected compromise.
- Separate encryption keys by environment (dev keys ≠ prod keys).

## Vulnerability Management

**Patching**: software vulnerabilities are regularly discovered and published in CVEs. Unpatched systems are the leading cause of breaches. Targets:
- Critical CVEs (CVSS ≥ 9.0): patch within 24–72 hours.
- High (7.0–8.9): patch within 7 days.
- Medium (4.0–6.9): patch within 30 days.
- Low (<4.0): patch in next planned maintenance window.

**Container and image scanning**: scan container images for known vulnerabilities before deployment (e.g. Trivy, Aqua, Prisma). Block images with critical CVEs from reaching production. Re-scan images in registries; base image updates introduce new vulnerabilities.

**SAST (Static Application Security Testing)**: analyse source code for vulnerabilities without running it. Tools: Semgrep, SonarQube, Bandit (Python), ESLint security plugins. Run in CI pipeline.

**DAST (Dynamic Application Security Testing)**: probe a running application for vulnerabilities (OWASP ZAP, Burp Suite). Run against staging.

**Dependency scanning**: track third-party libraries (npm, pip, NuGet) for known vulnerabilities. Tools: `pip-audit`, `npm audit`, Dependabot. Automate PRs to update vulnerable packages.

## Common Attack Vectors

**Phishing**: fraudulent emails or sites that trick users into revealing credentials. Counter: security awareness training, MFA, email filtering, SPF/DKIM/DMARC.

**SQL Injection**: attacker injects malicious SQL into input fields. Counter: parameterised queries / prepared statements; never string-concatenate user input into SQL.

**XSS (Cross-Site Scripting)**: attacker injects scripts into pages viewed by other users. Counter: output encoding, Content Security Policy (CSP), sanitise user-generated HTML.

**CSRF (Cross-Site Request Forgery)**: attacker tricks a user's browser into making an authenticated request to another site. Counter: CSRF tokens, SameSite cookie attribute, Referer validation.

**Supply chain attacks**: compromise a widely used library or build tool to propagate malicious code to consumers. Counter: pin dependencies to hashes (not just versions), use a private package registry, scan dependencies.

**Insider threats**: malicious or negligent actions by employees or contractors with legitimate access. Counter: least privilege, just-in-time access, audit logs, anomaly detection on access patterns.

## Audit Logging

Record who did what, when, and from where. Logs are essential for:
- Incident investigation (reconstruct the attack timeline).
- Compliance (prove who accessed sensitive data).
- Anomaly detection (unusual access times, locations, volumes).

**Log what matters**:
- Authentication events (success and failure).
- Privilege escalation.
- Changes to IAM policies and security configurations.
- Access to sensitive data (secrets, PII).
- Resource create, update, delete events.

**Log hygiene**:
- Centralise logs in a tamper-resistant store (attackers often try to delete or alter logs).
- Set retention to match compliance requirements (often 1–7 years for security logs).
- Alert on log ingestion gaps — a missing log stream can indicate a compromised agent.

## Incident Response Basics

1. **Identify**: detect the incident (alert, user report, anomaly).
2. **Contain**: limit the blast radius immediately (revoke compromised credentials, isolate affected systems, block attacker IPs).
3. **Eradicate**: remove the root cause (patch vulnerability, remove malware, rotate all affected secrets).
4. **Recover**: restore service from clean backups; verify integrity before bringing systems back online.
5. **Post-incident review**: blameless post-mortem, action items, update runbooks.

See `kb/runbooks/incident-response.md` for the team-specific runbook.
