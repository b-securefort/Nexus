import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ApprovalCard } from '../components/ApprovalCard';
import type { ApprovalInfo } from '../types';

vi.mock('../api/client', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../api/client';

const mockApproval: ApprovalInfo = {
  approval_id: 'ap-test-1',
  tool_name: 'execute_script',
  args: { path: 'list-resources.ps1', reason: 'List directory contents' },
  reason: 'Need to list directory contents',
  risk_level: 'safe',
  risk_description: 'Lists resources in the current directory',
};

describe('ApprovalCard', () => {
  it('renders tool name', () => {
    render(<ApprovalCard approval={mockApproval} onAction={() => {}} />);
    expect(screen.getByText(/execute_script/)).toBeInTheDocument();
  });

  it('renders the review description, not the generator reason', () => {
    render(<ApprovalCard approval={mockApproval} onAction={() => {}} />);
    expect(screen.getByText(/Lists resources in the current directory/)).toBeInTheDocument();
    // the generator's `reason` is intentionally not shown on the card
    expect(screen.queryByText(/Need to list directory contents/)).not.toBeInTheDocument();
  });

  it('renders approve and deny buttons', () => {
    render(<ApprovalCard approval={mockApproval} onAction={() => {}} />);
    expect(screen.getByText(/Approve/i)).toBeInTheDocument();
    expect(screen.getByText(/Deny/i)).toBeInTheDocument();
  });

  it('calls onAction with approve (non-destructive: no double-confirm)', async () => {
    const user = userEvent.setup();
    const handler = vi.fn();
    render(<ApprovalCard approval={mockApproval} onAction={handler} />);
    await user.click(screen.getByText(/Approve/i));
    expect(handler).toHaveBeenCalledWith('approve');
  });

  it('calls onAction with deny', async () => {
    const user = userEvent.setup();
    const handler = vi.fn();
    render(<ApprovalCard approval={mockApproval} onAction={handler} />);
    await user.click(screen.getByText(/Deny/i));
    expect(handler).toHaveBeenCalledWith('deny');
  });

  it('displays the command', () => {
    render(<ApprovalCard approval={mockApproval} onAction={() => {}} />);
    expect(screen.getByText(/list-resources\.ps1/)).toBeInTheDocument();
  });

  it('disables Approve while risk is being assessed', () => {
    const pending: ApprovalInfo = { ...mockApproval, risk_level: 'pending', risk_description: null };
    render(<ApprovalCard approval={pending} onAction={() => {}} />);
    expect(screen.getByText(/Assessing risk/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Approve/i })).toBeDisabled();
  });

  it('shows the AI-generated disclaimer once a verdict resolves', () => {
    render(<ApprovalCard approval={mockApproval} onAction={() => {}} />);
    expect(screen.getByText(/AI-generated and may be inaccurate/i)).toBeInTheDocument();
  });

  it('requires a second confirmation for destructive commands', async () => {
    const user = userEvent.setup();
    const handler = vi.fn();
    const destructive: ApprovalInfo = {
      ...mockApproval,
      risk_level: 'destructive',
      risk_description: 'Deletes the resource group and all its resources',
    };
    render(<ApprovalCard approval={destructive} onAction={handler} />);

    // first click arms the confirmation but does NOT approve
    await user.click(screen.getByRole('button', { name: /Approve/i }));
    expect(handler).not.toHaveBeenCalled();
    expect(screen.getByText(/Run it anyway/i)).toBeInTheDocument();

    // second click confirms
    await user.click(screen.getByRole('button', { name: /Yes, run it/i }));
    expect(handler).toHaveBeenCalledWith('approve');
  });

  it('cancel during destructive confirm does not deny', async () => {
    const user = userEvent.setup();
    const handler = vi.fn();
    const destructive: ApprovalInfo = { ...mockApproval, risk_level: 'destructive' };
    render(<ApprovalCard approval={destructive} onAction={handler} />);

    await user.click(screen.getByRole('button', { name: /Approve/i }));
    await user.click(screen.getByRole('button', { name: /Cancel/i }));
    expect(handler).not.toHaveBeenCalled();
    // back to the normal Approve/Deny state
    expect(screen.getByRole('button', { name: /Approve/i })).toBeInTheDocument();
  });

  // ── Backend-rendered command + download (§5 2026-06-12) ──────────────────────

  it('shows the backend rendered_command (real payload, not a reconstructed pointer)', () => {
    const withRender: ApprovalInfo = {
      ...mockApproval,
      rendered_command: 'execute script list-resources.ps1:\nGet-ChildItem -Recurse',
    };
    render(<ApprovalCard approval={withRender} onAction={() => {}} />);
    expect(screen.getByText(/Get-ChildItem -Recurse/)).toBeInTheDocument();
  });

  it('falls back to local formatCommand when rendered_command is absent', () => {
    // mockApproval carries no rendered_command → path-based reconstruction
    render(<ApprovalCard approval={mockApproval} onAction={() => {}} />);
    expect(screen.getByText(/list-resources\.ps1/)).toBeInTheDocument();
  });

  it('shows a download button only when the command is truncated', () => {
    const { rerender } = render(<ApprovalCard approval={mockApproval} onAction={() => {}} />);
    expect(screen.queryByText(/download full command/i)).not.toBeInTheDocument();
    rerender(
      <ApprovalCard approval={{ ...mockApproval, command_truncated: true }} onAction={() => {}} />,
    );
    expect(screen.getByText(/download full command/i)).toBeInTheDocument();
  });

  it('download button fetches the command endpoint through apiFetch (auth)', async () => {
    const user = userEvent.setup();
    (apiFetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      blob: async () => new Blob(['full command']),
    });
    URL.createObjectURL = vi.fn(() => 'blob:mock');
    URL.revokeObjectURL = vi.fn();
    render(<ApprovalCard approval={{ ...mockApproval, command_truncated: true }} onAction={() => {}} />);
    await user.click(screen.getByText(/download full command/i));
    expect(apiFetch).toHaveBeenCalledWith('/api/approvals/ap-test-1/command');
  });
});
