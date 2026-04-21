import { describe, it, expect, beforeEach } from 'vitest';
import { useAppStore } from '../store/useAppStore';
import type { Message, ApprovalInfo, ConversationSummary } from '../types';

describe('useAppStore', () => {
  beforeEach(() => {
    // Reset store to initial state
    useAppStore.setState({
      conversationId: null,
      selectedSkillId: null,
      messages: [],
      streamingContent: '',
      isStreaming: false,
      pendingApproval: null,
      error: null,
      toolCalls: [],
      conversations: [],
    });
  });

  describe('conversation management', () => {
    it('sets conversation id', () => {
      useAppStore.getState().setConversationId(42);
      expect(useAppStore.getState().conversationId).toBe(42);
    });

    it('clears conversation id', () => {
      useAppStore.getState().setConversationId(42);
      useAppStore.getState().setConversationId(null);
      expect(useAppStore.getState().conversationId).toBeNull();
    });
  });

  describe('skill selection', () => {
    it('sets selected skill id', () => {
      useAppStore.getState().setSelectedSkillId('shared:architect');
      expect(useAppStore.getState().selectedSkillId).toBe('shared:architect');
    });

    it('clears selected skill id', () => {
      useAppStore.getState().setSelectedSkillId('shared:architect');
      useAppStore.getState().setSelectedSkillId(null);
      expect(useAppStore.getState().selectedSkillId).toBeNull();
    });
  });

  describe('messages', () => {
    const msg: Message = {
      id: 1,
      role: 'user',
      content: 'Hello',
      created_at: '2026-01-01T00:00:00Z',
    };

    it('sets messages', () => {
      useAppStore.getState().setMessages([msg]);
      expect(useAppStore.getState().messages).toHaveLength(1);
      expect(useAppStore.getState().messages[0].content).toBe('Hello');
    });

    it('adds a message', () => {
      useAppStore.getState().setMessages([msg]);
      const msg2: Message = { id: 2, role: 'assistant', content: 'Hi', created_at: '' };
      useAppStore.getState().addMessage(msg2);
      expect(useAppStore.getState().messages).toHaveLength(2);
    });

    it('replaces messages on setMessages', () => {
      useAppStore.getState().addMessage(msg);
      useAppStore.getState().setMessages([]);
      expect(useAppStore.getState().messages).toHaveLength(0);
    });
  });

  describe('streaming', () => {
    it('sets streaming content', () => {
      useAppStore.getState().setStreamingContent('Hello');
      expect(useAppStore.getState().streamingContent).toBe('Hello');
    });

    it('appends streaming content', () => {
      useAppStore.getState().setStreamingContent('Hello');
      useAppStore.getState().appendStreamingContent(' World');
      expect(useAppStore.getState().streamingContent).toBe('Hello World');
    });

    it('sets isStreaming flag', () => {
      useAppStore.getState().setIsStreaming(true);
      expect(useAppStore.getState().isStreaming).toBe(true);
      useAppStore.getState().setIsStreaming(false);
      expect(useAppStore.getState().isStreaming).toBe(false);
    });
  });

  describe('approval', () => {
    it('sets pending approval', () => {
      const approval: ApprovalInfo = {
        approval_id: 'ap-1',
        tool_name: 'run_shell',
        args: { command: 'ls' },
        reason: 'List files',
      };
      useAppStore.getState().setPendingApproval(approval);
      expect(useAppStore.getState().pendingApproval).toEqual(approval);
    });

    it('clears pending approval', () => {
      useAppStore.getState().setPendingApproval({
        approval_id: 'ap-1',
        tool_name: 'run_shell',
        args: {},
        reason: '',
      });
      useAppStore.getState().setPendingApproval(null);
      expect(useAppStore.getState().pendingApproval).toBeNull();
    });
  });

  describe('tool calls', () => {
    it('adds a tool call', () => {
      useAppStore.getState().addToolCall({
        call_id: 'tc-1',
        name: 'search_kb',
        args: { query: 'test' },
      });
      const calls = useAppStore.getState().toolCalls;
      expect(calls).toHaveLength(1);
      expect(calls[0].call_id).toBe('tc-1');
      expect(calls[0].expanded).toBe(false);
      expect(calls[0].result).toBeUndefined();
    });

    it('sets tool call result', () => {
      useAppStore.getState().addToolCall({
        call_id: 'tc-1',
        name: 'search_kb',
        args: {},
      });
      useAppStore.getState().setToolCallResult('tc-1', 'found 3 results');
      const tc = useAppStore.getState().toolCalls[0];
      expect(tc.result).toBe('found 3 results');
    });

    it('toggles tool call expanded', () => {
      useAppStore.getState().addToolCall({
        call_id: 'tc-1',
        name: 'search_kb',
        args: {},
      });
      useAppStore.getState().toggleToolCallExpanded('tc-1');
      expect(useAppStore.getState().toolCalls[0].expanded).toBe(true);
      useAppStore.getState().toggleToolCallExpanded('tc-1');
      expect(useAppStore.getState().toolCalls[0].expanded).toBe(false);
    });

    it('does not affect other tool calls', () => {
      useAppStore.getState().addToolCall({ call_id: 'tc-1', name: 'a', args: {} });
      useAppStore.getState().addToolCall({ call_id: 'tc-2', name: 'b', args: {} });
      useAppStore.getState().setToolCallResult('tc-1', 'result');
      expect(useAppStore.getState().toolCalls[1].result).toBeUndefined();
    });
  });

  describe('conversations list', () => {
    it('sets conversations', () => {
      const convs: ConversationSummary[] = [
        { id: 1, title: 'Chat 1', skill_id: 's:a', created_at: '', updated_at: '' },
      ];
      useAppStore.getState().setConversations(convs);
      expect(useAppStore.getState().conversations).toHaveLength(1);
    });
  });

  describe('error handling', () => {
    it('sets error', () => {
      useAppStore.getState().setError('Something went wrong');
      expect(useAppStore.getState().error).toBe('Something went wrong');
    });

    it('clears error', () => {
      useAppStore.getState().setError('err');
      useAppStore.getState().setError(null);
      expect(useAppStore.getState().error).toBeNull();
    });
  });

  describe('resetChat', () => {
    it('resets chat state but preserves selectedSkillId', () => {
      useAppStore.getState().setSelectedSkillId('shared:architect');
      useAppStore.getState().setConversationId(42);
      useAppStore.getState().addMessage({ id: 1, role: 'user', content: 'hi', created_at: '' });
      useAppStore.getState().setStreamingContent('streaming...');
      useAppStore.getState().setIsStreaming(true);
      useAppStore.getState().setError('oops');
      useAppStore.getState().addToolCall({ call_id: 'tc', name: 'x', args: {} });

      useAppStore.getState().resetChat();

      const state = useAppStore.getState();
      expect(state.conversationId).toBeNull();
      expect(state.messages).toHaveLength(0);
      expect(state.streamingContent).toBe('');
      expect(state.isStreaming).toBe(false);
      expect(state.pendingApproval).toBeNull();
      expect(state.error).toBeNull();
      expect(state.toolCalls).toHaveLength(0);
      // selectedSkillId is preserved
      expect(state.selectedSkillId).toBe('shared:architect');
    });
  });
});
