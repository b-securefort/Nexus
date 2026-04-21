import { describe, it, expect, vi, beforeEach } from 'vitest';

// Mock apiFetch
const mockApiFetch = vi.fn();
vi.mock('../api/client', () => ({
  apiFetch: (...args: unknown[]) => mockApiFetch(...args),
}));

describe('API: skills', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('fetchSkills', () => {
    it('returns array when API returns array', async () => {
      const skills = [
        { id: 'shared:kb', name: 'kb', display_name: 'KB', description: '', tools: [], source: 'shared' },
      ];
      mockApiFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(skills),
      });

      const { fetchSkills } = await import('../api/skills');
      const result = await fetchSkills();
      expect(result).toEqual(skills);
      expect(result).toHaveLength(1);
    });

    it('unwraps value field when API returns envelope', async () => {
      const skills = [
        { id: 'shared:kb', name: 'kb', display_name: 'KB', description: '', tools: [], source: 'shared' },
      ];
      mockApiFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ value: skills, Count: 1 }),
      });

      const { fetchSkills } = await import('../api/skills');
      const result = await fetchSkills();
      expect(result).toEqual(skills);
    });

    it('returns empty array for null value', async () => {
      mockApiFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ value: null }),
      });

      const { fetchSkills } = await import('../api/skills');
      const result = await fetchSkills();
      expect(result).toEqual([]);
    });

    it('throws on failed response', async () => {
      mockApiFetch.mockResolvedValue({ ok: false, status: 500 });

      const { fetchSkills } = await import('../api/skills');
      await expect(fetchSkills()).rejects.toThrow('Failed to fetch skills');
    });
  });

  describe('fetchTools', () => {
    it('returns tools array', async () => {
      const tools = [{ name: 'search_kb', description: 'Search', requires_approval: false }];
      mockApiFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(tools),
      });

      const { fetchTools } = await import('../api/skills');
      const result = await fetchTools();
      expect(result).toEqual(tools);
    });

    it('throws on error', async () => {
      mockApiFetch.mockResolvedValue({ ok: false });

      const { fetchTools } = await import('../api/skills');
      await expect(fetchTools()).rejects.toThrow();
    });
  });

  describe('createPersonalSkill', () => {
    it('sends POST with body and returns skill', async () => {
      const skill = { id: 'personal:test', name: 'test', display_name: 'Test', description: '', tools: [], source: 'personal' };
      mockApiFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(skill),
      });

      const { createPersonalSkill } = await import('../api/skills');
      const result = await createPersonalSkill({
        name: 'test',
        display_name: 'Test',
        description: '',
        system_prompt: 'You are a test',
        tools: [],
      });

      expect(mockApiFetch).toHaveBeenCalledWith('/api/skills/personal', {
        method: 'POST',
        body: expect.any(String),
      });
      expect(result).toEqual(skill);
    });

    it('throws with detail on failure', async () => {
      mockApiFetch.mockResolvedValue({
        ok: false,
        json: () => Promise.resolve({ detail: 'Skill already exists' }),
      });

      const { createPersonalSkill } = await import('../api/skills');
      await expect(
        createPersonalSkill({
          name: 'dup',
          display_name: 'Dup',
          description: '',
          system_prompt: '',
          tools: [],
        })
      ).rejects.toThrow('Skill already exists');
    });
  });

  describe('deletePersonalSkill', () => {
    it('sends DELETE request', async () => {
      mockApiFetch.mockResolvedValue({ ok: true });

      const { deletePersonalSkill } = await import('../api/skills');
      await deletePersonalSkill('my-skill');

      expect(mockApiFetch).toHaveBeenCalledWith('/api/skills/personal/my-skill', {
        method: 'DELETE',
      });
    });

    it('throws on failure', async () => {
      mockApiFetch.mockResolvedValue({ ok: false });

      const { deletePersonalSkill } = await import('../api/skills');
      await expect(deletePersonalSkill('x')).rejects.toThrow();
    });
  });
});

describe('API: conversations', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('fetchConversations', () => {
    it('returns conversations list', async () => {
      const convs = [{ id: 1, title: 'Chat', skill_id: 's:a', created_at: '', updated_at: '' }];
      mockApiFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(convs),
      });

      const { fetchConversations } = await import('../api/conversations');
      const result = await fetchConversations();
      expect(result).toEqual(convs);
    });

    it('throws on error', async () => {
      mockApiFetch.mockResolvedValue({ ok: false });

      const { fetchConversations } = await import('../api/conversations');
      await expect(fetchConversations()).rejects.toThrow('Failed to fetch conversations');
    });
  });

  describe('fetchConversation', () => {
    it('returns conversation detail', async () => {
      const detail = {
        id: 1,
        title: 'Chat',
        skill_id: 's:a',
        skill_snapshot_json: '{}',
        created_at: '',
        updated_at: '',
        messages: [],
      };
      mockApiFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(detail),
      });

      const { fetchConversation } = await import('../api/conversations');
      const result = await fetchConversation(1);
      expect(result).toEqual(detail);
    });

    it('throws on 404', async () => {
      mockApiFetch.mockResolvedValue({ ok: false, status: 404 });

      const { fetchConversation } = await import('../api/conversations');
      await expect(fetchConversation(999)).rejects.toThrow('Conversation not found');
    });
  });

  describe('deleteConversation', () => {
    it('sends DELETE request', async () => {
      mockApiFetch.mockResolvedValue({ ok: true });

      const { deleteConversation } = await import('../api/conversations');
      await deleteConversation(42);

      expect(mockApiFetch).toHaveBeenCalledWith('/api/conversations/42', { method: 'DELETE' });
    });
  });

  describe('renameConversation', () => {
    it('sends PATCH with title', async () => {
      mockApiFetch.mockResolvedValue({ ok: true });

      const { renameConversation } = await import('../api/conversations');
      await renameConversation(1, 'New Title');

      expect(mockApiFetch).toHaveBeenCalledWith('/api/conversations/1', {
        method: 'PATCH',
        body: JSON.stringify({ title: 'New Title' }),
      });
    });
  });
});
