# Deploy Nexus Backend to Azure Container Apps

Deploy the Nexus FastAPI backend to Azure Container Apps using the Azure CLI.

## Prerequisites checklist

Before starting, confirm:
- Azure CLI is installed and you're authenticated (`az account show`)
- Docker is running
- You have the target resource group name, ACR name, and required secret values

Ask the user for anything missing before proceeding.

## Steps

### 1. Build the Docker image

Run from the repo root:
```powershell
docker build -t nexus-backend -f backend/Dockerfile .
```

### 2. Tag and push to ACR

```powershell
az acr login --name <acr-name>
docker tag nexus-backend <acr-name>.azurecr.io/nexus-backend:latest
docker push <acr-name>.azurecr.io/nexus-backend:latest
```

### 3. Deploy to Container Apps

```powershell
az containerapp up `
  --name nexus-backend `
  --resource-group <rg-name> `
  --image <acr-name>.azurecr.io/nexus-backend:latest `
  --target-port 8000 `
  --ingress external `
  --env-vars `
    APP_ENV=prod `
    DATABASE_URL=sqlite:///./app.db `
    AZURE_OPENAI_ENDPOINT=<endpoint> `
    AZURE_OPENAI_API_KEY=secretref:openai-key `
    AZURE_OPENAI_DEPLOYMENT=gpt-5.4-mini `
    ENTRA_TENANT_ID=<tenant-id> `
    ENTRA_API_CLIENT_ID=<client-id>
```

### 4. Verify

```powershell
az containerapp show --name nexus-backend --resource-group <rg-name> --query properties.configuration.ingress.fqdn -o tsv
```

Hit `/healthz` on the returned FQDN.

## Rules

- Always ask for resource group, ACR name, and secret values — never guess them
- Set `APP_ENV=prod` so dev auth bypass is rejected
- Use `secretref:` for sensitive values like API keys
- Check `backend/.env` for the current dev values as a reference for what prod needs
