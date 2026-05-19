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
};

describe('ContextUsageIndicator', () => {
  it('renders placeholder when usage is null', () => {
    render(<ContextUsageIndicator usage={null} />);
    expect(screen.getByText(/Context usage will appear/i)).toBeInTheDocument();
  });

  it('renders compact summary with percentage', () => {
    render(<ContextUsageIndicator usage={sampleUsage} />);
    // 32000 + 1500 = 33500 → 33.5k / 128.0k tokens (26%)
    expect(screen.getByText(/33\.5k \/ 128\.0k tokens \(26%\)/)).toBeInTheDocument();
  });

  it('opens popover on click and shows breakdown', async () => {
    const user = userEvent.setup();
    render(<ContextUsageIndicator usage={sampleUsage} />);
    await user.click(screen.getByRole('button', { name: /show context usage/i }));

    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('Context usage')).toBeInTheDocument();
    expect(screen.getByText('gpt-5.4-mini')).toBeInTheDocument();
    expect(screen.getByText('Cached prompt')).toBeInTheDocument();
    expect(screen.getByText('Fresh prompt')).toBeInTheDocument();
    expect(screen.getByText('Completion')).toBeInTheDocument();
    expect(screen.getByText('Free space')).toBeInTheDocument();
  });

  it('closes popover via close button', async () => {
    const user = userEvent.setup();
    render(<ContextUsageIndicator usage={sampleUsage} />);
    await user.click(screen.getByRole('button', { name: /show context usage/i }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /close context usage panel/i }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('shows correct category totals', async () => {
    const user = userEvent.setup();
    render(<ContextUsageIndicator usage={sampleUsage} />);
    await user.click(screen.getByRole('button', { name: /show context usage/i }));

    // Cached = 20000 → 20.0k
    // Fresh = 32000-20000 = 12000 → 12.0k
    // Completion = 1500 → 1.5k
    // Free = 128000 - 32000 - 1500 = 94500 → 94.5k
    expect(screen.getByText('20.0k')).toBeInTheDocument();
    expect(screen.getByText('12.0k')).toBeInTheDocument();
    expect(screen.getByText('1.5k')).toBeInTheDocument();
    expect(screen.getByText('94.5k')).toBeInTheDocument();
  });

  it('handles zero context window gracefully', () => {
    const broken: ContextUsage = { ...sampleUsage, context_window: 0 };
    render(<ContextUsageIndicator usage={broken} />);
    // Should not throw; percentage falls back to 0%
    expect(screen.getByText(/\(0%\)/)).toBeInTheDocument();
  });
});
