import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ApprovalCard } from '../components/ApprovalCard';
import type { ApprovalInfo } from '../types';

const mockApproval: ApprovalInfo = {
  approval_id: 'ap-test-1',
  tool_name: 'run_shell',
  args: { command: 'ls -la', reason: 'List directory contents' },
  reason: 'Need to list directory contents',
};

describe('ApprovalCard', () => {
  it('renders tool name', () => {
    render(<ApprovalCard approval={mockApproval} onAction={() => {}} />);
    expect(screen.getByText(/run_shell/)).toBeInTheDocument();
  });

  it('renders reason', () => {
    render(<ApprovalCard approval={mockApproval} onAction={() => {}} />);
    expect(screen.getByText(/Need to list directory contents/)).toBeInTheDocument();
  });

  it('renders approve and deny buttons', () => {
    render(<ApprovalCard approval={mockApproval} onAction={() => {}} />);
    expect(screen.getByText(/Approve/i)).toBeInTheDocument();
    expect(screen.getByText(/Deny/i)).toBeInTheDocument();
  });

  it('calls onAction with approve', async () => {
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

  it('displays args as JSON', () => {
    render(<ApprovalCard approval={mockApproval} onAction={() => {}} />);
    expect(screen.getByText(/ls -la/)).toBeInTheDocument();
  });
});
