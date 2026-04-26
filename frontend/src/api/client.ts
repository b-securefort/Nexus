/**
 * API client with auth token attachment.
 * In dev with DEV_AUTH_BYPASS, sends a dummy token.
 */

import { msalInstance } from "../auth/AuthProvider";
import { loginRequest } from "../auth/msalConfig";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

async function getToken(): Promise<string> {
  // In dev mode with bypass, just send a dummy token
  if (import.meta.env.VITE_DEV_AUTH_BYPASS === "true") {
    return "dev-bypass-token";
  }

  try {
    const accounts = msalInstance.getAllAccounts();
    if (accounts.length === 0) {
      await msalInstance.loginRedirect(loginRequest);
      return "";
    }

    const response = await msalInstance.acquireTokenSilent({
      ...loginRequest,
      account: accounts[0],
    });
    return response.accessToken;
  } catch {
    await msalInstance.acquireTokenRedirect(loginRequest);
    return "";
  }
}

export async function apiFetch(
  path: string,
  options: RequestInit = {}
): Promise<Response> {
  const token = await getToken();
  const headers = new Headers(options.headers);
  headers.set("Authorization", `Bearer ${token}`);
  headers.set("Content-Type", "application/json");

  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
  });

  if (response.status === 401) {
    // Trigger re-auth
    if (import.meta.env.VITE_DEV_AUTH_BYPASS !== "true") {
      await msalInstance.acquireTokenRedirect(loginRequest);
    }
  }

  return response;
}

/**
 * Fetch with auth but without Content-Type header (for multipart/form-data).
 * The browser sets the Content-Type with the correct boundary automatically.
 */
export async function apiFetchMultipart(
  path: string,
  options: RequestInit = {}
): Promise<Response> {
  const token = await getToken();
  const headers = new Headers(options.headers);
  headers.set("Authorization", `Bearer ${token}`);
  // Do NOT set Content-Type — browser handles multipart boundary

  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
  });

  if (response.status === 401) {
    if (import.meta.env.VITE_DEV_AUTH_BYPASS !== "true") {
      await msalInstance.acquireTokenRedirect(loginRequest);
    }
  }

  return response;
}

export function apiStreamUrl(path: string): string {
  return `${API_BASE}${path}`;
}

export { API_BASE };
