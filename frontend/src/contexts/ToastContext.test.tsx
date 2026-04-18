import { act, fireEvent, render, screen } from '@testing-library/react'
import { vi } from 'vitest'

import { ToastProvider, useToast } from './ToastContext'

function ToastHarness() {
  const { success, error, warning, info, clearToasts } = useToast()

  return (
    <div>
      <button onClick={() => success('Saved', { description: 'All changes were stored.' })}>success</button>
      <button onClick={() => error('Failed', { duration: 0 })}>error</button>
      <button onClick={() => warning('Heads up', { action: { label: 'Retry', onClick: vi.fn() } })}>warning</button>
      <button onClick={() => info('FYI', { duration: 50 })}>info</button>
      <button onClick={() => clearToasts()}>clear</button>
    </div>
  )
}

describe('ToastContext', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.runOnlyPendingTimers()
    vi.useRealTimers()
  })

  it('throws when used outside the provider', () => {
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})

    expect(() => render(<ToastHarness />)).toThrow('useToast must be used within ToastProvider')

    consoleErrorSpy.mockRestore()
  })

  it('shows and dismisses toasts', () => {
    render(
      <ToastProvider>
        <ToastHarness />
      </ToastProvider>
    )

    fireEvent.click(screen.getByRole('button', { name: 'success' }))

    expect(screen.getByRole('alert')).toHaveTextContent('Saved')
    expect(screen.getByText('All changes were stored.')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }))

    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('runs toast actions and removes the toast', () => {
    const actionSpy = vi.fn()

    function ActionHarness() {
      const { warning } = useToast()
      return (
        <button
          onClick={() =>
            warning('Heads up', {
              action: { label: 'Retry', onClick: actionSpy },
              duration: 0,
            })
          }
        >
          add warning
        </button>
      )
    }

    render(
      <ToastProvider>
        <ActionHarness />
      </ToastProvider>
    )

    fireEvent.click(screen.getByRole('button', { name: 'add warning' }))
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))

    expect(actionSpy).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('auto-removes timed toasts and clears all toasts', () => {
    render(
      <ToastProvider>
        <ToastHarness />
      </ToastProvider>
    )

    fireEvent.click(screen.getByRole('button', { name: 'info' }))
    expect(screen.getByRole('alert')).toHaveTextContent('FYI')

    act(() => {
      vi.advanceTimersByTime(60)
    })
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'error' }))
    expect(screen.getByRole('alert')).toHaveTextContent('Failed')

    fireEvent.click(screen.getByRole('button', { name: 'clear' }))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
