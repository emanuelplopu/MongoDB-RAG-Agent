import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@heroicons/react/24/outline', () => {
  const makeIcon = (name: string) => (props: { className?: string }) => <svg data-testid={name} {...props} />
  return {
    WifiIcon: makeIcon('WifiIcon'),
    ExclamationTriangleIcon: makeIcon('ExclamationTriangleIcon'),
    ArrowPathIcon: makeIcon('ArrowPathIcon'),
  }
})

import ConnectionStatus, { ConnectionDot } from './ConnectionStatus'

describe('ConnectionStatus', () => {
  beforeEach(() => {
    vi.useRealTimers()
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('shows a connected state with latency details and fixed positioning', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValue({ ok: true } as Response)
    const nowSpy = vi.spyOn(Date, 'now')
    nowSpy.mockReturnValueOnce(1000).mockReturnValueOnce(1200)

    render(<ConnectionStatus showText size="md" position="fixed" />)

    await waitFor(() => {
      expect(screen.getByText('Connected')).toBeInTheDocument()
    })

    expect(screen.getByText('(200ms)')).toBeInTheDocument()
    expect(screen.getByTestId('WifiIcon')).toBeInTheDocument()
    expect(screen.getByText('Connected').closest('div')?.parentElement?.className).toContain('fixed')
  })

  it('shows slow and disconnected states based on the health check', async () => {
    const fetchMock = vi.mocked(fetch)
    const nowSpy = vi.spyOn(Date, 'now')
    nowSpy.mockReturnValueOnce(1000).mockReturnValueOnce(4005)
    fetchMock.mockResolvedValueOnce({ ok: true } as Response)

    render(<ConnectionStatus showText healthCheckUrl="/healthz" />)

    await waitFor(() => {
      expect(screen.getByText('Slow connection')).toBeInTheDocument()
    })

    fetchMock.mockReset()
    fetchMock.mockResolvedValueOnce({ ok: false } as Response)
    render(<ConnectionStatus showText healthCheckUrl="/healthz-fail" />)

    await waitFor(() => {
      expect(screen.getAllByText('Disconnected')[0]).toBeInTheDocument()
    })
  })

  it('responds to online and offline browser events', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValue({ ok: true } as Response)

    render(<ConnectionStatus showText />)

    await waitFor(() => {
      expect(screen.getByText('Connected')).toBeInTheDocument()
    })

    fireEvent(window, new Event('offline'))
    expect(screen.getByText('Disconnected')).toBeInTheDocument()

    fireEvent(window, new Event('online'))
    await waitFor(() => {
      expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(2)
    })
  })
})

describe('ConnectionDot', () => {
  it('tracks navigator connectivity changes', () => {
    Object.defineProperty(window.navigator, 'onLine', {
      configurable: true,
      value: true,
    })

    const { rerender } = render(<ConnectionDot size="xs" className="dot-shell" />)

    let dot = document.querySelector('.dot-shell')
    expect(dot).toHaveAttribute('title', 'Online')
    expect(dot?.className).toContain('bg-green-500')

    Object.defineProperty(window.navigator, 'onLine', {
      configurable: true,
      value: false,
    })
    fireEvent(window, new Event('offline'))
    rerender(<ConnectionDot size="md" className="dot-shell" />)

    dot = document.querySelector('.dot-shell')
    expect(dot).toHaveAttribute('title', 'Offline')
    expect(dot?.className).toContain('bg-red-500')
    expect(dot?.className).toContain('w-3')
  })
})
