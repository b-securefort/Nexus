import { describe, it, expect } from 'vitest';
import type {
  Skill,
  Message,
  ConversationSummary,
  ConversationDetail,
  ApprovalInfo,
  ToolInfo,
  ChatRequest,
  CreateSkillRequest,
  UpdateSkillRequest,
} from '../types';

describe('TypeScript interfaces', () => {
  it('Skill interface accepts valid shared skill', () => {
    const skill: Skill = {
      id: 'shared:architect',
      name: 'architect',
      display_name: 'Architect',
      description: 'Senior architect mode',
      tools: ['read_kb_file', 'search_kb'],
      source: 'shared',
    };
    expect(skill.source).toBe('shared');
    expect(skill.tools).toHaveLength(2);
  });

  it('Skill interface accepts personal skill with system_prompt', () => {
    const skill: Skill = {
      id: 'personal:my-skill',
      name: 'my-skill',
      display_name: 'My Skill',
      description: '',
      tools: [],
      source: 'personal',
      system_prompt: 'You are helpful',
    };
    expect(skill.system_prompt).toBe('You are helpful');
  });

  it('Message interface for user message', () => {
    const msg: Message = {
      id: 1,
      role: 'user',
      content: 'Hello!',
      created_at: '2026-01-01T00:00:00Z',
    };
    expect(msg.role).toBe('user');
  });

  it('Message interface for assistant with tool calls', () => {
    const msg: Message = {
      id: 2,
      role: 'assistant',
      content: 'Let me search...',
      tool_calls_json: '[{"id":"tc1","function":{"name":"search_kb"}}]',
      created_at: '2026-01-01T00:00:00Z',
    };
    expect(msg.tool_calls_json).toBeDefined();
  });

  it('Message interface for tool result', () => {
    const msg: Message = {
      id: 3,
      role: 'tool',
      content: '{"results":[]}',
      tool_call_id: 'tc1',
      tool_name: 'search_kb',
      created_at: '',
    };
    expect(msg.tool_name).toBe('search_kb');
  });

  it('ConversationSummary interface', () => {
    const conv: ConversationSummary = {
      id: 1,
      title: 'My Chat',
      skill_id: 'shared:architect',
      created_at: '2026-01-01',
      updated_at: '2026-01-01',
    };
    expect(conv.id).toBe(1);
  });

  it('ConversationDetail interface', () => {
    const detail: ConversationDetail = {
      id: 1,
      title: 'Chat',
      skill_id: 'shared:architect',
      skill_snapshot_json: '{}',
      created_at: '',
      updated_at: '',
      messages: [],
    };
    expect(detail.messages).toHaveLength(0);
  });

  it('ApprovalInfo interface', () => {
    const approval: ApprovalInfo = {
      approval_id: 'uuid-1',
      tool_name: 'run_shell',
      args: { command: 'ls -la' },
      reason: 'List files in directory',
    };
    expect(approval.tool_name).toBe('run_shell');
  });

  it('ToolInfo interface', () => {
    const tool: ToolInfo = {
      name: 'run_shell',
      description: 'Run a shell command',
      requires_approval: true,
    };
    expect(tool.requires_approval).toBe(true);
  });

  it('ChatRequest interface', () => {
    const req: ChatRequest = {
      message: 'What is Azure?',
      skill_id: 'shared:architect',
    };
    expect(req.message).toBeTruthy();
    expect(req.conversation_id).toBeUndefined();
  });

  it('CreateSkillRequest interface', () => {
    const req: CreateSkillRequest = {
      name: 'my-skill',
      display_name: 'My Skill',
      description: 'desc',
      system_prompt: 'Be helpful',
      tools: ['read_kb_file'],
    };
    expect(req.name).toBe('my-skill');
  });

  it('UpdateSkillRequest interface', () => {
    const req: UpdateSkillRequest = {
      display_name: 'Updated Name',
    };
    expect(req.display_name).toBe('Updated Name');
    expect(req.system_prompt).toBeUndefined();
  });
});
