# Deploying Nexus with the draw.io render sidecar

The `render_drawio` tool needs draw.io to be available to the backend. For
production deployments this is satisfied by the
[`jgraph/drawio-image-export2`](https://github.com/jgraph/drawio-image-export2)
container running as a sidecar. The backend reaches it over HTTP at the URL
configured by `DRAWIO_EXPORT_URL`.

If `DRAWIO_EXPORT_URL` is empty, `render_drawio` falls back to the locally
installed draw.io desktop CLI - useful for development on Windows but not
available in PaaS containers.

---

## Local development

```bash
docker compose up --build
```

That brings up two containers:

| Service | Image | Address |
|---|---|---|
| `nexus-backend` | local build of `backend/Dockerfile` | http://localhost:8002 |
| `drawio-export` | `jgraph/drawio-image-export2:latest` | internal-only, port 8080 |

The backend uses `DRAWIO_EXPORT_URL=http://drawio-export:8080` (set in the
compose file) so the agent can call `render_drawio` and get a real PNG back.
Container DNS handles the hostname; no networking configuration needed.

Without docker, the local CLI fallback still works as long as draw.io desktop
is installed on the dev machine.

---

## Azure Container Apps (recommended production target)

Container Apps supports running multiple containers in a single app as
**sidecars** sharing localhost. This is the closest analogue to the
docker-compose layout above and is the simplest deployment.

### One-time setup

```bash
RG=nexus-rg
ENV=nexus-env
APP=nexus
LOCATION=eastus
ACR=nexusregistry  # your ACR name; replace if you use a different one

az group create -n $RG -l $LOCATION
az containerapp env create -n $ENV -g $RG -l $LOCATION
```

Push your backend image to ACR (or any registry the Container App env can pull
from):

```bash
az acr build -t nexus-backend:latest -r $ACR backend/
# drawio-image-export2 is on Docker Hub - no rebuild needed; just reference it
```

### Deploy with two containers in one app

Create `containerapp.yaml`:

```yaml
properties:
  managedEnvironmentId: /subscriptions/<sub>/resourceGroups/nexus-rg/providers/Microsoft.App/managedEnvironments/nexus-env
  configuration:
    ingress:
      external: true
      targetPort: 8000
      transport: http
    secrets:
      - name: azure-openai-key
        value: "<your-key>"
    registries:
      - server: nexusregistry.azurecr.io
        identity: system  # use managed identity to pull
  template:
    containers:
      - name: nexus-backend
        image: nexusregistry.azurecr.io/nexus-backend:latest
        resources:
          cpu: 1.0
          memory: 2.0Gi
        env:
          - name: APP_ENV
            value: prod
          - name: AZURE_OPENAI_ENDPOINT
            value: "https://<your-aoai>.openai.azure.com/"
          - name: AZURE_OPENAI_API_KEY
            secretRef: azure-openai-key
          - name: AZURE_OPENAI_DEPLOYMENT
            value: "gpt-5.4-mini"
          # Both containers share localhost in the same app
          - name: DRAWIO_EXPORT_URL
            value: "http://localhost:8080"
          - name: TOOL_RENDER_DRAWIO_ENABLED
            value: "true"
      - name: drawio-export
        image: jgraph/drawio-image-export2:latest
        resources:
          cpu: 0.5
          memory: 1.0Gi
        # Sidecar is internal-only; no ingress, no env needed
    scale:
      minReplicas: 1
      maxReplicas: 3
```

Deploy:

```bash
az containerapp create -n $APP -g $RG --yaml containerapp.yaml
```

After deployment, the FQDN is in the create output. Set frontend
`VITE_API_BASE_URL` to that URL.

### Why this works

Sidecar containers in a single Container App share the network namespace, so
the backend reaches the export service at `http://localhost:8080` - no service
discovery, no Ingress rules, no DNS configuration. Both containers scale
together as one logical app.

### Sizing

- `drawio-image-export2` is Chromium-based. 0.5 CPU / 1 GiB RAM handles single-
  user concurrency comfortably; scale up if you see export timeouts under load.
- The export service is stateless. `maxReplicas: 3` is fine for ~30 concurrent
  diagram generations.

---

## Azure App Service for Linux (multi-container alternative)

Less recommended than Container Apps - the App Service multi-container path is
based on docker-compose v3.3 and is being deprecated. If you have a strong
reason to use App Service:

1. Create an App Service plan (Linux).
2. Configure the app for "Docker Compose (Preview)" deployment.
3. Upload the `docker-compose.yml` from the repo as the deployment configuration.
4. Set the app settings: `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`,
   `DRAWIO_EXPORT_URL=http://drawio-export:8080`, etc.

Container Apps is the cleaner Azure target for this workload.

---

## Verification after deploy

After deploy, run a quick check from the backend container's perspective:

```bash
# From a Container Apps revision shell, or by triggering a render via the UI:
curl -X POST $DRAWIO_EXPORT_URL/ \
  -d "xml=<mxfile><diagram><mxGraphModel><root><mxCell id=\"0\"/><mxCell id=\"1\" parent=\"0\"/><mxCell id=\"2\" value=\"test\" style=\"shape=image;image=img/lib/azure2/general/Globe.svg;\" vertex=\"1\" parent=\"1\"><mxGeometry x=\"10\" y=\"10\" width=\"48\" height=\"48\" as=\"geometry\"/></mxCell></root></mxGraphModel></diagram></mxfile>" \
  -d "format=png" \
  --output /tmp/test.png

ls -la /tmp/test.png  # expect a PNG of a few KB
```

Or simpler: ask the agent in the UI to generate any diagram and call `render_drawio`. A
successful render reports `via sidecar` in the result; a fallback to local CLI
on a Linux container will fail (no draw.io desktop installed there) and tells
you the sidecar URL is misconfigured.

---

## When NOT to use the sidecar

- **Pure dev on Windows**: keep using the local draw.io CLI; leave
  `DRAWIO_EXPORT_URL` empty.
- **Air-gapped environments where you can't pull jgraph/drawio-image-export2**:
  set `TOOL_RENDER_DRAWIO_ENABLED=false`. The agent skips rendering, validator
  + hints still catch most issues, and users open `.drawio` files directly in
  desktop draw.io or `app.diagrams.net`.
