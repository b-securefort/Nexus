import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { WeeklyBudgetIndicator } from '../components/WeeklyBudgetIndicator';
import type { WeeklyBudget } from '../types';
import * as usageApi from '../api/usage';

vi.mock('../api/usage');

const enabled: WeeklyBudget = {
  enabled: true,
  cap_usd: 20,
  spent_this_week_usd: 5,
  carryover_debt_usd: 0,
  remaining_usd: 15,
  remaining_fraction: 0.75,
  week_resets_at: '2026-06-15T00:00:00+00:00',
};

describe('WeeklyBudgetIndicator', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it('renders nothing when the feature is disabled', async () => {
    vi.mocked(usageApi.fetchWeeklyBudget).mockResolvedValue({ enabled: false });
    const { container } = render(<WeeklyBudgetIndicator />);
    await waitFor(() => expect(usageApi.fetchWeeklyBudget).toHaveBeenCalled());
    expect(container.firstChild).toBeNull();
  });

  it('renders nothing when the fetch fails (read-only accessory)', async () => {
    vi.mocked(usageApi.fetchWeeklyBudget).mockRejectedValue(new Error('boom'));
    const { container } = render(<WeeklyBudgetIndicator />);
    await waitFor(() => expect(usageApi.fetchWeeklyBudget).toHaveBeenCalled());
    expect(container.firstChild).toBeNull();
  });

  it('shows remaining dollars and opens the breakdown', async () => {
    vi.mocked(usageApi.fetchWeeklyBudget).mockResolvedValue(enabled);
    const user = userEvent.setup();
    render(<WeeklyBudgetIndicator />);

    // $5 used of $20 cap → 500 / 2,000 credits used, 25% (1 credit = $0.01)
    expect(await screen.findByText(/500 \/ 2,000 credits used/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /show weekly budget/i }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('Weekly budget')).toBeInTheDocument();
    expect(screen.getByText(/500 of 2,000 credits used \(25%\)/)).toBeInTheDocument();
    expect(screen.getByText('Spent this week')).toBeInTheDocument();
  });

  it('hides the carryover row when there is no debt', async () => {
    vi.mocked(usageApi.fetchWeeklyBudget).mockResolvedValue(enabled);
    const user = userEvent.setup();
    render(<WeeklyBudgetIndicator />);
    await screen.findByText(/credits used/);
    await user.click(screen.getByRole('button', { name: /show weekly budget/i }));
    expect(screen.queryByText('Carried-over debt')).not.toBeInTheDocument();
  });

  it('shows the carryover row when debt is carried forward', async () => {
    vi.mocked(usageApi.fetchWeeklyBudget).mockResolvedValue({
      ...enabled,
      carryover_debt_usd: 2.5,
      remaining_usd: 12.5,
      remaining_fraction: 0.625,
    });
    const user = userEvent.setup();
    render(<WeeklyBudgetIndicator />);
    await screen.findByText(/credits used/);
    await user.click(screen.getByRole('button', { name: /show weekly budget/i }));
    expect(screen.getByText('Carried-over debt')).toBeInTheDocument();
    expect(screen.getByText(/−250/)).toBeInTheDocument();  // $2.50 → 250 credits
  });
});
