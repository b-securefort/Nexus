# Deployment Work Items

Based on the project's documentation (`DESIGN.md`, `GLOSSARY.md`, `ToDo.md`, and deployment guides), here are all the items that need to be addressed to deploy Nexus for production use:

## 1. Complete Pending Tool Development
The core functionality of Nexus relies on several tools that are currently marked as pending in `ToDo.md`. These should be completed to provide the full planned experience:
* **Foundation (Priority 0):** Implement `az_login_check` to auto-detect Azure CLI auth state before any Azure tool executes.
* **Core Tools (Priority 1-10):** Implement the remaining tools: `az_cost_query`, `az_monitor_logs`, `az_rest_api`, `generate_file`, `az_devops`, `az_policy_check`, `diagram_gen`, `network_test`, `az_advisor`, and `web_fetch`.
* **Skill Updates:** Once tools are implemented, update the `chat-with-kb` and `architect` skill definitions to grant access to the new capabilities.

## 2. Infrastructure & Security Configuration
The application architecture requires several Azure resources to be provisioned and configured:
* **Azure Entra ID (Auth):** Set up an App Registration to support MSAL authentication. Crucially, configure API permissions for `user_impersonation` so the frontend can pass the `X-ARM-Token` to the backend, allowing Azure tools to run as the signed-in user.
* **Azure App Configuration (RBAC):** Provision an App Configuration store to hold the `Nexus:RoleAccessMap` JSON value. This maps Entra App Roles to specific skills and tool access.
* **Managed Identities:** Assign the `App Configuration Data Reader` role via Managed Identity to the hosting compute environment so the backend can fetch the RBAC mapping securely at startup.
* **Azure OpenAI:** Provision the necessary models: `text-embedding-3-small` (for KB hybrid retrieval) and the target chat model (e.g., `gpt-5.4-mini` or equivalent).

## 3. Storage and State Management
* **Persistent Storage Mounts:** Nexus relies heavily on the local filesystem (`backend/app.db` SQLite database, `kb_data/` directory for knowledge base, and `output/` for generated artifacts). In a containerized PaaS deployment, you must attach persistent storage (e.g., Azure Files SMB shares) to prevent data loss on container restarts.
* **Concurrency Limitations:** The `app.db` SQLite database uses WAL mode, which works great for a single container. However, if you plan to scale the backend to multiple replicas (e.g., `maxReplicas: 3`), SQLite over network file shares can experience locking issues. You may need to restrict the backend to a single replica or evaluate moving to a managed PostgreSQL database if high concurrency is required.

---

# Azure Deployment Recommendations

Here are three recommended approaches for deploying the Nexus frontend and backend in Azure:

### 1. Azure Container Apps (Recommended)
This is the most natural fit for the Nexus backend architecture and is officially recommended in `DEPLOYMENT_RENDER_SIDECAR.md`.
* **Architecture:** Container Apps natively supports running multiple containers in a single app (Pods) that share the same network namespace (`localhost`). 
* **Why it fits:** You can deploy the `nexus-backend` container alongside the stateless `jgraph/drawio-image-export2` container as a sidecar. The backend can communicate with the draw.io exporter seamlessly via `http://localhost:8080`.
* **Storage:** Azure Files can be easily mounted into the Container App to persist the SQLite database and KB data.

### 2. Azure Static Web Apps (Frontend) + Azure Container Apps (Backend)
This approach decouples the frontend and backend for better performance and separation of concerns.
* **Architecture:** The React+Vite frontend is built and deployed to Azure Static Web Apps. The FastAPI backend and the draw.io sidecar are deployed to Azure Container Apps as described above.
* **Why it fits:** Azure Static Web Apps provides global edge caching (CDN) for static assets, free SSL certificates, and streamlined CI/CD integrations for frontend frameworks. The backend container app can focus purely on compute-heavy orchestrator loops and tool execution.

### 3. Azure App Service for Linux (Multi-Container)
If your organization heavily standardizes on App Service and hasn't adopted Container Apps, this is a viable alternative.
* **Architecture:** Use the "Docker Compose (Preview)" deployment feature of Azure App Service for Linux. You can deploy a modified version of the existing `docker-compose.yml` to spin up both the backend and draw.io exporter containers.
* **Why it fits:** It requires minimal configuration changes from the local development setup.
* **Caveat:** Sidecar networking relies on Docker Compose DNS (e.g., `DRAWIO_EXPORT_URL=http://drawio-export:8080`) rather than `localhost`. Note that the multi-container feature on App Service is an older preview feature, which makes Container Apps the safer long-term choice.
