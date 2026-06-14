import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { UsersAdminPage } from '../pages/UsersAdminPage';
import * as usersApi from '../api/users';
import type { UserUsageRow } from '../api/users';

vi.mock('../api/users');

const rowA: UserUsageRow = {
  oid: 'a', email: 'a@x.com', display_name: 'Alice',
  cap_credits: null, effective_cap_credits: 2000,
  spent_this_week_credits: 150, remaining_credits: 1850,
  week_resets_at: '2026-06-15T00:00:00+00:00',
};
const rowB: UserUsageRow = {
  oid: 'b', email: 'b@x.com', display_name: 'Bob',
  cap_credits: 3000, effective_cap_credits: 3000,
  spent_this_week_credits: 500, remaining_credits: 2500,
  week_resets_at: '2026-06-15T00:00:00+00:00',
};

function renderPage() {
  return render(
    <MemoryRouter>
      <UsersAdminPage />
    </MemoryRouter>,
  );
}

describe('UsersAdminPage', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(usersApi.listUsers).mockResolvedValue({
      items: [rowA, rowB],
      default_cap_credits: 2000,
    });
  });

  it('lists users with their weekly spend and remaining', async () => {
    renderPage();
    expect(await screen.findByText('Alice')).toBeInTheDocument();
    expect(screen.getByText('Bob')).toBeInTheDocument();
    expect(screen.getByText('1,850')).toBeInTheDocument();
    expect(screen.getByText('2,500')).toBeInTheDocument();
  });

  it('saves a new cap in credits', async () => {
    vi.mocked(usersApi.updateUserCap).mockResolvedValue({
      ...rowA, cap_credits: 5000, effective_cap_credits: 5000, remaining_credits: 4850,
    });
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Alice');

    await user.type(screen.getByLabelText(/weekly cap for a@x.com/i), '5000');
    await user.click(screen.getAllByRole('button', { name: 'Save' })[0]);

    await waitFor(() => expect(usersApi.updateUserCap).toHaveBeenCalledWith('a', 5000));
  });

  it('clears a cap back to default', async () => {
    vi.mocked(usersApi.updateUserCap).mockResolvedValue({
      ...rowB, cap_credits: null, effective_cap_credits: 2000,
    });
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Bob');

    // Alice's Clear is disabled (no override); Bob's (index 1) is enabled.
    await user.click(screen.getAllByRole('button', { name: 'Clear' })[1]);

    await waitFor(() => expect(usersApi.updateUserCap).toHaveBeenCalledWith('b', null));
  });

  it('shows an error when the list fails (e.g. non-architect)', async () => {
    vi.mocked(usersApi.listUsers).mockRejectedValue(
      new Error('Architect role required to manage user caps.'),
    );
    renderPage();
    expect(await screen.findByText(/architect role required/i)).toBeInTheDocument();
  });
});
