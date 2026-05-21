import { describe, it, expect, vi, beforeEach } from 'vitest';

// Mock apiFetch
const mockApiFetch = vi.fn();
const mockApiFetchMultipart = vi.fn();
vi.mock('../api/client', () => ({
  apiFetch: (...args: unknown[]) => mockApiFetch(...args),
  apiFetchMultipart: (...args: unknown[]) => mockApiFetchMultipart(...args),
}));

function makeSSEStream(events: string): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(events));
      controller.close();
    },
  });
}

describe('API: chat', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('sendChatMessage', () => {
    it('parses SSE token events', async () => {
      const sseData =
        'event: token\ndata: {"text":"Hello"}\n\nevent: token\ndata: {"text":" world"}\n\nevent: done\ndata: {"conversation_id":1}\n\n';

      mockApiFetch.mockResolvedValue({
        ok: true,
        body: makeSSEStream(sseData),
      });

      const events: Array<[string, unknown]> = [];
      const { sendChatMessage } = await import('../api/chat');
      await sendChatMessage(
        { message: 'Hello', skill_id: 'shared:kb' },
        (event, data) => events.push([event, data])
      );

      expect(events).toHaveLength(3);
      expect(events[0]).toEqual(['token', { text: 'Hello' }]);
      expect(events[1]).toEqual(['token', { text: ' world' }]);
      expect(events[2]).toEqual(['done', { conversation_id: 1 }]);
    });

    it('parses tool_call_start and tool_result events', async () => {
      const sseData =
        'event: tool_call_start\ndata: {"call_id":"tc-1","name":"search_kb","args":{"query":"test"}}\n\n' +
        'event: tool_result\ndata: {"call_id":"tc-1","name":"search_kb","content":"found 3 results"}\n\n' +
        'event: done\ndata: {"conversation_id":1}\n\n';

      mockApiFetch.mockResolvedValue({
        ok: true,
        body: makeSSEStream(sseData),
      });

      const events: Array<[string, unknown]> = [];
      const { sendChatMessage } = await import('../api/chat');
      await sendChatMessage(
        { message: 'search', skill_id: 'shared:kb' },
        (event, data) => events.push([event, data])
      );

      expect(events[0]).toEqual([
        'tool_call_start',
        { call_id: 'tc-1', name: 'search_kb', args: { query: 'test' } },
      ]);
      expect(events[1]).toEqual([
        'tool_result',
        { call_id: 'tc-1', name: 'search_kb', content: 'found 3 results' },
      ]);
    });

    it('parses approval_required events', async () => {
      const sseData =
        'event: approval_required\ndata: {"approval_id":"ap-1","tool_name":"run_shell","args":{"command":"ls"},"reason":"needs approval"}\n\n';

      mockApiFetch.mockResolvedValue({
        ok: true,
        body: makeSSEStream(sseData),
      });

      const events: Array<[string, unknown]> = [];
      const { sendChatMessage } = await import('../api/chat');
      await sendChatMessage(
        { message: 'run ls', skill_id: 'shared:runner' },
        (event, data) => events.push([event, data])
      );

      expect(events[0]).toEqual([
        'approval_required',
        {
          approval_id: 'ap-1',
          tool_name: 'run_shell',
          args: { command: 'ls' },
          reason: 'needs approval',
        },
      ]);
    });

    it('parses error events', async () => {
      const sseData = 'event: error\ndata: {"message":"Something went wrong"}\n\n';

      mockApiFetch.mockResolvedValue({
        ok: true,
        body: makeSSEStream(sseData),
      });

      const events: Array<[string, unknown]> = [];
      const { sendChatMessage } = await import('../api/chat');
      await sendChatMessage(
        { message: 'fail', skill_id: 'shared:kb' },
        (event, data) => events.push([event, data])
      );

      expect(events[0]).toEqual(['error', { message: 'Something went wrong' }]);
    });

    it('throws on HTTP error response', async () => {
      mockApiFetch.mockResolvedValue({
        ok: false,
        status: 429,
        json: () => Promise.resolve({ detail: 'Rate limited' }),
      });

      const { sendChatMessage } = await import('../api/chat');
      await expect(
        sendChatMessage(
          { message: 'hi', skill_id: 'shared:kb' },
          () => {}
        )
      ).rejects.toThrow('Rate limited');
    });

    it('throws when no response body', async () => {
      mockApiFetch.mockResolvedValue({
        ok: true,
        body: null,
      });

      const { sendChatMessage } = await import('../api/chat');
      await expect(
        sendChatMessage({ message: 'hi', skill_id: 'shared:kb' }, () => {})
      ).rejects.toThrow('No response body');
    });

    it('ignores malformed data lines', async () => {
      const sseData =
        'event: token\ndata: not-json\n\nevent: token\ndata: {"text":"ok"}\n\nevent: done\ndata: {"conversation_id":1}\n\n';

      mockApiFetch.mockResolvedValue({
        ok: true,
        body: makeSSEStream(sseData),
      });

      const events: Array<[string, unknown]> = [];
      const { sendChatMessage } = await import('../api/chat');
      await sendChatMessage(
        { message: 'test', skill_id: 'shared:kb' },
        (event, data) => events.push([event, data])
      );

      // Malformed line skipped, only valid events captured
      expect(events).toHaveLength(2);
      expect(events[0]).toEqual(['token', { text: 'ok' }]);
    });
  });

  describe('resolveApproval', () => {
    it('sends POST with action', async () => {
      mockApiFetch.mockResolvedValue({ ok: true });

      const { resolveApproval } = await import('../api/chat');
      await resolveApproval('ap-123', 'approve');

      expect(mockApiFetch).toHaveBeenCalledWith('/api/approvals/ap-123', {
        method: 'POST',
        body: JSON.stringify({ action: 'approve' }),
      });
    });

    it('sends deny action', async () => {
      mockApiFetch.mockResolvedValue({ ok: true });

      const { resolveApproval } = await import('../api/chat');
      await resolveApproval('ap-456', 'deny');

      expect(mockApiFetch).toHaveBeenCalledWith('/api/approvals/ap-456', {
        method: 'POST',
        body: JSON.stringify({ action: 'deny' }),
      });
    });
  });

  describe('refreshArmToken', () => {
    it('sends POST with conversation_id and arm_token', async () => {
      mockApiFetch.mockResolvedValue({ ok: true });

      const { refreshArmToken } = await import('../api/chat');
      await refreshArmToken(42, 'fresh-arm-token-value');

      expect(mockApiFetch).toHaveBeenCalledWith('/api/chat/refresh-token', {
        method: 'POST',
        body: JSON.stringify({
          conversation_id: 42,
          arm_token: 'fresh-arm-token-value',
        }),
      });
    });

    it('throws on HTTP error response', async () => {
      mockApiFetch.mockResolvedValue({
        ok: false,
        status: 422,
        json: () => Promise.resolve({ detail: 'Token audience is not Azure Resource Manager' }),
      });

      const { refreshArmToken } = await import('../api/chat');
      await expect(refreshArmToken(1, 'bad-token')).rejects.toThrow(
        'Token audience is not Azure Resource Manager'
      );
    });
  });

  describe('sendChatMessage - token_refresh_required SSE', () => {
    it('parses token_refresh_required events', async () => {
      const sseData =
        'event: token_refresh_required\ndata: {"conversation_id":5,"tool_name":"az_cli","status":"expired"}\n\n' +
        'event: done\ndata: {"conversation_id":5}\n\n';

      mockApiFetch.mockResolvedValue({
        ok: true,
        body: makeSSEStream(sseData),
      });

      const events: Array<[string, unknown]> = [];
      const { sendChatMessage } = await import('../api/chat');
      await sendChatMessage(
        { message: 'list vms', skill_id: 'shared:architect' },
        (event, data) => events.push([event, data])
      );

      expect(events).toHaveLength(2);
      expect(events[0]).toEqual([
        'token_refresh_required',
        { conversation_id: 5, tool_name: 'az_cli', status: 'expired' },
      ]);
    });
  });
});
