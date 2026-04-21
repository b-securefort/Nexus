import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

// Mock API
const mockFetchSkills = vi.fn();
const mockDeletePersonalSkill = vi.fn();
vi.mock('../api/skills', () => ({
  fetchSkills: () => mockFetchSkills(),
  deletePersonalSkill: (name: string) => mockDeletePersonalSkill(name),
  fetchTools: () => Promise.resolve([]),
  fetchPersonalSkill: () => Promise.resolve({}),
  createPersonalSkill: () => Promise.resolve({}),
  updatePersonalSkill: () => Promise.resolve({}),
}));

import { SkillsPage } from '../pages/SkillsPage';

const sampleSkills = [
  {
    id: 'shared:architect',
    name: 'architect',
    display_name: 'Architect',
    description: 'Cloud architect',
    tools: ['read_kb_file'],
    source: 'shared' as const,
  },
  {
    id: 'personal:my-skill',
    name: 'my-skill',
    display_name: 'My Custom Skill',
    description: 'A personal skill',
    tools: [],
    source: 'personal' as const,
  },
];

function renderSkillsPage() {
  return render(
    <MemoryRouter>
      <SkillsPage />
    </MemoryRouter>
  );
}

describe('SkillsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Mock window.confirm for delete tests
    vi.spyOn(window, 'confirm').mockReturnValue(true);
  });

  it('renders page title', async () => {
    mockFetchSkills.mockResolvedValue([]);
    renderSkillsPage();
    expect(screen.getByText('Skills')).toBeInTheDocument();
  });

  it('has a New Skill button', async () => {
    mockFetchSkills.mockResolvedValue([]);
    renderSkillsPage();
    expect(screen.getByText('New Skill')).toBeInTheDocument();
  });

  it('shows shared and personal skills', async () => {
    mockFetchSkills.mockResolvedValue(sampleSkills);
    renderSkillsPage();

    await waitFor(() => {
      expect(screen.getByText('Architect')).toBeInTheDocument();
      expect(screen.getByText('My Custom Skill')).toBeInTheDocument();
    });
  });

  it('shows section headers', async () => {
    mockFetchSkills.mockResolvedValue(sampleSkills);
    renderSkillsPage();

    await waitFor(() => {
      expect(screen.getByText('Shared (Team)')).toBeInTheDocument();
      expect(screen.getByText('My Skills')).toBeInTheDocument();
    });
  });

  it('shows skill descriptions', async () => {
    mockFetchSkills.mockResolvedValue(sampleSkills);
    renderSkillsPage();

    await waitFor(() => {
      expect(screen.getByText('Cloud architect')).toBeInTheDocument();
      expect(screen.getByText('A personal skill')).toBeInTheDocument();
    });
  });

  it('switches to editor when New Skill is clicked', async () => {
    mockFetchSkills.mockResolvedValue(sampleSkills);
    const user = userEvent.setup();
    renderSkillsPage();

    await waitFor(() => {
      expect(screen.getByText('Skills')).toBeInTheDocument();
    });

    await user.click(screen.getByText('New Skill'));
    expect(screen.getByText('Create Skill')).toBeInTheDocument();
  });

  it('shows "No shared skills available" when none exist', async () => {
    mockFetchSkills.mockResolvedValue([
      { ...sampleSkills[1] }, // only personal
    ]);
    renderSkillsPage();

    await waitFor(() => {
      expect(screen.getByText('No shared skills available.')).toBeInTheDocument();
    });
  });
});
