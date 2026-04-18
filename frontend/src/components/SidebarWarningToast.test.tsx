import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

const navigate = vi.fn()

vi.mock('@heroicons/react/24/outline', () => {
  const makeIcon = (name: string) => (props: { className?: string }) => <svg data-testid={name} {...props} />
  return {
    XMarkIcon: makeIcon('XMarkIcon'),
    ExclamationTriangleIcon: makeIcon('ExclamationTriangleIcon'),
    ExclamationCircleIcon: makeIcon('ExclamationCircleIcon'),
  }
})

vi.mock('./LocalizedLink', () => ({
  useLocalizedNavigate: () => navigate,
}))

import SidebarWarningToast from './SidebarWarningToast'

describe('SidebarWarningToast', () => {
  it('renders nothing without warnings', () => {
    const { container } = render(<SidebarWarningToast warnings={[]} onDismiss={vi.fn()} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('handles dismiss, navigation, actions, and keyboard activation', () => {
    const onDismiss = vi.fn()
    const action = vi.fn()

    render(
      <SidebarWarningToast
        warnings={[
          {
            id: 'warn-1',
            level: 'warning',
            message: 'Storage almost full',
            path: '/settings/storage',
            actionLabel: 'Review',
          },
          {
            id: 'warn-2',
            level: 'critical',
            message: 'Sync failed',
            action,
            actionLabel: 'Retry sync',
          },
        ]}
        onDismiss={onDismiss}
      />
    )

    expect(screen.getByText('Storage almost full')).toBeInTheDocument()
    expect(screen.getByText('Sync failed')).toBeInTheDocument()
    expect(screen.getByTestId('ExclamationTriangleIcon')).toBeInTheDocument()
    expect(screen.getByTestId('ExclamationCircleIcon')).toBeInTheDocument()

    fireEvent.click(screen.getAllByLabelText('Dismiss warning')[0])
    expect(onDismiss).toHaveBeenCalledWith('warn-1')

    fireEvent.click(screen.getByRole('button', { name: /Storage almost full/i }))
    expect(navigate).toHaveBeenCalledWith('/settings/storage')

    const criticalWarning = screen.getByRole('button', { name: /Sync failed/i })
    fireEvent.keyDown(criticalWarning, { key: 'Enter' })
    fireEvent.keyDown(criticalWarning, { key: ' ' })
    expect(action).toHaveBeenCalledTimes(2)
    expect(screen.getByText('Retry sync')).toBeInTheDocument()
  })
})
