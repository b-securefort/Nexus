import { describe, it, expect, vi, beforeEach } from 'vitest';

const mockApiFetch = vi.fn();
vi.mock('../api/client', () => ({
  apiFetch: (...args: unknown[]) => mockApiFetch(...args),
}));

const SAMPLE_LIST = {
  items: [
    {
      id: 1, type: 'semantic', category: 'syntax-fix',
      tool_name: 'az_resource_graph', summary: 's', status: 'active',
      validation_count: 3, failure_count: 0,
      recorded_at: '2026-05-20T00:00:00Z',
      last_validated_at: null, last_retrieved_at: null,
    },
  ],
  total: 1, offset: 0, limit: 50,
};

describe('API: learnings', () => {
  beforeEach(() => vi.clearAllMocks());

  describe('listLearnings', () => {
    it('passes default pagination when no params given', async () => {
      mockApiFetch.mockResolvedValue({ ok: true, json: () => Promise.resolve(SAMPLE_LIST) });
      const { listLearnings } = await import('../api/learnings');
      await listLearnings();
      const calledPath = mockApiFetch.mock.calls[0][0] as string;
      expect(calledPath).toMatch(/limit=50/);
      expect(calledPath).toMatch(/offset=0/);
    });

    it('includes filters in the query string', async () => {
      mockApiFetch.mockResolvedValue({ ok: true, json: () => Promise.resolve(SAMPLE_LIST) });
      const { listLearnings } = await import('../api/learnings');
      await listLearnings({ status: 'active', tool_name: 'az_cli', category: 'gotcha' });
      const calledPath = mockApiFetch.mock.calls[0][0] as string;
      expect(calledPath).toMatch(/status=active/);
      expect(calledPath).toMatch(/tool_name=az_cli/);
      expect(calledPath).toMatch(/category=gotcha/);
    });

    it('throws a specific message on 403', async () => {
      mockApiFetch.mockResolvedValue({ ok: false, status: 403 });
      const { listLearnings } = await import('../api/learnings');
      await expect(listLearnings()).rejects.toThrow(/architect/i);
    });

    it('returns the parsed list response', async () => {
      mockApiFetch.mockResolvedValue({ ok: true, json: () => Promise.resolve(SAMPLE_LIST) });
      const { listLearnings } = await import('../api/learnings');
      const res = await listLearnings();
      expect(res.items).toHaveLength(1);
      expect(res.items[0].status).toBe('active');
    });
  });

  describe('getLearning', () => {
    it('returns 404 message when missing', async () => {
      mockApiFetch.mockResolvedValue({ ok: false, status: 404 });
      const { getLearning } = await import('../api/learnings');
      await expect(getLearning(42)).rejects.toThrow(/not found/i);
    });
  });

  describe('patchLearningStatus', () => {
    it('sends PATCH with status body', async () => {
      mockApiFetch.mockResolvedValue({ ok: true, json: () => Promise.resolve({ id: 1, status: 'active' }) });
      const { patchLearningStatus } = await import('../api/learnings');
      await patchLearningStatus(1, 'active');
      const [path, opts] = mockApiFetch.mock.calls[0];
      expect(path).toBe('/api/learnings/1');
      expect((opts as RequestInit).method).toBe('PATCH');
      expect((opts as RequestInit).body).toBe(JSON.stringify({ status: 'active' }));
    });

    it('surfaces server detail on error', async () => {
      mockApiFetch.mockResolvedValue({
        ok: false, status: 409,
        json: () => Promise.resolve({ detail: 'Cannot revive rejected entry' }),
      });
      const { patchLearningStatus } = await import('../api/learnings');
      await expect(patchLearningStatus(1, 'active')).rejects.toThrow(/Cannot revive/);
    });
  });

  describe('deleteLearning', () => {
    it('sends DELETE', async () => {
      mockApiFetch.mockResolvedValue({ ok: true, status: 204 });
      const { deleteLearning } = await import('../api/learnings');
      await deleteLearning(7);
      const [path, opts] = mockApiFetch.mock.calls[0];
      expect(path).toBe('/api/learnings/7');
      expect((opts as RequestInit).method).toBe('DELETE');
    });
  });
});
