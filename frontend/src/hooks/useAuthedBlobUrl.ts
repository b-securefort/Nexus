import { useEffect, useState } from "react";
import { apiFetch, API_BASE } from "../api/client";

export interface AuthedBlob {
  /** A usable `<img src>` / `<a href>` value: a `blob:` object URL for
   *  backend-served files, or the original URL for blob:/CDN inputs. Null until
   *  loaded, or on error. */
  src: string | null;
  loading: boolean;
  error: boolean;
}

/**
 * Resolve a file URL into something the browser can render WITHOUT relying on a
 * header-less `<img src>` GET — which 401s in MSAL mode because the browser
 * can't attach the bearer token (B7). Backend-served paths (`/api/...`) are
 * fetched through `apiFetch` (which always sends `Authorization: Bearer` — a
 * dummy token in dev-bypass, a real Entra token in prod) and exposed as a
 * `blob:` object URL that needs no auth to display or download. `blob:` inputs
 * (optimistic local previews) and allowed absolute CDN URLs pass through
 * untouched.
 */
export function useAuthedBlobUrl(rawUrl: string | null): AuthedBlob {
  const [src, setSrc] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (!rawUrl) {
      setSrc(null);
      setError(false);
      return;
    }
    // Directly usable: browser-generated previews and allowed absolute URLs.
    if (
      rawUrl.startsWith("blob:") ||
      rawUrl.startsWith("http://") ||
      rawUrl.startsWith("https://")
    ) {
      setSrc(rawUrl);
      setError(false);
      return;
    }

    // Relative API path → fetch with auth, expose as an object URL.
    let cancelled = false;
    let objectUrl: string | null = null;
    setLoading(true);
    setError(false);
    // apiFetch prepends API_BASE; strip it if an absolute API URL was passed.
    const path = rawUrl.startsWith(API_BASE) ? rawUrl.slice(API_BASE.length) : rawUrl;

    apiFetch(path)
      .then(async (resp) => {
        if (!resp.ok) throw new Error(String(resp.status));
        const blob = await resp.blob();
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setSrc(objectUrl);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [rawUrl]);

  return { src, loading, error };
}
