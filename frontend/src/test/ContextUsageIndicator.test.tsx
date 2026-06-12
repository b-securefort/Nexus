import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ContextUsageIndicator } from '../components/ContextUsageIndicator';
import type { ContextUsage } from '../types';

const sampleUsage: ContextUsage = {
  prompt_tokens: 32_000,
  completion_tokens: 1_500,
  cached_tokens: 20_000,
  context_window: 128_000,
  model: 'gpt-5.4-mini',
  segments: [
    { label: 'System prompt', tokens: 8_000 },
    { label: 'Knowledge base', tokens: 4_000 },
    { label: 'Tools', tokens: 12_000 },
    { label: 'Messages', tokens: 8_000 },
  ],
};

// A pre-segments payload, to exercise the legacy fallback path.
const legacyUsage: ContextUsage = {
  prompt_tokens: 32_000,
  completion_tokens: 1_500,
  cached_tokens: 20_000,
  context_window: 128_000,
  model: 'gpt-5.4-mini',
};

describe('ContextUsageIndicator', () => {
  it('renders nothing when usage is null', () => {
    const { container } = render(<ContextUsageIndicator usage={null} />);
    expect(container.firstChild).toBeNull();
  });

  it('renders compact summary using prompt tokens only (occupancy, not spend)', () => {
    render(<ContextUsageIndicator usage={sampleUsage} />);
    // Headline = prompt_tokens only (completion excluded): 32000 → 32.0k / 128.0k (25%)
    expect(screen.getByText(/32\.0k \/ 128\.0k tokens \(25%\)/)).toBeInTheDocument();
  });

  it('opens popover and shows the structural segment breakdown', async () => {
    const user = userEvent.setup();
    render(<ContextUsageIndicator usage={sampleUsage} />);
    await user.click(screen.getByRole('button', { name: /show context usage/i }));

    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('Context usage')).toBeInTheDocument();
    expect(screen.getByText('gpt-5.4-mini')).toBeInTheDocument();
    expect(screen.getByText('System prompt')).toBeInTheDocument();
    expect(screen.getByText('Knowledge base')).toBeInTheDocument();
    expect(screen.getByText('Tools')).toBeInTheDocument();
    expect(screen.getByText('Messages')).toBeInTheDocument();
    expect(screen.getByText('Free space')).toBeInTheDocument();
    // Completion is output, not occupancy — must not appear.
    expect(screen.queryByText('Completion')).not.toBeInTheDocument();
  });

  it('falls back to the cache split when no segments are present', async () => {
    const user = userEvent.setup();
    render(<ContextUsageIndicator usage={legacyUsage} />);
    await user.click(screen.getByRole('button', { name: /show context usage/i }));

    expect(screen.getByText('Cached prompt')).toBeInTheDocument();
    expect(screen.getByText('Fresh prompt')).toBeInTheDocument();
    expect(screen.getByText('Free space')).toBeInTheDocument();
    expect(screen.queryByText('Completion')).not.toBeInTheDocument();
  });

  it('closes popover via close button', async () => {
    const user = userEvent.setup();
    render(<ContextUsageIndicator usage={sampleUsage} />);
    await user.click(screen.getByRole('button', { name: /show context usage/i }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /close context usage panel/i }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('shows correct category totals from segments', async () => {
    const user = userEvent.setup();
    render(<ContextUsageIndicator usage={sampleUsage} />);
    await user.click(screen.getByRole('button', { name: /show context usage/i }));

    // Segments render as-is; Free = context_window - prompt_tokens.
    // System prompt 8.0k, KB 4.0k, Tools 12.0k, Messages 8.0k
    // Free = 128000 - 32000 = 96000 → 96.0k
    expect(screen.getByText('12.0k')).toBeInTheDocument();
    expect(screen.getByText('4.0k')).toBeInTheDocument();
    expect(screen.getByText('96.0k')).toBeInTheDocument();
    // 8.0k appears twice (System prompt + Messages) — assert at least one.
    expect(screen.getAllByText('8.0k').length).toBeGreaterThanOrEqual(1);
  });

  it('handles zero context window gracefully', () => {
    const broken: ContextUsage = { ...sampleUsage, context_window: 0 };
    render(<ContextUsageIndicator usage={broken} />);
    // Should not throw; percentage falls back to 0%
    expect(screen.getByText(/\(0%\)/)).toBeInTheDocument();
  });
});
