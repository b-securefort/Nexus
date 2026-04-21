import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';

// Mock the AuthProvider module before importing client
vi.mock('../auth/AuthProvider', () => ({
  msalInstance: {
    getAllAccounts: vi.fn(() => []),
    loginRedirect: vi.fn(),
    acquireTokenSilent: vi.fn(),
    acquireTokenRedirect: vi.fn(),
  },
}));
vi.mock('../auth/msalConfig', () => ({
  loginRequest: { scopes: ['api://test/user_impersonation'] },
}));

// We need to mock import.meta.env
const originalEnv = { ...import.meta.env };

describe('API Client', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    // Reset env
    import.meta.env.VITE_DEV_AUTH_BYPASS = 'true';
    import.meta.env.VITE_API_BASE_URL = 'http://localhost:8000';
  });

  describe('apiFetch', () => {
    it('sends Authorization header with dev bypass token', async () => {
      const mockFetch = vi.fn().mockResolvedValue(new Response('{}', { status: 200 }));
      vi.stubGlobal('fetch', mockFetch);

      const { apiFetch } = await import('../api/client');
      await apiFetch('/api/skills');

      expect(mockFetch).toHaveBeenCalledTimes(1);
      const [url, opts] = mockFetch.mock.calls[0];
      expect(url).toContain('/api/skills');
      const headers = new Headers(opts.headers);
      expect(headers.get('Authorization')).toBe('Bearer dev-bypass-token');
      expect(headers.get('Content-Type')).toBe('application/json');

      vi.unstubAllGlobals();
    });

    it('passes custom options through', async () => {
      const mockFetch = vi.fn().mockResolvedValue(new Response('{}', { status: 200 }));
      vi.stubGlobal('fetch', mockFetch);

      const { apiFetch } = await import('../api/client');
      await apiFetch('/api/skills/personal', {
        method: 'POST',
        body: JSON.stringify({ name: 'test' }),
      });

      const [, opts] = mockFetch.mock.calls[0];
      expect(opts.method).toBe('POST');
      expect(opts.body).toBe('{"name":"test"}');

      vi.unstubAllGlobals();
    });
  });

  describe('apiStreamUrl', () => {
    it('returns full URL with API base', async () => {
      const { apiStreamUrl } = await import('../api/client');
      const url = apiStreamUrl('/api/chat');
      expect(url).toContain('/api/chat');
    });
  });
});
