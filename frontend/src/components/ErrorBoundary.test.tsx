import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@heroicons/react/24/outline', () => {
  const makeIcon = (name: string) => (props: { className?: string }) => <svg data-testid={name} {...props} />
  return {
    ExclamationTriangleIcon: makeIcon('ExclamationTriangleIcon'),
    ArrowPathIcon: makeIcon('ArrowPathIcon'),
    HomeIcon: makeIcon('HomeIcon'),
    BugAntIcon: makeIcon('BugAntIcon'),
  }
})

import ErrorBoundary, { ErrorFallback, NetworkError } from './ErrorBoundary'

function Boom() {
  throw new Error('boom failure')
}

describe('ErrorBoundary', () => {
  const originalLocation = window.location

  beforeEach(() => {
    const reload = vi.fn()
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: {
        ...originalLocation,
        href: 'http://localhost/test',
        reload,
      },
    })
  })

  afterEach(() => {
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: originalLocation,
    })
    vi.restoreAllMocks()
  })

  it('renders children when no error occurs', () => {
    render(
      <ErrorBoundary>
        <div>Safe content</div>
      </ErrorBoundary>
    )

    expect(screen.getByText('Safe content')).toBeInTheDocument()
  })

  it('renders the fallback UI, reports errors, and supports reset/recovery actions', () => {
    const onError = vi.fn()
    const onReset = vi.fn()
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})

    render(
      <ErrorBoundary onError={onError} onReset={onReset}>
        <Boom />
      </ErrorBoundary>
    )

    expect(screen.getByText('Something went wrong')).toBeInTheDocument()
    expect(screen.getByText('boom failure')).toBeInTheDocument()
    expect(onError).toHaveBeenCalled()
    expect(consoleError).toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Reload Page' }))
    expect(window.location.reload).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByRole('button', { name: 'Go Home' }))
    expect(window.location.href).toBe('/')

    fireEvent.click(screen.getByRole('button', { name: 'Try Again' }))
    expect(onReset).toHaveBeenCalledTimes(1)
  })

  it('renders a custom fallback when provided', () => {
    render(
      <ErrorBoundary fallback={<div>Custom fallback</div>}>
        <Boom />
      </ErrorBoundary>
    )

    expect(screen.getByText('Custom fallback')).toBeInTheDocument()
    expect(screen.queryByText('Something went wrong')).not.toBeInTheDocument()
  })
})

describe('ErrorBoundary helper components', () => {
  it('renders error fallback details and retry button', () => {
    const onRetry = vi.fn()
    render(
      <ErrorFallback
        error={new Error('Retry me')}
        onRetry={onRetry}
        message="Custom message"
      />
    )

    expect(screen.getByText('Custom message')).toBeInTheDocument()
    expect(screen.getByText('Retry me')).toBeInTheDocument()
    expect(screen.getByTestId('BugAntIcon')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })

  it('renders the network error variant', () => {
    const onRetry = vi.fn()
    render(<NetworkError onRetry={onRetry} />)

    expect(screen.getByText('Connection Error')).toBeInTheDocument()
    expect(screen.getByText(/Unable to connect/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })
})
