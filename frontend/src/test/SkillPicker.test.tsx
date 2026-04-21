import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useAppStore } from '../store/useAppStore';

// Mock the skills API
const mockFetchSkills = vi.fn();
vi.mock('../api/skills', () => ({
  fetchSkills: () => mockFetchSkills(),
}));

import { SkillPicker } from '../components/SkillPicker';

const sampleSkills = [
  {
    id: 'shared:architect',
    name: 'architect',
    display_name: 'Architect',
    description: 'Cloud architect mode',
    tools: ['read_kb_file'],
    source: 'shared' as const,
  },
  {
    id: 'shared:chat-with-kb',
    name: 'chat-with-kb',
    display_name: 'Chat with KB',
    description: 'General chat',
    tools: ['read_kb_file', 'search_kb'],
    source: 'shared' as const,
  },
  {
    id: 'personal:my-skill',
    name: 'my-skill',
    display_name: 'My Skill',
    description: 'Personal skill',
    tools: [],
    source: 'personal' as const,
  },
];

describe('SkillPicker', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAppStore.setState({
      selectedSkillId: null,
      conversationId: null,
    });
  });

  it('auto-selects chat-with-kb as default skill', async () => {
    mockFetchSkills.mockResolvedValue(sampleSkills);
    render(<SkillPicker />);

    await waitFor(() => {
      expect(useAppStore.getState().selectedSkillId).toBe('shared:chat-with-kb');
    });
  });

  it('shows "Select a skill..." when no skill selected and API not loaded', () => {
    mockFetchSkills.mockResolvedValue([]);
    render(<SkillPicker />);
    expect(screen.getByText('Select a skill...')).toBeInTheDocument();
  });

  it('displays the selected skill display name', async () => {
    mockFetchSkills.mockResolvedValue(sampleSkills);
    render(<SkillPicker />);

    await waitFor(() => {
      expect(screen.getByText('Chat with KB')).toBeInTheDocument();
    });
  });

  it('opens dropdown on click', async () => {
    mockFetchSkills.mockResolvedValue(sampleSkills);
    const user = userEvent.setup();
    render(<SkillPicker />);

    await waitFor(() => {
      expect(screen.getByText('Chat with KB')).toBeInTheDocument();
    });

    await user.click(screen.getByRole('button'));

    // Should show section headers
    expect(screen.getByText('Shared (Team)')).toBeInTheDocument();
    expect(screen.getByText('My Skills')).toBeInTheDocument();
    // Should list all skills
    expect(screen.getByText('Architect')).toBeInTheDocument();
    expect(screen.getByText('My Skill')).toBeInTheDocument();
  });

  it('selects a different skill on click', async () => {
    mockFetchSkills.mockResolvedValue(sampleSkills);
    const user = userEvent.setup();
    render(<SkillPicker />);

    await waitFor(() => {
      expect(screen.getByText('Chat with KB')).toBeInTheDocument();
    });

    await user.click(screen.getByRole('button'));
    await user.click(screen.getByText('Architect'));

    expect(useAppStore.getState().selectedSkillId).toBe('shared:architect');
  });

  it('is disabled when conversationId is set (locked)', async () => {
    mockFetchSkills.mockResolvedValue(sampleSkills);
    useAppStore.setState({ conversationId: 1, selectedSkillId: 'shared:architect' });

    render(<SkillPicker />);

    const button = screen.getByRole('button');
    expect(button).toBeDisabled();
  });

  it('shows "No skills available" when skills list is empty', async () => {
    mockFetchSkills.mockResolvedValue([]);
    const user = userEvent.setup();
    render(<SkillPicker />);

    await user.click(screen.getByRole('button'));
    expect(screen.getByText('No skills available')).toBeInTheDocument();
  });

  it('falls back to first skill when chat-with-kb is not available', async () => {
    const skillsWithoutDefault = [sampleSkills[0]]; // only architect
    mockFetchSkills.mockResolvedValue(skillsWithoutDefault);
    render(<SkillPicker />);

    await waitFor(() => {
      expect(useAppStore.getState().selectedSkillId).toBe('shared:architect');
    });
  });

  it('handles fetch error gracefully', async () => {
    mockFetchSkills.mockRejectedValue(new Error('Network error'));
    render(<SkillPicker />);

    // Should still render without crashing
    expect(screen.getByText('Select a skill...')).toBeInTheDocument();
  });
});
