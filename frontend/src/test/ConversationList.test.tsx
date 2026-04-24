import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useAppStore } from '../store/useAppStore';

// Mock conversations API
const mockFetchConversations = vi.fn();
const mockDeleteConversation = vi.fn();
vi.mock('../api/conversations', () => ({
  fetchConversations: () => mockFetchConversations(),
  deleteConversation: (id: number) => mockDeleteConversation(id),
  renameConversation: vi.fn().mockResolvedValue(undefined),
}));

import { ConversationList } from '../components/ConversationList';

const sampleConversations = [
  { id: 1, title: 'First Chat', skill_id: 'shared:architect', created_at: new Date().toISOString(), updated_at: new Date().toISOString() },
  { id: 2, title: 'Second Chat', skill_id: 'shared:kb', created_at: new Date(Date.now() - 86400000).toISOString(), updated_at: new Date(Date.now() - 86400000).toISOString() },
];

describe('ConversationList', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    useAppStore.setState({
      conversations: [],
      conversationId: null,
      selectedSkillId: null,
      messages: [],
      streamingContent: '',
      isStreaming: false,
      pendingApproval: null,
      error: null,
      toolCalls: [],
      sidebarOpen: true,
      searchQuery: '',
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('shows loading state initially', () => {
    mockFetchConversations.mockReturnValue(new Promise(() => {})); // never resolves
    render(<ConversationList />);
    expect(screen.getByText('Loading...')).toBeInTheDocument();
  });

  it('shows "No conversations yet" when empty', async () => {
    mockFetchConversations.mockResolvedValue([]);
    render(<ConversationList />);

    await waitFor(() => {
      expect(screen.getByText('No conversations yet')).toBeInTheDocument();
    });
  });

  it('renders conversation titles', async () => {
    mockFetchConversations.mockResolvedValue(sampleConversations);
    render(<ConversationList />);

    await waitFor(() => {
      expect(screen.getByText('First Chat')).toBeInTheDocument();
      expect(screen.getByText('Second Chat')).toBeInTheDocument();
    });
  });

  it('has a search input', async () => {
    mockFetchConversations.mockResolvedValue([]);
    render(<ConversationList />);
    expect(screen.getByPlaceholderText('Search chats...')).toBeInTheDocument();
  });

  it('filters conversations by search query', async () => {
    mockFetchConversations.mockResolvedValue(sampleConversations);
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<ConversationList />);

    await waitFor(() => {
      expect(screen.getByText('First Chat')).toBeInTheDocument();
    });

    const searchInput = screen.getByPlaceholderText('Search chats...');
    await user.type(searchInput, 'First');
    expect(screen.getByText('First Chat')).toBeInTheDocument();
    expect(screen.queryByText('Second Chat')).not.toBeInTheDocument();
  });

  it('selects a conversation on click', async () => {
    mockFetchConversations.mockResolvedValue(sampleConversations);
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<ConversationList />);

    await waitFor(() => {
      expect(screen.getByText('First Chat')).toBeInTheDocument();
    });

    await user.click(screen.getByText('First Chat'));
    expect(useAppStore.getState().conversationId).toBe(1);
    expect(useAppStore.getState().selectedSkillId).toBe('shared:architect');
  });

  it('groups conversations under "Today" for recent ones', async () => {
    const recentConv = [{
      id: 1,
      title: 'Recent',
      skill_id: 's:a',
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    }];
    mockFetchConversations.mockResolvedValue(recentConv);
    render(<ConversationList />);

    await waitFor(() => {
      expect(screen.getByText('Today')).toBeInTheDocument();
      expect(screen.getByText('Recent')).toBeInTheDocument();
    });
  });
});
