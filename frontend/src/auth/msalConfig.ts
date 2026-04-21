import type { Configuration } from "@azure/msal-browser";
import { LogLevel } from "@azure/msal-browser";

const tenantId = import.meta.env.VITE_ENTRA_TENANT_ID || "placeholder";
const clientId = import.meta.env.VITE_ENTRA_CLIENT_ID || "placeholder";
const apiScope = import.meta.env.VITE_ENTRA_API_SCOPE || "api://placeholder/user_impersonation";

export const msalConfig: Configuration = {
  auth: {
    clientId,
    authority: `https://login.microsoftonline.com/${tenantId}`,
    redirectUri: window.location.origin,
  },
  cache: {
    cacheLocation: "sessionStorage",
    storeAuthStateInCookie: false,
  },
  system: {
    loggerOptions: {
      logLevel: LogLevel.Warning,
    },
  },
};

export const loginRequest = {
  scopes: [apiScope],
};
