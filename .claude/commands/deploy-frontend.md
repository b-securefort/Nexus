# Deploy Nexus Frontend to Azure Static Web Apps

Deploy the Nexus React frontend to Azure Static Web Apps using the Azure CLI or SWA CLI.

## Prerequisites checklist

Before starting, confirm:
- Azure CLI is installed and authenticated (`az account show`)
- Node.js and npm are available
- You have the backend API URL (deployed Container App FQDN)
- You have the Entra ID SPA client ID and tenant ID

Ask the user for anything missing before proceeding.

## Steps

### 1. Build the frontend

```powershell
cd frontend
$env:VITE_API_BASE_URL = "https://<backend-fqdn>"
$env:VITE_ENTRA_CLIENT_ID = "<spa-client-id>"
$env:VITE_ENTRA_TENANT_ID = "<tenant-id>"
$env:VITE_DEV_AUTH_BYPASS = "false"
npm run build
```

### 2. Create Azure Static Web App (first time only)

```powershell
az staticwebapp create `
  --name nexus-frontend `
  --resource-group <rg-name> `
  --location <location> `
  --sku Free
```

### 3. Deploy the build output

Option A — Azure CLI:
```powershell
az staticwebapp deploy `
  --name nexus-frontend `
  --resource-group <rg-name> `
  --app-location ./frontend `
  --output-location dist `
  --no-build
```

Option B — SWA CLI:
```powershell
npx @azure/static-web-apps-cli deploy ./frontend/dist `
  --deployment-token <token> `
  --env production
```

### 4. Ensure SPA routing fallback exists

`frontend/public/staticwebapp.config.json` should contain:
```json
{
  "navigationFallback": {
    "rewrite": "/index.html",
    "exclude": ["/assets/*"]
  }
}
```

### 5. Verify

```powershell
az staticwebapp show --name nexus-frontend --resource-group <rg-name> --query defaultHostname -o tsv
```

Open the returned hostname in a browser.

## Rules

- Always ask for resource group, backend URL, and Entra IDs — never guess them
- Set `VITE_DEV_AUTH_BYPASS=false` for production builds
- The SPA redirect URI in the Entra app registration must include the Static Web App hostname
