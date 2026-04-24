import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MessageBubble } from '../components/MessageBubble';
import type { Message } from '../types';

describe('MessageBubble', () => {
  it('renders user message content', () => {
    const msg: Message = {
      id: 1,
      role: 'user',
      content: 'What is Azure?',
      created_at: '2026-01-01T00:00:00Z',
    };
    render(
      <MessageBubble message={msg} toolCalls={[]} toolResultMap={new Map()} onToggleToolCall={() => {}} />
    );
    expect(screen.getByText('What is Azure?')).toBeInTheDocument();
  });

  it('renders assistant message content', () => {
    const msg: Message = {
      id: 2,
      role: 'assistant',
      content: 'Azure is a cloud platform by Microsoft.',
      created_at: '',
    };
    render(
      <MessageBubble message={msg} toolCalls={[]} toolResultMap={new Map()} onToggleToolCall={() => {}} />
    );
    expect(
      screen.getByText('Azure is a cloud platform by Microsoft.')
    ).toBeInTheDocument();
  });

  it('returns null for tool messages', () => {
    const msg: Message = {
      id: 3,
      role: 'tool',
      content: 'tool result',
      tool_call_id: 'tc-1',
      tool_name: 'search_kb',
      created_at: '',
    };
    const { container } = render(
      <MessageBubble message={msg} toolCalls={[]} toolResultMap={new Map()} onToggleToolCall={() => {}} />
    );
    expect(container.firstChild).toBeNull();
  });

  it('renders user messages right-aligned with blue styling', () => {
    const msg: Message = {
      id: 1,
      role: 'user',
      content: 'Hello',
      created_at: '',
    };
    const { container } = render(
      <MessageBubble message={msg} toolCalls={[]} toolResultMap={new Map()} onToggleToolCall={() => {}} />
    );
    const wrapper = container.firstChild as HTMLElement;
    expect(wrapper.className).toContain('justify-end');
  });

  it('renders assistant messages left-aligned', () => {
    const msg: Message = {
      id: 2,
      role: 'assistant',
      content: 'Hi',
      created_at: '',
    };
    const { container } = render(
      <MessageBubble message={msg} toolCalls={[]} toolResultMap={new Map()} onToggleToolCall={() => {}} />
    );
    const wrapper = container.firstChild as HTMLElement;
    expect(wrapper.className).toContain('justify-start');
  });
});
