import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useAppStore } from '../store/useAppStore';

// Mock APIs
const mockSendChatMessage = vi.fn();
const mockResolveApproval = vi.fn();
const mockFetchConversation = vi.fn();
vi.mock('../api/chat', async () => ({
  sendChatMessage: (...args: unknown[]) => mockSendChatMessage(...args),
  resumeChat: vi.fn(),
  resolveApproval: (...args: unknown[]) => mockResolveApproval(...args),
  fetchGreeting: vi.fn().mockResolvedValue('Hey there, happy Thursday'),
}));
vi.mock('../api/conversations', () => ({
  fetchConversation: (id: number) => mockFetchConversation(id),
}));

import { ChatWindow } from '../components/ChatWindow';

describe('ChatWindow', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAppStore.setState({
      conversationId: null,
      selectedSkillId: 'shared:chat-with-kb',
      messages: [],
      streamingContent: '',
      isStreaming: false,
      pendingApproval: null,
      error: null,
      toolCalls: [],
      conversations: [],
    });
  });

  it('renders empty state with skill selected', async () => {
    render(<ChatWindow />);
    // Starts with fallback, then AI greeting loads
    await waitFor(() => {
      expect(screen.getByText('Hey there, happy Thursday')).toBeInTheDocument();
    });
    expect(screen.getByText(/Ask me anything/)).toBeInTheDocument();
  });

  it('renders empty state without skill selected', () => {
    useAppStore.setState({ selectedSkillId: null });
    render(<ChatWindow />);
    expect(
      screen.getByText(/Select a skill from the dropdown/)
    ).toBeInTheDocument();
  });

  it('renders the message input field', () => {
    render(<ChatWindow />);
    expect(screen.getByPlaceholderText(/Type your message/)).toBeInTheDocument();
  });

  it('renders existing messages', () => {
    useAppStore.setState({
      messages: [
        { id: 1, role: 'user', content: 'Hello there', created_at: '' },
        { id: 2, role: 'assistant', content: 'Hi! How can I help?', created_at: '' },
      ],
    });
    render(<ChatWindow />);
    expect(screen.getByText('Hello there')).toBeInTheDocument();
    expect(screen.getByText('Hi! How can I help?')).toBeInTheDocument();
  });

  it('shows streaming content', () => {
    useAppStore.setState({
      isStreaming: true,
      streamingContent: 'Generating response...',
      streamingSegments: [{ type: 'text', content: 'Generating response...' }],
    });
    render(<ChatWindow />);
    expect(screen.getByText('Generating response...')).toBeInTheDocument();
  });

  it('shows error message', () => {
    useAppStore.setState({ error: 'Something went wrong' });
    render(<ChatWindow />);
    expect(screen.getByText('Something went wrong')).toBeInTheDocument();
  });

  it('shows approval card when pending', () => {
    useAppStore.setState({
      pendingApproval: {
        approval_id: 'ap-1',
        tool_name: 'run_shell',
        args: { command: 'ls' },
        reason: 'List files',
      },
    });
    render(<ChatWindow />);
    expect(screen.getByText(/run_shell/)).toBeInTheDocument();
    expect(screen.getByText(/Approve/i)).toBeInTheDocument();
  });

  it('disables send button when input is empty', () => {
    useAppStore.setState({ selectedSkillId: null });
    render(<ChatWindow />);
    const sendButton = screen.getByRole('button', { name: /send message/i });
    expect(sendButton).toBeDisabled();
  });

  it('shows error when sending without skill selected', async () => {
    useAppStore.setState({ selectedSkillId: null });
    const user = userEvent.setup();
    render(<ChatWindow />);

    const input = screen.getByPlaceholderText(/Type your message/);
    await user.type(input, 'Hello');
    await user.keyboard('{Enter}');

    expect(useAppStore.getState().error).toBe(
      'Please select a skill to start a new conversation'
    );
  });

  it('sends message and adds optimistic user message', async () => {
    mockSendChatMessage.mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(<ChatWindow />);

    const input = screen.getByPlaceholderText(/Type your message/);
    await user.type(input, 'Hello world');
    await user.keyboard('{Enter}');

    // Optimistic message added
    const messages = useAppStore.getState().messages;
    expect(messages).toHaveLength(1);
    expect(messages[0].role).toBe('user');
    expect(messages[0].content).toBe('Hello world');

    // sendChatMessage called
    expect(mockSendChatMessage).toHaveBeenCalledTimes(1);
    expect(mockSendChatMessage.mock.calls[0][0]).toMatchObject({
      message: 'Hello world',
      skill_id: 'shared:chat-with-kb',
    });
  });

  it('clears input after sending', async () => {
    mockSendChatMessage.mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(<ChatWindow />);

    const input = screen.getByPlaceholderText(/Type your message/) as HTMLTextAreaElement;
    await user.type(input, 'Hello');
    await user.keyboard('{Enter}');

    expect(input.value).toBe('');
  });

  it('does not send on shift+enter (allows newline)', async () => {
    const user = userEvent.setup();
    render(<ChatWindow />);

    const input = screen.getByPlaceholderText(/Type your message/);
    await user.type(input, 'Line 1');
    await user.keyboard('{Shift>}{Enter}{/Shift}');

    expect(mockSendChatMessage).not.toHaveBeenCalled();
  });

  it('loads conversation messages when conversationId changes', async () => {
    const convDetail = {
      id: 1,
      title: 'Chat',
      skill_id: 'shared:kb',
      skill_snapshot_json: '{}',
      created_at: '',
      updated_at: '',
      messages: [
        { id: 1, role: 'user', content: 'Hello', created_at: '' },
        { id: 2, role: 'assistant', content: 'Hi!', created_at: '' },
      ],
    };
    mockFetchConversation.mockResolvedValue(convDetail);

    render(<ChatWindow />);
    useAppStore.setState({ conversationId: 1 });

    await waitFor(() => {
      expect(mockFetchConversation).toHaveBeenCalledWith(1);
    });
  });

  it('resolves approval when approve is clicked', async () => {
    mockResolveApproval.mockResolvedValue(undefined);
    useAppStore.setState({
      pendingApproval: {
        approval_id: 'ap-1',
        tool_name: 'run_shell',
        args: { command: 'ls' },
        reason: 'List files',
      },
    });

    const user = userEvent.setup();
    render(<ChatWindow />);

    await user.click(screen.getByText(/Approve/i));

    expect(mockResolveApproval).toHaveBeenCalledWith('ap-1', 'approve');
    await waitFor(() => {
      expect(useAppStore.getState().pendingApproval).toBeNull();
    });
  });
});
